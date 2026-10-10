"""Span-preserving edits of placed KiCad schematic symbols by UUID."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

from .plan_ops import FileEditError, _fields, _pointer_parts

OPS = {"set-property", "move-symbol"}
CONTRACT = {
    "addressing": "Inspect /symbols/<UUID>; only placed root-level symbol nodes are edit targets.",
    "operations": {
        "set-property": {"example": {"op": "set-property", "uuid": "e2b62867-7d5d-4a92-9064-d715d42a6657", "name": "Value", "value": "10k"}},
        "move-symbol": {"example": {"op": "move-symbol", "uuid": "e2b62867-7d5d-4a92-9064-d715d42a6657", "x": 100, "y": 75}},
    },
    "limitations": [
        "KiCad schematic S-expression files; targets are placed symbols with unique UUIDs.",
        "Only existing Value, Footprint and Datasheet properties may change. Reference changes need instance-path synchronization.",
        "move-symbol changes symbol origin in millimeters, not connected wire endpoints; run KiCad ERC before use.",
        "Unmodified source spans remain byte-identical. This parser does not certify KiCad's full schematic semantics.",
    ],
    "reference": "https://dev-docs.kicad.org/en/file-formats/sexpr-schematic/",
}


@dataclass
class Atom:
    value: str
    start: int
    end: int
    quoted: bool


@dataclass
class Node:
    items: list
    start: int
    end: int

    @property
    def head(self):
        return self.items[0].value if self.items and isinstance(self.items[0], Atom) else None


@dataclass
class Document:
    raw: bytes
    text: str
    root: Node


def _bad(message, code="INVALID_KICAD_SCH"):
    raise FileEditError(code, message)


def _tokens(text):
    output = []
    index = 0
    while index < len(text):
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if char in "()":
            output.append(Atom(char, index, index + 1, False))
            index += 1
            continue
        if char == '"':
            start = index
            index += 1
            while index < len(text):
                if text[index] == "\\":
                    index += 2
                elif text[index] == '"':
                    index += 1
                    break
                else:
                    index += 1
            else:
                _bad("Unclosed schematic string")
            try:
                value = json.loads(text[start:index])
            except ValueError as exc:
                raise FileEditError("INVALID_KICAD_SCH", "Invalid quoted string") from exc
            output.append(Atom(value, start, index, True))
            continue
        start = index
        while index < len(text) and not text[index].isspace() and text[index] not in "()":
            index += 1
        output.append(Atom(text[start:index], start, index, False))
    return output


def _parse_tokens(tokens):
    stack = []
    root = None
    for token in tokens:
        if token.value == "(" and not token.quoted:
            if len(stack) >= 100:
                _bad("S-expression nesting exceeds 100")
            stack.append(Node([], token.start, -1))
        elif token.value == ")" and not token.quoted:
            if not stack:
                _bad("Unexpected closing parenthesis")
            node = stack.pop()
            node.end = token.end
            if stack:
                stack[-1].items.append(node)
            elif root is None:
                root = node
            else:
                _bad("Multiple top-level expressions")
        elif stack:
            stack[-1].items.append(token)
        else:
            _bad("Unexpected token outside schematic")
    if stack or root is None or root.head != "kicad_sch":
        _bad("Expected one complete kicad_sch expression")
    return root


def _children(node, name):
    return [item for item in node.items if isinstance(item, Node) and item.head == name]


def _atom(node, index=1):
    if len(node.items) <= index or not isinstance(node.items[index], Atom):
        _bad("Malformed schematic node")
    return node.items[index]


def _symbols(document):
    symbols = {}
    for node in _children(document.root, "symbol"):
        uuids = _children(node, "uuid")
        if len(uuids) != 1:
            _bad("Placed symbol needs one UUID")
        ident = _atom(uuids[0]).value
        if not ident or ident in symbols:
            _bad("Placed symbol UUIDs must be unique")
        symbols[ident] = node
    return symbols


def parse(data):
    if len(data) > 8_000_000:
        _bad("Schematic exceeds 8 MB", "UNSUPPORTED_KICAD_SCH")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError as exc:
        raise FileEditError("INVALID_KICAD_SCH", "Expected UTF-8 schematic") from exc
    document = Document(data, text, _parse_tokens(_tokens(text)))
    _symbols(document)
    return document


def _symbol(document, ident):
    if not isinstance(ident, str) or not ident:
        _bad("uuid must be a nonempty string", "INVALID_STEP")
    try:
        return _symbols(document)[ident]
    except KeyError as exc:
        raise FileEditError("TARGET_NOT_FOUND", f"Symbol UUID {ident} not found") from exc


def _view(node):
    uuid = _atom(_children(node, "uuid")[0]).value
    at = _children(node, "at")
    props = {}
    positions = {}
    for item in _children(node, "property"):
        name = _atom(item, 1).value
        props[name] = _atom(item, 2).value
        at_property = _children(item, "at")
        if at_property:
            positions[name] = [float(_atom(at_property[0], i).value) for i in (1, 2, 3)]
    return {"uuid": uuid, "lib_id": _atom(_children(node, "lib_id")[0]).value,
            "at": [float(_atom(at[0], i).value) for i in (1, 2, 3)] if at else None,
            "properties": props, "property_positions": positions}


def summary(document):
    return {"symbol_count": len(_symbols(document)), "uuids": list(_symbols(document))[:100]}


def inspect(document, pointer=""):
    parts = _pointer_parts(pointer)
    if not parts:
        return {"pointer": pointer, "value": summary(document)}
    if len(parts) != 2 or parts[0] != "symbols":
        _bad("Use /symbols/<UUID>", "INVALID_INSPECT")
    return {"pointer": pointer, "value": _view(_symbol(document, parts[1]))}


def projection(document):
    return {"symbols": [_view(node) for node in _symbols(document).values()]}


def _replace(document, replacements):
    text = document.text
    for start, end, value in sorted(replacements, reverse=True):
        text = text[:start] + value + text[end:]
    return parse(text.encode("utf-8"))


def edit(document, step):
    op = step["op"]
    fields = {"op", "uuid", "name", "value"} if op == "set-property" else {"op", "uuid", "x", "y"}
    _fields(step, fields, fields)
    node = _symbol(document, step["uuid"])
    if op == "set-property":
        if step["name"] not in {"Value", "Footprint", "Datasheet"} or not isinstance(step["value"], str):
            _bad("Only Value, Footprint and Datasheet text may be set", "INVALID_STEP")
        found = [item for item in _children(node, "property") if _atom(item, 1).value == step["name"]]
        if len(found) != 1:
            _bad("Property must exist exactly once", "TARGET_NOT_UNIQUE")
        target = _atom(found[0], 2)
        if not target.quoted:
            _bad("Property value must be a quoted string", "UNSUPPORTED_KICAD_SCH")
        return _replace(document, [(target.start, target.end, json.dumps(step["value"], ensure_ascii=False))])
    if any(type(step[key]) not in {int, float} or not math.isfinite(step[key]) for key in ("x", "y")):
        _bad("x/y must be finite millimeter numbers", "INVALID_STEP")
    at = _children(node, "at")
    if len(at) != 1 or len(at[0].items) < 4:
        _bad("Symbol needs one explicit (at x y angle)", "UNSUPPORTED_KICAD_SCH")
    delta = (step["x"] - float(_atom(at[0], 1).value), step["y"] - float(_atom(at[0], 2).value))
    replacements = [(_atom(at[0], i).start, _atom(at[0], i).end, str(step[key]))
                    for i, key in ((1, "x"), (2, "y"))]
    for prop in _children(node, "property"):
        locations = _children(prop, "at")
        if len(locations) != 1:
            _bad("Property needs one explicit position for symbol move", "UNSUPPORTED_KICAD_SCH")
        for i in (1, 2):
            atom = _atom(locations[0], i)
            replacements.append((atom.start, atom.end, str(round(float(atom.value) + delta[i - 1], 6))))
    return _replace(document, replacements)


def serialize(document):
    return document.raw
