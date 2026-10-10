"""Local stdio MCP adapter for sfe/0.1 file profiles and EAS-HMI plans."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from filelock import Timeout
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent
from pydantic import ValidationError

from eas_hmi.integration import FORMAT as EAS_FORMAT
from eas_hmi.integration import PROTOCOL as EAS_PROTOCOL
from eas_hmi.integration import (
    EngineeringError,
    Query,
    Store,
    apply_steps,
    canonical_hash,
    canonical_json,
    parse_plan,
)
from eas_hmi.integration import plan_contract as eas_plan_contract

from .plan_ops import FileEditError
from .service import apply_data, contract_data, inspect_data, preview_data, status_data

server = FastMCP("EAFS Structured File Editing")


def allowed(path: str) -> Path:
    """Resolve paths against the server's configured local workspace roots."""
    configured = os.environ.get("EAFS_MCP_ROOTS", str(Path.cwd()))
    roots = [Path(root).resolve() for root in configured.split(os.pathsep) if root]
    target = Path(path).resolve()
    if not any(target.is_relative_to(root) for root in roots):
        raise FileEditError("PATH_NOT_ALLOWED", "Path outside EAFS_MCP_ROOTS")
    return target


def _plan_bytes(plan: dict[str, Any]) -> bytes:
    try:
        return json.dumps(plan, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise FileEditError("INVALID_PLAN", "Plan must contain JSON-compatible values") from exc


def _result(action: Callable[[], dict]) -> CallToolResult:
    try:
        value = action()
    except (FileEditError, EngineeringError) as exc:
        value = exc.as_dict()
    except Timeout:
        value = EngineeringError("PROJECT_BUSY", "Project lock timed out").as_dict()
    except ValidationError as exc:
        value = EngineeringError(
            "VALIDATION_FAILED",
            "Schema validation failed",
            exc.errors(include_url=False, include_context=False, include_input=False),
        ).as_dict()
    except (OSError, UnicodeError) as exc:
        value = {"ok": False, "code": "IO_ERROR", "message": str(exc)}
    except (ValueError, TypeError) as exc:
        value = {"ok": False, "code": "INVALID_INPUT", "message": str(exc)}
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(value, ensure_ascii=False, allow_nan=False))],
        structuredContent=value,
        isError=not value.get("ok", False),
    )


@server.tool()
def file_contract(format_name: str | None = None) -> CallToolResult:
    """List file and directory profiles, or request one full instruction set."""

    def action():
        catalog = contract_data()
        if format_name is None:
            return {
                **catalog,
                "formats": {
                    name: {key: value for key, value in detail.items() if key != "instruction_set"}
                    for name, detail in catalog["formats"].items()
                },
            }
        if format_name not in catalog["formats"]:
            raise FileEditError("UNSUPPORTED_FORMAT", f"Unknown file profile: {format_name}")
        return {
            "ok": True,
            "protocol": catalog["protocol"],
            "format": format_name,
            **catalog["formats"][format_name],
        }

    return _result(action)


@server.tool()
def file_status(file: str) -> CallToolResult:
    """Read a local source's sfe/0.1 format, fingerprint and structure summary."""
    return _result(lambda: status_data(allowed(file)))


@server.tool()
def file_inspect(
    file: str,
    pointer: str = "",
    key_column: str | None = None,
    key: str | None = None,
    heading: str | None = None,
    object_id: str | None = None,
    limit: int = 20,
) -> CallToolResult:
    """Inspect a format-specific pointer, CSV key, Markdown heading or SVG id."""
    return _result(
        lambda: inspect_data(
            allowed(file),
            pointer=pointer,
            key_column=key_column,
            key=key,
            heading=heading,
            object_id=object_id,
            limit=limit,
        )
    )


@server.tool()
def file_preview(file: str, plan: dict[str, Any], limit: int = 200) -> CallToolResult:
    """Validate every step of an inline sfe/0.1 plan and return a diff without writing."""
    return _result(lambda: preview_data(allowed(file), _plan_bytes(plan), limit=limit))


@server.tool()
def file_apply(file: str, plan: dict[str, Any], output: str) -> CallToolResult:
    """Apply an inline plan to a new allowed path; the input file is preserved."""
    return _result(lambda: apply_data(allowed(file), _plan_bytes(plan), allowed(output)))


