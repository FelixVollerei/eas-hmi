"""A comment-aware YAML 1.2 profile for the JSON-compatible subset."""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from ruamel.yaml.events import AliasEvent, DocumentEndEvent, DocumentStartEvent

from .plan_ops import FileEditError, _json_edit, json_get

OPS = {"add", "set", "remove", "append"}
CONTRACT = {
    "addressing": {
        "kind": "JSON Pointer (RFC 6901)",
        "root": "",
        "example": "/spec/replicas",
        "escaping": "Use ~1 for / and ~0 for ~ inside a mapping key; array indices start at zero.",
    },
    "operations": {
        "add": {
            "example": {"op": "add", "pointer": "/metadata/reviewed", "value": True},
            "description": "Add or replace a mapping key; insert before an array index or append at /-.",
        },
        "set": {
            "example": {"op": "set", "pointer": "/spec/replicas", "value": 3},
            "description": "Replace an existing value. An empty pointer replaces the whole document.",
        },
        "remove": {
            "example": {"op": "remove", "pointer": "/metadata/temporary"},
            "description": "Remove an existing mapping entry or array item; the root cannot be removed.",
        },
        "append": {
            "example": {"op": "append", "pointer": "/spec/ports", "value": 443},
            "description": "Append one JSON-compatible value to an existing sequence.",
        },
    },
    "limitations": [
        "UTF-8, one YAML document, YAML 1.2 semantics, string mapping keys only.",
        "Rejects duplicate keys, anchors, aliases, merge keys, custom tags, dates/timestamps, binary and sets.",
        "Values must be JSON-compatible; NaN and infinity are rejected. Quote dates to keep them as strings.",
        "Retains common comments, key order and scalar quoting. Indentation and spacing may normalize.",
        "Replacing a whole mapping, sequence or root also replaces comments attached inside that subtree.",
    ],
}

_JSON_TAGS = {
    "tag:yaml.org,2002:" + kind for kind in ("str", "int", "float", "bool", "null", "map", "seq")
}


@dataclass
class YamlDocument:
    root: Any
    newline: str = "\n"
    bom: bool = False
    explicit_start: bool = False
    explicit_end: bool = False
    version: tuple[int, int] | None = None


def _yaml() -> YAML:
    parser = YAML(typ="rt", pure=True)
    parser.allow_duplicate_keys = False
    parser.preserve_quotes = True
    return parser


def _plain(value: Any) -> Any:
    """Validate YAML's richer values and expose only ordinary JSON data."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return str(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise FileEditError("UNSUPPORTED_YAML", "YAML values must not contain NaN or infinity")
        return float(value)
    if isinstance(value, dict):
        if getattr(value, "merge", None):
            raise FileEditError("UNSUPPORTED_YAML", "YAML merge keys are not supported")
        if any(not isinstance(key, str) for key in value):
            raise FileEditError("UNSUPPORTED_YAML", "YAML mapping keys must be strings")
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    raise FileEditError("UNSUPPORTED_YAML", f"Non-JSON YAML value: {type(value).__name__}")


def parse(data: bytes) -> YamlDocument:
    try:
        text = data.decode("utf-8-sig")
        starts = []
        explicit_end = False
        # Inspect parser events before constructing any object. Rejecting all anchors also
        # guarantees that an edit through one pointer cannot change another aliased path.
        for event in _yaml().parse(text):
            if isinstance(event, DocumentStartEvent):
                starts.append(event)
                if event.version is not None and event.version != (1, 2):
                    raise FileEditError("UNSUPPORTED_YAML", "Only YAML 1.2 documents are supported")
            if isinstance(event, DocumentEndEvent):
                explicit_end = event.explicit
            if isinstance(event, AliasEvent) or getattr(event, "anchor", None) is not None:
                raise FileEditError("UNSUPPORTED_YAML", "YAML anchors and aliases are not supported")
            tag = getattr(event, "tag", None)
            if tag is not None and tag not in _JSON_TAGS:
                raise FileEditError("UNSUPPORTED_YAML", f"Unsupported YAML tag: {tag}")
        if len(starts) != 1:
            raise FileEditError("UNSUPPORTED_YAML", "Expected exactly one YAML document")
        root = _yaml().load(text)
        _plain(root)
        first_line = text.splitlines(keepends=True)[0] if text else ""
        return YamlDocument(
            root=root,
            newline="\r\n" if first_line.endswith("\r\n") else "\n",
            bom=data.startswith(b"\xef\xbb\xbf"),
            explicit_start=starts[0].explicit,
            explicit_end=explicit_end,
            version=starts[0].version,
        )
    except (UnicodeError, YAMLError, ValueError, TypeError) as exc:
        raise FileEditError("INVALID_YAML", str(exc)) from exc


def summary(document: YamlDocument) -> dict:
    root = document.root
    keys = list(root) if isinstance(root, dict) else None
    return {
        "root_type": type(_plain(root)).__name__,
        "keys": [str(key) for key in keys[:200]] if keys is not None else None,
        "length": len(root) if isinstance(root, (dict, list, str)) else None,
        "truncated": keys is not None and len(keys) > 200,
        "yaml_version": "1.2",
        "comment_preservation": "round-trip",
    }


def inspect(document: YamlDocument, pointer: str = "") -> dict:
    if not isinstance(pointer, str):
        raise FileEditError("INVALID_POINTER", "pointer must be a string")
    return {"pointer": pointer, "value": _plain(json_get(document.root, pointer))}


def edit(document: YamlDocument, step: dict) -> YamlDocument:
    if not isinstance(step, dict) or not isinstance(step.get("op"), str) or step["op"] not in OPS:
        raise FileEditError("INVALID_STEP", "Unsupported YAML operation")
    if "value" in step:
        _plain(step["value"])
    document.root = _json_edit(document.root, step)
    return document


def serialize(document: YamlDocument) -> bytes:
    expected = _plain(document.root)
    writer = _yaml()
    writer.line_break = document.newline
    writer.explicit_start = document.explicit_start
    writer.explicit_end = document.explicit_end
    writer.version = document.version
    stream = io.StringIO()
    try:
        writer.dump(document.root, stream)
        result = (b"\xef\xbb\xbf" if document.bom else b"") + stream.getvalue().encode("utf-8")
    except (YAMLError, ValueError, TypeError) as exc:
        raise FileEditError("INVALID_YAML", str(exc)) from exc
    if _plain(parse(result).root) != expected:
        raise FileEditError("INVALID_YAML", "YAML round-trip changed the document values")
    return result
