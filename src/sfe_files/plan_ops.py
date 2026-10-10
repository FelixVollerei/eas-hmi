"""Shared plan errors, JSON edits, and pointer validation for file profiles."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any


class FileEditError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code

    def as_dict(self):
        return {"ok": False, "code": self.code, "message": str(self)}


def _json(data: bytes):
    def reject_constant(value: str):
        raise ValueError(f"Non-JSON number: {value}")

    def unique_pairs(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError(f"Duplicate JSON key: {key}")
            out[key] = value
        return out

    try:
        return json.loads(
            data.decode("utf-8-sig"), parse_constant=reject_constant, object_pairs_hook=unique_pairs
        )
    except (UnicodeError, ValueError) as exc:
        raise FileEditError("INVALID_JSON", str(exc)) from exc


def _pointer_parts(pointer: str) -> list[str]:
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise FileEditError("INVALID_POINTER", "JSON pointer must be empty or start with /")
    parts = pointer[1:].split("/")
    if any(re.search(r"~(?![01])", part) for part in parts):
        raise FileEditError("INVALID_POINTER", "Only ~0 and ~1 escapes are valid")
    return [part.replace("~1", "/").replace("~0", "~") for part in parts]


def _child(value: Any, part: str):
    try:
        if isinstance(value, dict):
            return value[part]
        if isinstance(value, list) and part.isascii() and re.fullmatch(r"0|[1-9][0-9]*", part):
            return value[int(part)]
    except (KeyError, IndexError):
        pass
    raise FileEditError("TARGET_NOT_FOUND", f"JSON pointer segment not found: {part}")


def json_get(value: Any, pointer: str):
    for part in _pointer_parts(pointer):
        value = _child(value, part)
    return value


def _fields(step: dict, allowed: set[str], required: set[str]):
    if not isinstance(step, dict) or set(step) - allowed or required - set(step):
        raise FileEditError("INVALID_STEP", f"Expected fields {sorted(required)}; allowed {sorted(allowed)}")


def _json_edit(value: Any, step: dict):
    op = step["op"]
    _fields(
        step,
        {"op", "pointer", "value"} if op != "remove" else {"op", "pointer"},
        {"op", "pointer"} if op == "remove" else {"op", "pointer", "value"},
    )
    pointer = step["pointer"]
    if not isinstance(pointer, str):
        raise FileEditError("INVALID_STEP", "pointer must be a string")
    parts = _pointer_parts(pointer)
    if op == "append":
        target = json_get(value, pointer)
        if not isinstance(target, list):
            raise FileEditError("INVALID_TARGET", "append requires an array")
        target.append(deepcopy(step["value"]))
        return value
    if not parts:
        if op == "remove":
            raise FileEditError("INVALID_TARGET", "Cannot remove the JSON root")
        return deepcopy(step["value"])
    parent = value
    for part in parts[:-1]:
        parent = _child(parent, part)
    key = parts[-1]
    if op != "add":
        _child(parent, key)
    if isinstance(parent, dict):
        if op == "remove":
            del parent[key]
        else:
            parent[key] = deepcopy(step["value"])
    elif isinstance(parent, list):
        if op == "add" and key == "-":
            index = len(parent)
        elif key.isascii() and re.fullmatch(r"0|[1-9][0-9]*", key):
            index = int(key)
        else:
            raise FileEditError("INVALID_POINTER", "Array segment must be an index or add at -")
        if op == "remove":
            del parent[index]
        elif op == "add":
            if index > len(parent):
                raise FileEditError("TARGET_NOT_FOUND", "Array insertion index exceeds length")
            parent.insert(index, deepcopy(step["value"]))
        else:
            parent[index] = deepcopy(step["value"])
    else:
        raise FileEditError("INVALID_TARGET", "JSON parent must be an object or array")
    return value