@server.tool()
def eas_contract() -> CallToolResult:
    """Discover the EAS-HMI sfe/0.1 plan JSON Schema and engineering operations."""
    return _result(eas_plan_contract)


@server.tool()
def eas_status(project: str) -> CallToolResult:
    """Read an EAS-HMI project's revision, canonical hash and object counts."""

    def action():
        current = Store(allowed(project)).load()
        return {
            "ok": True,
            "protocol": EAS_PROTOCOL,
            "format": EAS_FORMAT,
            "project_id": current.id,
            "revision": current.revision,
            "canonical_hash": canonical_hash(current),
            "pages": len(current.pages),
            "nodes": len(current.all_nodes()),
            "points": len(current.points),
        }

    return _result(action)


@server.tool()
def eas_query(
    project: str,
    kind: str | None = None,
    page: str | None = None,
    equipment: str | None = None,
    limit: int = 100,
) -> CallToolResult:
    """Find EAS-HMI node IDs using optional kind, page and equipment filters."""

    def action():
        if not 1 <= limit <= 200:
            raise EngineeringError("INVALID_LIMIT", "query limit must be 1..200")
        current = Store(allowed(project)).load()
        nodes = Query(current).select(kind=kind, page=page, equipment=equipment)
        return {
            "ok": True,
            "protocol": EAS_PROTOCOL,
            "format": EAS_FORMAT,
            "revision": current.revision,
            "canonical_hash": canonical_hash(current),
            "count": len(nodes),
            "nodes": [node.model_dump(mode="json") for node in nodes[:limit]],
            "truncated": len(nodes) > limit,
        }

    return _result(action)


@server.tool()
def eas_inspect(project: str, object_id: str) -> CallToolResult:
    """Inspect an EAS-HMI node, equipment or point by its stable ID."""

    def action():
        current = Store(allowed(project)).load()
        return {
            "ok": True,
            "protocol": EAS_PROTOCOL,
            "format": EAS_FORMAT,
            "revision": current.revision,
            "canonical_hash": canonical_hash(current),
            **Query(current).inspect(object_id),
        }

    return _result(action)


@server.tool()
def eas_context(project: str, object_id: str, depth: int = 1, limit: int = 30) -> CallToolResult:
    """Read bounded related EAS-HMI objects around one node, equipment or point."""

    def action():
        current = Store(allowed(project)).load()
        return {
            "ok": True,
            "protocol": EAS_PROTOCOL,
            "format": EAS_FORMAT,
            "revision": current.revision,
            "canonical_hash": canonical_hash(current),
            **Query(current).context(object_id, depth=depth, limit=limit),
        }

    return _result(action)


@server.tool()
def eas_preview(project: str, plan: dict[str, Any]) -> CallToolResult:
    """Validate an inline EAS-HMI plan in one dry-run transaction; HEAD is unchanged."""

    def action():
        parsed = parse_plan(_plan_bytes(plan))
        result = Store(allowed(project)).preview(
            lambda current: apply_steps(current, parsed), expected_revision=parsed.base.revision
        )
        return {**result, "protocol": EAS_PROTOCOL, "format": EAS_FORMAT, "dry_run": True}

    return _result(action)


@server.tool()
def eas_apply(project: str, plan: dict[str, Any]) -> CallToolResult:
    """Commit every step of an inline EAS-HMI plan as one audited revision."""

    def action():
        parsed = parse_plan(_plan_bytes(plan))
        result = Store(allowed(project)).transaction(
            canonical_json(["apply-plan", parsed.model_dump(mode="json")]),
            lambda current: apply_steps(current, parsed),
            "agent",
            parsed.base.revision,
        )
        return {**result, "protocol": EAS_PROTOCOL, "format": EAS_FORMAT}

    return _result(action)


@server.tool()
def eas_undo(project: str, expected_revision: int) -> CallToolResult:
    """Undo one EAS-HMI revision only when the expected current revision matches."""
    return _result(
        lambda: {
            **Store(allowed(project)).undo("agent", expected_revision),
            "protocol": EAS_PROTOCOL,
            "format": EAS_FORMAT,
        }
    )


def main():
    # Load native-backed profiles before the stdio transport takes over process streams.
    contract_data()
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
