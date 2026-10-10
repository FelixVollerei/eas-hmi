"""TOML configuration editing with JSON Pointer and preserved source trivia."""

from __future__ import annotations

import math
import re
import tomllib
from collections.abc import Mapping, MutableMapping, MutableSequence
from copy import deepcopy
from datetime import date, datetime, time

import tomlkit
from tomlkit.exceptions import TOMLKitError
from tomlkit.items import AoT
from tomlkit.toml_document import TOMLDocument

from .plan_ops import FileEditError, _fields, _pointer_parts

OPS = {"add", "set", "remove", "append"}
CONTRACT = {
    "addressing": "JSON Pointer: empty string selects the root table; /server/port selects a key; "
    "/peers/0 selects an array item. Escape ~ as ~0 and / as ~1.",
    "operations": {
        "add": {
            "example": {"op": "add", "pointer": "/server/enabled", "value": True},
            "description": "Create or replace a table key; insert at an array index or at - for its end.",
        },
        "set": {
            "example": {"op": "set", "pointer": "/server/port", "value": 8081},
            "description": "Replace an existing value. An empty pointer replaces the root with a JSON object.",
        },
        "remove": {
            "example": {"op": "remove", "pointer": "/server/debug"},
            "description": "Remove an existing key or array item; the root cannot be removed.",
        },
        "append": {
            "example": {"op": "append", "pointer": "/server/peers", "value": "backup"},
            "description": "Append to an array; an array of tables accepts objects only.",
        },
    },
    "limitations": [
        (
            "UTF-8 TOML with a table root. Plans accept JSON strings, booleans, finite numbers, arrays and objects; "
            "null has no TOML equivalent and is rejected recursively. Integers must fit signed 64 bits."
        ),
        (
            "Existing dates, times and nonfinite floats are preserved. Inspection represents them as "
            "{$type: date|datetime|time|float, value: text}; these are read-only type annotations, "
            "not date/time constructors for edit plans."
        ),
        (
            "Unchanged comments and layout are retained by tomlkit. Replacing an entire table or array "
            "recreates its contents and can discard comments inside it; removing a value removes its inline comment."
        ),
        (
            "Parent tables and arrays must exist. Array indices are decimal without leading zeroes. "
            "The - segment is only accepted for add at the end of an array."
        ),
    ],
}


def parse(data: bytes) -> TOMLDocument:
    try:
        text = data.decode("utf-8")
        # Independent parser ensures only valid TOML is accepted, including duplicate-key rejection.
        tomllib.loads(text)
        return tomlkit.parse(text)
    except (UnicodeError, ValueError, TOMLKitError) as exc:
        raise FileEditError("INVALID_TOML", str(exc)) from exc


def _json_value(value):
    if isinstance(value, datetime):
        return {"$type": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"$type": "date", "value": value.isoformat()}
    if isinstance(value, time):
        return {"$type": "time", "value": value.isoformat()}
    if isinstance(value, float) and not math.isfinite(value):
        return {"$type": "float", "value": str(value)}
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    return value


def summary(document: TOMLDocument) -> dict:
    keys = list(document)
    return {"root_type": "table", "keys": keys[:200], "length": len(keys), "truncated": len(keys) > 200}


def _index(part: str, *, allow_end: bool = False, length: int = 0) -> int:
    if allow_end and part == "-":
        return length
    if not re.fullmatch(r"0|[1-9][0-9]*", part, flags=re.ASCII):
        raise FileEditError("INVALID_POINTER", "Array segment must be an index or add at -")
    return int(part)


def _child(value, part: str):
    if isinstance(value, MutableMapping):
        if part in value:
            return value[part]
    elif isinstance(value, MutableSequence):
        index = _index(part)
        if index < len(value):
            return value[index]
    else:
        raise FileEditError("INVALID_TARGET", "Pointer traversal requires a table or array")
    raise FileEditError("TARGET_NOT_FOUND", f"TOML pointer segment not found: {part}")


