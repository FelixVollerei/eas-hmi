"""Transport-neutral operations shared by the file CLI and MCP adapter."""

from __future__ import annotations

import difflib
import json
import os
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from . import minecraft_world
from .core import (
    BASE_CONTRACTS,
    FORMATS,
    PROTOCOL,
    _csv_rows,
    format_for,
    markdown_section,
    operations_for,
    parse_file,
    prepare,
    profile_for,
    sha256,
    summary,
)
from .plan_ops import FileEditError, json_get


def _publish_new(output: Path, data: bytes) -> str | None:
    """Publish complete bytes under a new name without replacing another writer's file."""
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(f".{output.name}.{uuid4().hex}.tmp")
    phase = "write"
    try:
        with temp.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        phase = "publish"
        os.link(temp, output)
    except OSError as exc:
        try:
            temp.unlink(missing_ok=True)
        except OSError as cleanup:
            exc.add_note(f"Temporary-file cleanup also failed: {cleanup}")
        if phase == "publish" and isinstance(exc, FileExistsError):
            raise FileEditError("OUTPUT_EXISTS", "Output must be a new path distinct from input") from exc
        raise
    try:
        temp.unlink()
    except OSError as exc:
        # The output link already exists. Report cleanup failure as a warning,
        # so callers do not mistake a committed edit for a rejected edit.
        return f"Output published; temporary-file cleanup failed: {exc}"
    return None


def contract_data() -> dict:
    formats = {}
    for fmt in dict.fromkeys(FORMATS.values()):
        profile = profile_for(fmt)
        formats[fmt] = {
            "extensions": sorted(ext for ext, name in FORMATS.items() if name == fmt),
            "operations": sorted(operations_for(fmt)),
        }
        if profile is not None:
            formats[fmt]["instruction_set"] = deepcopy(profile.CONTRACT)
        else:
            formats[fmt]["instruction_set"] = deepcopy(BASE_CONTRACTS[fmt])
    formats[minecraft_world.FORMAT] = {
        "extensions": [], "kind": "directory", "operations": sorted(minecraft_world.OPS),
        "instruction_set": deepcopy(minecraft_world.CONTRACT),
    }
    return {
        "ok": True,
        "protocol": PROTOCOL,
        "formats": formats,
        "workflow": ["status", "inspect", "preview", "apply"],
    }


def status_data(file: Path) -> dict:
    if file.is_dir():
        return minecraft_world.status_data(file)
    data = file.read_bytes()
    fmt = format_for(file)
    value = parse_file(fmt, data)
    return {
        "ok": True,
        "protocol": PROTOCOL,
        "format": fmt,
        "path": str(file.resolve()),
        "sha256": sha256(data),
        "summary": summary(fmt, value),
    }


def inspect_data(
    file: Path,
    *,
    pointer: str = "",
    key_column: str | None = None,
    key: str | None = None,
    heading: str | None = None,
    object_id: str | None = None,
    limit: int = 20,
) -> dict:
    if not 1 <= limit <= 1000:
        raise FileEditError("INVALID_LIMIT", "inspect limit must be 1..1000")
    if file.is_dir():
        if any(value is not None for value in (key_column, key, heading, object_id)):
            raise FileEditError("INVALID_INSPECT", "Minecraft world inspection uses --pointer")
        return minecraft_world.inspect_data(file, pointer)
    data = file.read_bytes()
    fmt = format_for(file)
    value = parse_file(fmt, data)
    profile = profile_for(fmt)
    if fmt == "svg":
        detail = profile.inspect(value, object_id=object_id)
    elif profile is not None:
        detail = profile.inspect(value, pointer=pointer)
    elif fmt == "json":
        selected = json_get(value, pointer)
        encoded = json.dumps(selected, ensure_ascii=False, allow_nan=False)
        detail = (
            {"pointer": pointer, "value": selected}
            if len(encoded) <= 20000
            else {"pointer": pointer, "value_omitted": True, "summary": summary(fmt, selected)}
        )
    elif fmt == "csv":
        if (key_column is None) != (key is None):
            raise FileEditError("INVALID_INSPECT", "CSV lookup requires both --key-column and --key")
        detail = (
            {"key_column": key_column, "key": key, "row": value["rows"][_csv_rows(value, key_column, key)]}
            if key_column is not None
            else {
                "columns": value["columns"],
                "rows": value["rows"][:limit],
                "total_rows": len(value["rows"]),
                "truncated": len(value["rows"]) > limit,
            }
        )
    else:
        if heading is None:
            detail = {"headings": summary(fmt, value)["headings"]}
        else:
            item, lines, end = markdown_section(value, heading)
            body = "".join(lines[item["line"] + 1 : end])
            detail = {"heading": item, "body": body[:20000], "truncated": len(body) > 20000}
    return {"ok": True, "protocol": PROTOCOL, "format": fmt, "sha256": sha256(data), **detail}


def preview_data(file: Path, plan_data: bytes, *, limit: int = 200) -> dict:
    if not 1 <= limit <= 10000:
        raise FileEditError("INVALID_LIMIT", "preview limit must be 1..10000")
    if file.is_dir():
        return minecraft_world.preview_data(file, plan_data, limit=limit)
    data = file.read_bytes()
    candidate = prepare(file, data, plan_data)
    profile = profile_for(candidate.format)
    extra = {}
    if profile is not None and hasattr(profile, "projection"):
        before = json.dumps(
            profile.projection(profile.parse(data)), ensure_ascii=False, allow_nan=False, indent=2
        ) + "\n"
        after = json.dumps(
            profile.projection(profile.parse(candidate.output)), ensure_ascii=False, allow_nan=False, indent=2
        ) + "\n"
        extra = {"diff_kind": "semantic"}
        if candidate.format in {"xlsx", "docx", "pptx"} or getattr(profile, "PACKAGE", False):
            from .ooxml import changed_parts

            extra["package_parts"] = changed_parts(data, candidate.output)
    else:
        before, after = data.decode("utf-8-sig"), candidate.output.decode("utf-8-sig")
    lines = list(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=str(file),
            tofile=str(file) + " (edited)",
        )
    )
    return {
        "ok": True,
        "protocol": PROTOCOL,
        "format": candidate.format,
        "before_sha256": candidate.before_sha256,
        "after_sha256": candidate.after_sha256,
        "steps": candidate.steps,
        "diff": "".join(lines[:limit]),
        "truncated": len(lines) > limit,
        "committed": False,
        **extra,
    }


def apply_data(file: Path, plan_data: bytes, output: Path) -> dict:
    if file.is_dir():
        return minecraft_world.apply_data(file, plan_data, output)
    if output.resolve() == file.resolve() or output.exists():
        raise FileEditError("OUTPUT_EXISTS", "Output must be a new path distinct from input")
    if format_for(output) != format_for(file):
        raise FileEditError("FORMAT_MISMATCH", "Output extension must match input format")
    candidate = prepare(file, file.read_bytes(), plan_data)
    cleanup_warning = _publish_new(output, candidate.output)
    result = {
        "ok": True,
        "protocol": PROTOCOL,
        "format": candidate.format,
        "before_sha256": candidate.before_sha256,
        "after_sha256": candidate.after_sha256,
        "steps": candidate.steps,
        "output": str(output.resolve()),
        "committed": True,
    }
    if cleanup_warning:
        result["warnings"] = [cleanup_warning]
    return result
