"""CLI for inspecting, reviewing and compiling a Minecraft semantic scene."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .minecraft_structure import compile_plan, inspect, parse_scene, resolve, statistics
from .minecraft_structure_view import render_html
from .plan_ops import FileEditError


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scene", type=Path, help="editable scene JSON")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("stats")
    point = commands.add_parser("inspect")
    for axis in ("x", "y", "z"):
        point.add_argument(axis, type=int)
    review = commands.add_parser("review")
    review.add_argument("output", type=Path, help="standalone offline HTML")
    plan = commands.add_parser("compile")
    plan.add_argument("world", type=Path, help="closed Java 26.3 world directory")
    plan.add_argument("output", type=Path, help="derived EAFS plan JSON")
    args = parser.parse_args()
    try:
        result = _execute(args)
    except (ValueError, TypeError, UnicodeError, FileEditError) as exc:
        code = exc.code if isinstance(exc, FileEditError) else "INVALID_SCENE"
        print(json.dumps({"ok": False, "code": code, "message": str(exc)}, ensure_ascii=False))
        raise SystemExit(2) from exc
    except OSError as exc:
        print(json.dumps({"ok": False, "code": "IO_ERROR", "message": str(exc)}, ensure_ascii=False))
        raise SystemExit(4) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _execute(args: argparse.Namespace) -> dict:
    scene_bytes = args.scene.read_bytes()
    digest = hashlib.sha256(scene_bytes).hexdigest()
    resolved = resolve(parse_scene(json.loads(scene_bytes.decode("utf-8"))))
    if args.command == "stats":
        result = statistics(resolved)
    elif args.command == "inspect":
        result = inspect(resolved, args.x, args.y, args.z)
    elif args.command == "review":
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(render_html(resolved, scene_sha256=digest), encoding="utf-8")
        result = {"review": str(args.output), "final_blocks": len(resolved.voxels)}
    else:
        payload = compile_plan(resolved, args.world)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        result = {"plan": str(args.output), "base_sha256": payload["base"]["sha256"],
                  "final_blocks": len(resolved.voxels), "runs": len(payload["steps"][0]["runs"])}
    result["scene_sha256"] = digest
    return result


if __name__ == "__main__":
    main()