def _get(document, pointer: str):
    if not isinstance(pointer, str):
        raise FileEditError("INVALID_POINTER", "pointer must be a string")
    value = document
    for part in _pointer_parts(pointer):
        value = _child(value, part)
    return value


def inspect(document: TOMLDocument, pointer: str = "") -> dict:
    value = _get(document, pointer)
    if hasattr(value, "unwrap"):
        value = value.unwrap()
    return {"pointer": pointer, "value": _json_value(value)}


def _validate_value(value):
    if value is None:
        raise FileEditError("INVALID_VALUE", "TOML has no null value")
    if type(value) in (str, bool):
        return
    if type(value) is int:
        if not -(2**63) <= value < 2**63:
            raise FileEditError("INVALID_VALUE", "TOML integers must fit signed 64 bits")
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise FileEditError("INVALID_VALUE", "Plan numbers must be finite")
        return
    if type(value) is list:
        for item in value:
            _validate_value(item)
        return
    if type(value) is dict:
        if not all(isinstance(key, str) for key in value):
            raise FileEditError("INVALID_VALUE", "Table keys must be strings")
        for item in value.values():
            _validate_value(item)
        return
    raise FileEditError("INVALID_VALUE", "Plan value must be a JSON value representable in TOML")


def _array_value(target, value):
    if isinstance(target, AoT) and not isinstance(value, dict):
        raise FileEditError("INVALID_VALUE", "An array of tables accepts object values only")
    return deepcopy(value)


def edit(document: TOMLDocument, step: dict) -> TOMLDocument:
    if not isinstance(step, dict) or not isinstance(step.get("op"), str) or step["op"] not in OPS:
        raise FileEditError("INVALID_STEP", "Unsupported TOML operation")
    op = step["op"]
    fields = {"op", "pointer"} if op == "remove" else {"op", "pointer", "value"}
    _fields(step, fields, fields)
    pointer = step["pointer"]
    if not isinstance(pointer, str):
        raise FileEditError("INVALID_STEP", "pointer must be a string")
    parts = _pointer_parts(pointer)
    if op != "remove":
        _validate_value(step["value"])
    # A rejected operation leaves the caller's parsed document intact.
    result = deepcopy(document)
    try:
        if op == "append":
            target = _get(result, pointer)
            if not isinstance(target, MutableSequence):
                raise FileEditError("INVALID_TARGET", "append requires an array")
            target.append(_array_value(target, step["value"]))
        elif not parts:
            if op == "remove" or not isinstance(step["value"], dict):
                raise FileEditError("INVALID_TARGET", "TOML root must remain a table")
            result = tomlkit.document()
            result.update(deepcopy(step["value"]))
        else:
            parent = result
            for part in parts[:-1]:
                parent = _child(parent, part)
            key = parts[-1]
            if op != "add":
                _child(parent, key)
            if isinstance(parent, MutableMapping):
                if op == "remove":
                    del parent[key]
                else:
                    parent[key] = deepcopy(step["value"])
            elif isinstance(parent, MutableSequence):
                index = _index(key, allow_end=op == "add", length=len(parent))
                if op == "remove":
                    del parent[index]
                elif op == "add":
                    if index > len(parent):
                        raise FileEditError("TARGET_NOT_FOUND", "Array insertion index exceeds length")
                    parent.insert(index, _array_value(parent, step["value"]))
                else:
                    parent[index] = _array_value(parent, step["value"])
            else:
                raise FileEditError("INVALID_TARGET", "TOML parent must be a table or array")
        serialize(result)
    except (TypeError, ValueError, TOMLKitError) as exc:
        raise FileEditError("INVALID_VALUE", str(exc)) from exc
    return result


def serialize(document: TOMLDocument) -> bytes:
    try:
        output = tomlkit.dumps(document)
        tomllib.loads(output)
        return output.encode("utf-8")
    except (UnicodeError, TypeError, ValueError, TOMLKitError) as exc:
        raise FileEditError("INVALID_TOML", str(exc)) from exc
