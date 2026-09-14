"""Six high-level stdio MCP tools; shared Python API, no host SDK in Core."""

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .errors import VisualError, require
from .project import VisualProject

server = FastMCP("EAS Visual Experimental")


def allowed(path):
    roots = [
        Path(p).resolve() for p in os.environ.get("EAS_VISUAL_ROOTS", str(Path.cwd())).split(os.pathsep) if p
    ]
    target = Path(path).resolve()
    require(
        any(target.is_relative_to(root) for root in roots),
        "PATH_NOT_ALLOWED",
        "Path outside EAS_VISUAL_ROOTS",
    )
    return str(target)


def invoke(function):
    try:
        return {"ok": True, "result": function()}
    except VisualError as exc:
        return {"ok": False, "error": {"code": exc.code, "message": str(exc)}}
    except (ValueError, TypeError, KeyError, OSError) as exc:
        return {"ok": False, "error": {"code": "INVALID_INPUT_OR_IO", "message": str(exc)}}


@server.tool()
def inspect_visual(project: str) -> dict:
    """Verify integrity, inspect semantic objects/history and obtain current PNG/SVG paths."""
    return invoke(lambda: VisualProject(allowed(project)).inspect())


@server.tool()
def create_visual_project(input_image: str, output: str, colors: int = 64) -> dict:
    """Create an immutable pixel-faithful baseline; refuses existing output. Inkscape required."""
    return invoke(lambda: VisualProject.create(allowed(input_image), allowed(output), colors))


@server.tool()
def promote_object(
    project: str,
    label: str,
    bbox: list[int] | None = None,
    polygon: list[list[int]] | None = None,
    mask_path: str | None = None,
) -> dict:
    """Promote just one requested object using exactly one explicit bbox, polygon or binary mask."""
    return invoke(
        lambda: VisualProject(allowed(project)).promote(
            label, bbox, polygon, allowed(mask_path) if mask_path else None
        )
    )


@server.tool()
def edit_visual(project: str, operation: str, options: dict) -> dict:
    """operation: move, prepare, edit or replay. Uses the same Python API options as CLI.

    move: object_id, dx, dy, repair=donor, donor_bbox. prepare: object_id,
    instruction, context_bbox, margin, purpose=edit|repair. Host calls its image
    model on ONLY returned context_file. edit: object_id, provider=external-patch,
    request_id, patch_path, metadata, normalize=false. mock is explicitly synthetic.
    Failed validation never advances history. No automatic segmentation.
    """

    def execute():
        require(operation in ("move", "prepare", "edit", "replay"), "INVALID_OPERATION", operation)
        arguments = dict(options)
        if arguments.get("patch_path"):
            arguments["patch_path"] = allowed(arguments["patch_path"])
        return getattr(VisualProject(allowed(project)), operation)(**arguments)

    return invoke(execute)


@server.tool()
def render_visual(project: str, output: str) -> dict:
    """Export current verified PNG or SVG outside the project; never overwrite existing files."""
    return invoke(lambda: VisualProject(allowed(project)).export(allowed(output)))


@server.tool()
def undo_visual(project: str, operation: str = "undo", revision: str | None = None) -> dict:
    """Reversible navigation: undo, redo, reset to baseline, or checkout a saved edit ID."""
    return invoke(lambda: VisualProject(allowed(project)).navigate(operation, revision))


def main():
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
