"""JSON-first CLI. --json is an explicit alias for the default output contract."""

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .errors import VisualError
from .project import VisualProject


def emit(value):
    # ASCII JSON is valid UTF-8 even on Windows consoles configured for a legacy code page.
    print(json.dumps(value, ensure_ascii=True, allow_nan=False))


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise VisualError("ARGUMENT_ERROR", message)


def parser():
    p = Parser(prog="eas-visual", description="Give your agent editable vision. JSON is the default output.")
    p.add_argument("--version", action="version", version=f"EAS Visual {__version__} Experimental")
    p.add_argument("--json", action="store_true", help="Explicit JSON (already the default)")
    subs = p.add_subparsers(dest="command", required=True, parser_class=Parser)
    for name in (
        "init",
        "inspect",
        "promote",
        "move",
        "prepare",
        "edit",
        "undo",
        "redo",
        "reset",
        "checkout",
        "render",
        "diff",
        "replay",
        "providers",
    ):
        q = subs.add_parser(name)
        q.add_argument("--json", action="store_true", help="Explicit JSON (already the default)")
        q.add_argument("--inkscape", help="Inkscape executable; alternatively EAS_VISUAL_INKSCAPE")
        if name == "providers":
            continue
        if name == "init":
            q.add_argument("input")
            q.add_argument("-o", "--output", required=True)
            q.add_argument("--colors", type=int, default=64)
            continue
        q.add_argument("project")
        if name == "promote":
            q.add_argument("--label", required=True)
            s = q.add_mutually_exclusive_group(required=True)
            s.add_argument("--bbox", type=json.loads, help="JSON [x0,y0,x1,y1), integer pixels")
            s.add_argument("--polygon", type=json.loads, help="JSON [[x,y],...]")
            s.add_argument("--mask", dest="mask_path", help="Full-size binary grayscale PNG")
        if name in ("move", "prepare", "edit"):
            q.add_argument("--object", dest="object_id", required=True)
            q.add_argument("--margin", type=int, default=0)
        if name in ("prepare", "edit"):
            q.add_argument("--instruction", default="")
            q.add_argument("--context", dest="context_bbox", type=json.loads)
        if name == "prepare":
            q.add_argument("--purpose", choices=("edit", "repair"), default="edit")
        if name == "move":
            q.add_argument("--dx", type=int, required=True)
            q.add_argument("--dy", type=int, required=True)
            q.add_argument("--repair", choices=("donor", "generative", "none"), default="donor")
            q.add_argument("--donor", dest="donor_bbox", type=json.loads)
        if name in ("move", "edit"):
            q.add_argument("--request", dest="request_id")
            q.add_argument("--patch", dest="patch_path")
            q.add_argument(
                "--metadata", help="JSON file with real provider metadata; unknown model/seed can be null"
            )
            q.add_argument("--normalize", action="store_true", help="Explicitly resize returned patch only")
        if name == "edit":
            q.add_argument("--provider", choices=("mock", "external-patch"), default="mock")
        if name == "render":
            q.add_argument("-o", "--output", required=True)
        if name == "checkout":
            q.add_argument("revision")
    return p


def dispatch(args):
    values = vars(args).copy()
    command = values.pop("command")
    values.pop("json", None)
    inkscape = values.pop("inkscape", None)
    if command == "providers":
        return {
            "image_edit": ["mock", "external-patch"],
            "background_repair": ["donor", "none", "generative"],
            "external_patch_workflow": "prepare crop -> host calls image model -> submit patch and metadata",
            "automatic_localization": False,
        }
    if command == "init":
        return VisualProject.create(values["input"], values["output"], values["colors"], inkscape)
    project = VisualProject(values.pop("project"), inkscape)
    if "metadata" in values:
        values["metadata"] = (
            json.loads(Path(values["metadata"]).read_text(encoding="utf-8")) if values["metadata"] else None
        )
    if command in ("undo", "redo", "reset", "checkout"):
        return project.navigate(command, **values)
    if command == "render":
        return project.export(**values)
    return getattr(project, command)(**values)


def main(argv=None):
    try:
        result = dispatch(parser().parse_args(argv))
        emit({"ok": True, "version": __version__, "result": result})
        return 0
    except VisualError as exc:
        emit({"ok": False, "version": __version__, "error": {"code": exc.code, "message": str(exc)}})
        return 2
    except (OSError, ValueError, TypeError, KeyError) as exc:
        emit(
            {
                "ok": False,
                "version": __version__,
                "error": {"code": "INVALID_INPUT_OR_IO", "message": str(exc)},
            }
        )
        return 2
    except Exception as exc:  # noqa: BLE001 -- CLI boundary must preserve the JSON error contract
        emit({"ok": False, "version": __version__, "error": {"code": "INTERNAL_ERROR", "message": str(exc)}})
        return 1


if __name__ == "__main__":
    sys.exit(main())
