"""Inspect JSON Lines values and edit object records by a unique string key."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .plan_ops import FileEditError, _fields, _json, _json_edit, _pointer_parts, json_get

OPS = {"set-field", "replace-record", "append-record", "delete-record"}
CONTRACT = {
    "addressing": (
        "Read /records/1 (1-based source order) or /by-key/id/P-01/status (string key). "
        "Escape pointer components with ~0 and ~1. Edits select key_column + key; "
        "every record must have a unique string value in that top-level column."
    ),
    "operations": {
        "set-field": {
            "example": {"op": "set-field", "key_column": "id", "key": "P-01", "pointer": "/status", "value": "ready"},
            "description": "Set an existing JSON Pointer in the selected record; keys must remain unique.",
        },
        "replace-record": {
            "example": {"op": "replace-record", "key_column": "id", "key": "P-01", "record": {"id": "P-01", "status": "ready"}},
            "description": "Replace a whole keyed object record.",
        },
        "append-record": {
            "example": {"op": "append-record", "key_column": "id", "record": {"id": "T-03", "status": "inspection"}},
            "description": "Append an object with a new unique string key.",
        },
        "delete-record": {
            "example": {"op": "delete-record", "key_column": "id", "key": "P-01"},
            "description": "Remove exactly one keyed record.",
        },
    },
    "limitations": [
        "UTF-8 .jsonl/.ndjson only; BOM, blank lines, duplicate JSON keys and nonfinite numbers are rejected.",
        "Any JSON value can be inspected. Editing requires object records with unique string business keys.",
        "Unchanged records retain original whitespace and line endings; changed records use compact JSON.",
        "The entire file is validated in memory; compressed and streaming datasets are not supported.",
        "Line numbers are valid only for the stated base hash. An empty file is an empty dataset.",
    ],
}


@dataclass
class Record:
    value: Any
    raw: str


@dataclass
class Document:
    records: list[Record]
    newline: str


def _encoded(value) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        text.encode("utf-8")
        return text
    except (ValueError, TypeError, UnicodeError) as exc:
        raise FileEditError("INVALID_JSONL", "Record must contain finite JSON values and valid Unicode") from exc


def parse(data: bytes) -> Document:
    if data.startswith(b"\xef\xbb\xbf"):
        raise FileEditError("INVALID_JSONL", "JSON Lines must not contain a UTF-8 BOM")
    try:
        text = data.decode("utf-8")
    except UnicodeError as exc:
        raise FileEditError("INVALID_JSONL", "JSON Lines requires UTF-8") from exc
    records = []
    lines = text.split("\n")
    for index, line in enumerate(lines):
        if index == len(lines) - 1 and not line:
            continue
        if not line.strip():
            raise FileEditError("INVALID_JSONL", f"Blank record on line {index + 1}")
        if line.startswith("\ufeff"):
            raise FileEditError("INVALID_JSONL", f"Unexpected BOM on line {index + 1}")
        try:
            value = _json(line.encode("utf-8"))
        except FileEditError as exc:
            raise FileEditError("INVALID_JSONL", f"Line {index + 1}: {exc}") from exc
        _encoded(value)
        records.append(Record(value, line + ("\n" if index < len(lines) - 1 else "")))
    first = records[0].raw if records else ""
    return Document(records, "\r\n" if first.endswith("\r\n") else "\n")


def summary(doc: Document) -> dict:
    return {"record_count": len(doc.records), "records": [
        {"record": index, "type": type(item.value).__name__,
         "keys": list(item.value)[:30] if isinstance(item.value, dict) else None}
        for index, item in enumerate(doc.records[:20], 1)
    ], "truncated": len(doc.records) > 20}


def _keys(doc: Document, column: str) -> dict[str, int]:
    if not isinstance(column, str) or not column:
        raise FileEditError("INVALID_KEY_COLUMN", "key_column must be a nonempty top-level property name")
    keys = {}
    for index, item in enumerate(doc.records):
        if not isinstance(item.value, dict) or not isinstance(item.value.get(column), str):
            raise FileEditError("INVALID_RECORD_KEY", f"Record {index + 1} needs a string {column} property")
        key = item.value[column]
        if key in keys:
            raise FileEditError("RECORD_NOT_UNIQUE", f"Duplicate {column} key: {key}")
        keys[key] = index
    return keys


def inspect(doc: Document, pointer: str = "") -> dict:
    if not isinstance(pointer, str):
        raise FileEditError("INVALID_POINTER", "pointer must be a string")
    parts = _pointer_parts(pointer)
    if not parts:
        return {"value": summary(doc)}
    if len(parts) >= 2 and parts[0] == "records" and re.fullmatch(r"[1-9][0-9]*", parts[1]):
        index = int(parts[1]) - 1
        if index >= len(doc.records):
            raise FileEditError("TARGET_NOT_FOUND", "Record number is outside the dataset")
        remaining = parts[2:]
    elif len(parts) >= 3 and parts[0] == "by-key":
        keys = _keys(doc, parts[1])
        if parts[2] not in keys:
            raise FileEditError("TARGET_NOT_FOUND", f"Record key not found: {parts[2]}")
        index, remaining = keys[parts[2]], parts[3:]
    else:
        raise FileEditError("INVALID_POINTER", "Use /records/1 or /by-key/id/P-01")
    suffix = "".join("/" + part.replace("~", "~0").replace("/", "~1") for part in remaining)
    value = json_get(doc.records[index].value, suffix)
    if len(_encoded(value)) > 20000:
        return {"pointer": pointer, "record": index + 1, "value_omitted": True,
                "message": "Select a smaller field using a pointer"}
    return {"pointer": pointer, "record": index + 1, "value": value}


def edit(doc: Document, step: dict) -> Document:
    if not isinstance(step, dict) or not isinstance(step.get("op"), str) or step["op"] not in OPS:
        raise FileEditError("INVALID_STEP", "Unsupported JSONL operation")
    op = step["op"]
    fields = {"op", "key_column"} | {
        "set-field": {"key", "pointer", "value"}, "replace-record": {"key", "record"},
        "append-record": {"record"}, "delete-record": {"key"},
    }[op]
    _fields(step, fields, fields)
    column = step["key_column"]
    keys = _keys(doc, column)
    index = None
    if op != "append-record":
        if not isinstance(step["key"], str) or step["key"] not in keys:
            raise FileEditError("TARGET_NOT_FOUND", "Expected an existing string record key")
        index = keys[step["key"]]
    if op == "delete-record":
        del doc.records[index]
        return doc
    if op == "set-field":
        value = _json_edit(deepcopy(doc.records[index].value),
                           {"op": "set", "pointer": step["pointer"], "value": step["value"]})
    else:
        value = deepcopy(step["record"])
    if not isinstance(value, dict) or not isinstance(value.get(column), str):
        raise FileEditError("INVALID_RECORD_KEY", "Edited record must remain an object with a string business key")
    if value[column] in keys and keys[value[column]] != index:
        raise FileEditError("RECORD_NOT_UNIQUE", f"Duplicate {column} key: {value[column]}")
    encoded = _encoded(value)
    if op == "append-record":
        if doc.records and not doc.records[-1].raw.endswith("\n"):
            doc.records[-1].raw += doc.newline
        doc.records.append(Record(value, encoded + doc.newline))
    else:
        old = doc.records[index]
        ending = "\r\n" if old.raw.endswith("\r\n") else "\n" if old.raw.endswith("\n") else ""
        doc.records[index] = Record(value, encoded + ending)
    return doc


def serialize(doc: Document) -> bytes:
    return "".join(record.raw for record in doc.records).encode("utf-8")
