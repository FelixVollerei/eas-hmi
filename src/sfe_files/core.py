"""Shared plan execution for inspectable structured-file edit profiles."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any

from .plan_ops import FileEditError, _fields, _json, _json_edit

PROTOCOL = "sfe/0.1"
FORMATS = {
    ".json": "json",
    ".csv": "csv",
    ".md": "markdown",
    ".markdown": "markdown",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".svg": "svg",
    ".xlsx": "xlsx",
    ".docx": "docx",
    ".pptx": "pptx",
    ".srt": "srt",
    ".vtt": "vtt",
    ".ics": "ics",
    ".vcf": "vcf",
    ".jsonl": "jsonl",
    ".ndjson": "jsonl",
    ".odt": "odt",
    ".ods": "ods",
    ".odp": "odp",
    ".html": "html",
    ".htm": "html",
    ".epub": "epub",
    ".drawio": "drawio",
    ".geojson": "geojson",
    ".gpx": "gpx",
    ".kml": "kml",
    ".sqlite": "sqlite",
    ".sqlite3": "sqlite",
    ".db": "sqlite",
    ".parquet": "parquet",
    ".arrow": "arrow",
    ".feather": "arrow",
    ".dxf": "dxf",
    ".kicad_sch": "kicad_sch",
    ".musicxml": "musicxml",
}
ADAPTERS = {
    name: f".{name}_profile"
    for name in (
        "yaml", "toml", "svg", "xlsx", "docx", "pptx", "srt", "vtt", "ics", "vcf", "jsonl",
        "odt", "ods", "odp", "html", "epub", "drawio", "geojson", "gpx", "kml",
        "sqlite", "parquet", "arrow", "dxf", "kicad_sch", "musicxml",
    )
}
OPS = {
    "json": {"add", "set", "remove", "append"},
    "csv": {"set-cell", "append-row", "delete-row"},
    "markdown": {"replace-section", "append-section"},
}
BASE_CONTRACTS = {
    "json": {
        "addressing": "JSON Pointer: empty string selects the root; escape ~ as ~0 and / as ~1.",
        "operations": {
            "add": {
                "example": {"op": "add", "pointer": "/reviewed_by", "value": "agent"},
                "description": "Add or replace an object member, insert an array item, or replace the root.",
            },
            "set": {
                "example": {"op": "set", "pointer": "/enabled", "value": True},
                "description": "Replace an existing value or the root document.",
            },
            "remove": {
                "example": {"op": "remove", "pointer": "/temporary"},
                "description": "Remove an existing member or array item; the root cannot be removed.",
            },
            "append": {
                "example": {"op": "append", "pointer": "/alarms", "value": "low-pressure"},
                "description": "Append one value to an existing array.",
            },
        },
        "limitations": [
            "UTF-8 JSON; duplicate object keys and nonstandard or nonfinite numbers are rejected.",
            "Serialization uses two-space indentation and may change original whitespace.",
            "These are profile operations, not a complete RFC 6902 JSON Patch implementation.",
        ],
    },
    "csv": {
        "addressing": "Use unique key_column and key strings to select one row; inspect can also list a bounded prefix.",
        "operations": {
            "set-cell": {
                "example": {
                    "op": "set-cell", "key_column": "id", "key": "P-01", "column": "status", "value": "ready"
                },
                "description": "Replace one string cell in a uniquely selected row.",
            },
            "append-row": {
                "example": {
                    "op": "append-row", "key_column": "id", "row": {"id": "T-03", "status": "pending"}
                },
                "description": "Append a row containing every header column and a new unique key.",
            },
            "delete-row": {
                "example": {"op": "delete-row", "key_column": "id", "key": "T-03"},
                "description": "Remove one uniquely selected row.",
            },
        },
        "limitations": [
            "UTF-8 comma-separated CSV with a nonempty header and unique column names.",
            "All cells, keys and column values in plans are strings; row width must match the header.",
            "BOM and first-line newline style are retained; field quoting may normalize.",
        ],
    },
    "markdown": {
        "addressing": "Use a unique ATX heading; fenced code blocks are excluded from the heading outline.",
        "operations": {
            "replace-section": {
                "example": {"op": "replace-section", "heading": "Findings", "body": "Reviewed."},
                "description": "Replace the body under one heading until the next heading of the same or higher level.",
            },
            "append-section": {
                "example": {"op": "append-section", "heading": "Next action", "level": 2, "body": "Verify alarms."},
                "description": "Append a new unique ATX heading with level 1..6 and its body.",
            },
        },
        "limitations": [
            "Only ATX headings are addresses; duplicate headings are rejected as ambiguous.",
            "Setext and container-nested headings are outside this profile's addressing subset.",
        ],
    },
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def format_for(path: Path) -> str:
    legacy = {".xls": ".xlsx", ".doc": ".docx", ".ppt": ".pptx"}
    if path.suffix.lower() in legacy:
        raise FileEditError(
            "LEGACY_OFFICE_FORMAT",
            f"{path.suffix} is a legacy binary format; save as {legacy[path.suffix.lower()]} first. "
            "Renaming the extension does not convert the file.",
        )
    try:
        return FORMATS[path.suffix.lower()]
    except KeyError as exc:
        raise FileEditError("UNSUPPORTED_FORMAT", f"Unsupported file extension: {path.suffix}") from exc


def profile_for(fmt: str):
    """Load a format implementation only when its capabilities or data are requested."""
    return import_module(ADAPTERS[fmt], __package__) if fmt in ADAPTERS else None


def operations_for(fmt: str) -> set[str]:
    profile = profile_for(fmt)
    return set(profile.OPS if profile is not None else OPS[fmt])


def _csv(data: bytes):
    try:
        text = data.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        columns = reader.fieldnames
        if not columns or any(not c for c in columns) or len(columns) != len(set(columns)):
            raise ValueError("CSV requires a nonempty header with unique column names")
        rows = list(reader)
        if any(None in row or any(value is None for value in row.values()) for row in rows):
            raise ValueError("CSV row width does not match the header")
        first_line = text.splitlines(keepends=True)[0]
        newline = "\r\n" if first_line.endswith("\r\n") else "\n"
        return {"columns": columns, "rows": rows, "newline": newline, "bom": data.startswith(b"\xef\xbb\xbf")}
    except (UnicodeError, ValueError, csv.Error) as exc:
        raise FileEditError("INVALID_CSV", str(exc)) from exc


HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
FENCE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})(.*)$")


def _headings(text: str):
    """ATX headings outside fenced code blocks, with source line positions."""
    headings = []
    fence_char = None
    fence_size = 0
    for index, line in enumerate(text.splitlines(keepends=True)):
        clean = line.rstrip("\r\n")
        marker = FENCE.match(clean)
        if fence_char:
            if (
                marker
                and marker.group(1)[0] == fence_char
                and len(marker.group(1)) >= fence_size
                and not marker.group(2).strip()
            ):
                fence_char = None
            continue
        if marker:
            fence_char, fence_size = marker.group(1)[0], len(marker.group(1))
            continue
        match = HEADING.match(clean)
        if match:
            headings.append({"line": index, "level": len(match.group(1)), "heading": match.group(2).strip()})
    return headings


def parse_file(fmt: str, data: bytes):
    profile = profile_for(fmt)
    if profile is not None:
        return profile.parse(data)
    if fmt == "json":
        return _json(data)
    if fmt == "csv":
        return _csv(data)
    try:
        return data.decode("utf-8-sig")
    except UnicodeError as exc:
        raise FileEditError("INVALID_MARKDOWN", str(exc)) from exc


def summary(fmt: str, value: Any) -> dict:
    profile = profile_for(fmt)
    if profile is not None:
        return profile.summary(value)
    if fmt == "json":
        keys = list(value) if isinstance(value, dict) else None
        return {
            "root_type": type(value).__name__,
            "keys": keys[:200] if keys is not None else None,
            "length": len(value) if isinstance(value, (dict, list, str)) else None,
            "truncated": keys is not None and len(keys) > 200,
        }
    if fmt == "csv":
        return {"columns": value["columns"], "row_count": len(value["rows"])}
    headings = _headings(value)
    return {
        "headings": headings[:200],
        "heading_count": len(headings),
        "line_count": len(value.splitlines()),
        "truncated": len(headings) > 200,
    }


def _csv_rows(value: dict, key_column: str, key_value: str):
    if key_column not in value["columns"]:
        raise FileEditError("INVALID_KEY_COLUMN", f"Column not found: {key_column}")
    matches = [i for i, row in enumerate(value["rows"]) if row[key_column] == key_value]
    if len(matches) != 1:
        raise FileEditError(
            "ROW_NOT_UNIQUE", f"Expected one row for {key_column}={key_value}; found {len(matches)}"
        )
    return matches[0]


def _csv_edit(value: dict, step: dict):
    op = step["op"]
    if op == "set-cell":
        _fields(
            step,
            {"op", "key_column", "key", "column", "value"},
            {"op", "key_column", "key", "column", "value"},
        )
        if step["column"] not in value["columns"]:
            raise FileEditError("INVALID_COLUMN", f"Column not found: {step['column']}")
        if not all(isinstance(step[k], str) for k in ("key_column", "key", "column", "value")):
            raise FileEditError("INVALID_STEP", "CSV keys, columns and values must be strings")
        index = _csv_rows(value, step["key_column"], step["key"])
        value["rows"][index][step["column"]] = step["value"]
    elif op == "delete-row":
        _fields(step, {"op", "key_column", "key"}, {"op", "key_column", "key"})
        if not isinstance(step["key_column"], str) or not isinstance(step["key"], str):
            raise FileEditError("INVALID_STEP", "CSV key must be a string")
        del value["rows"][_csv_rows(value, step["key_column"], step["key"])]
    else:
        _fields(step, {"op", "row", "key_column"}, {"op", "row", "key_column"})
        row = step["row"]
        if (
            not isinstance(row, dict)
            or set(row) != set(value["columns"])
            or not all(isinstance(cell, str) for cell in row.values())
        ):
            raise FileEditError("INVALID_ROW", "append-row requires every header column with string values")
        key_column = step["key_column"]
        if not isinstance(key_column, str) or key_column not in value["columns"]:
            raise FileEditError("INVALID_KEY_COLUMN", "append-row requires an existing key column")
        if any(existing[key_column] == row[key_column] for existing in value["rows"]):
            raise FileEditError("ROW_NOT_UNIQUE", "append-row key already exists")
        value["rows"].append(dict(row))
    return value


def markdown_section(text: str, heading: str):
    headings = _headings(text)
    matches = [i for i, item in enumerate(headings) if item["heading"] == heading]
    if len(matches) != 1:
        raise FileEditError("SECTION_NOT_UNIQUE", f"Expected one heading '{heading}'; found {len(matches)}")
    item = headings[matches[0]]
    lines = text.splitlines(keepends=True)
    end = len(lines)
    for later in headings[matches[0] + 1 :]:
        if later["level"] <= item["level"]:
            end = later["line"]
            break
    return item, lines, end


def _markdown_edit(text: str, step: dict):
    op = step["op"]
    newline = "\r\n" if "\r\n" in text else "\n"
    if op == "replace-section":
        _fields(step, {"op", "heading", "body"}, {"op", "heading", "body"})
        if not isinstance(step["heading"], str) or not isinstance(step["body"], str):
            raise FileEditError("INVALID_STEP", "heading and body must be strings")
        item, lines, end = markdown_section(text, step["heading"])
        body = step["body"].replace("\r\n", "\n").replace("\r", "\n").rstrip("\n").replace("\n", newline)
        prefix = "".join(lines[: item["line"] + 1])
        if not prefix.endswith(("\n", "\r")):
            prefix += newline
        return prefix + (body + newline * 2 if body else newline) + "".join(lines[end:])
    _fields(step, {"op", "heading", "level", "body"}, {"op", "heading", "level", "body"})
    if (
        not isinstance(step["heading"], str)
        or not step["heading"].strip()
        or "\n" in step["heading"]
        or "\r" in step["heading"]
        or type(step["level"]) is not int
        or not 1 <= step["level"] <= 6
        or not isinstance(step["body"], str)
    ):
        raise FileEditError("INVALID_STEP", "append-section needs a heading, level 1..6 and body")
    if any(item["heading"] == step["heading"] for item in _headings(text)):
        raise FileEditError("SECTION_NOT_UNIQUE", "Heading already exists")
    prefix = text.rstrip("\r\n") + newline * 2 if text else ""
    body = step["body"].replace("\r\n", "\n").replace("\r", "\n").rstrip("\n").replace("\n", newline)
    return (
        prefix + "#" * step["level"] + " " + step["heading"] + newline * 2 + (body + newline if body else "")
    )


def serialize(fmt: str, value: Any) -> bytes:
    profile = profile_for(fmt)
    if profile is not None:
        return profile.serialize(value)
    if fmt == "json":
        try:
            return (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise FileEditError("INVALID_JSON", str(exc)) from exc
    if fmt == "csv":
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=value["columns"], lineterminator=value["newline"])
        writer.writeheader()
        writer.writerows(value["rows"])
        return (b"\xef\xbb\xbf" if value["bom"] else b"") + output.getvalue().encode("utf-8")
    return value.encode("utf-8")


@dataclass
class Prepared:
    format: str
    before_sha256: str
    after_sha256: str
    output: bytes
    steps: int


def prepare(path: Path, data: bytes, plan_data: bytes) -> Prepared:
    fmt = format_for(path)
    plan = _json(plan_data)
    if not isinstance(plan, dict) or set(plan) != {"protocol", "format", "base", "steps"}:
        raise FileEditError("INVALID_PLAN", "Plan requires protocol, format, base and steps")
    if plan["protocol"] != PROTOCOL or plan["format"] != fmt:
        raise FileEditError("INVALID_PLAN", "Plan protocol or format does not match the file")
    base = plan["base"]
    if not isinstance(base, dict) or set(base) != {"sha256"} or not isinstance(base["sha256"], str):
        raise FileEditError("INVALID_PLAN", "Plan base requires sha256")
    if base["sha256"] != sha256(data):
        raise FileEditError("BASE_CONFLICT", "File bytes no longer match the plan base")
    steps = plan["steps"]
    if not isinstance(steps, list) or not steps:
        raise FileEditError("INVALID_PLAN", "Plan steps must be a nonempty array")
    value = parse_file(fmt, data)
    profile = profile_for(fmt)
    operations = operations_for(fmt)
    for step in steps:
        if not isinstance(step, dict) or not isinstance(step.get("op"), str) or step["op"] not in operations:
            raise FileEditError("INVALID_STEP", f"Unsupported {fmt} operation")
        if profile is not None:
            value = profile.edit(value, step)
        elif fmt == "json":
            value = _json_edit(value, step)
        elif fmt == "csv":
            value = _csv_edit(value, step)
        else:
            value = _markdown_edit(value, step)
    output = serialize(fmt, value)
    return Prepared(fmt, sha256(data), sha256(output), output, len(steps))
