"""ID-addressed edits to UTF-8 SVG XML, without a renderer or browser."""

from __future__ import annotations

import io
import math
import re

from lxml import etree

from .plan_ops import FileEditError, _fields

SVG_NAMESPACE = "http://www.w3.org/2000/svg"
OPS = {"set-attribute", "remove-attribute", "set-text", "translate"}
CONTRACT = {
    "addressing": "A unique, nonempty SVG id, supplied as id in steps and --id for inspect.",
    "operations": {
        "set-attribute": {
            "example": {"op": "set-attribute", "id": "pump-body", "name": "fill", "value": "#16a34a"},
            "description": "Set an unqualified XML attribute to a string; id and xmlns are immutable.",
        },
        "remove-attribute": {
            "example": {"op": "remove-attribute", "id": "pump-body", "name": "opacity"},
            "description": "Remove an existing unqualified attribute; id and xmlns are immutable.",
        },
        "set-text": {
            "example": {"op": "set-text", "id": "pump-label", "text": "Pump ready"},
            "description": "Replace text on a text, tspan, title or desc element with no child nodes.",
        },
        "translate": {
            "example": {"op": "translate", "id": "pump", "dx": 24, "dy": 12},
            "description": (
                "Prepend translate(dx dy) to the transform attribute, preserving the existing transform. "
                "The displacement is in the parent's user coordinates, before ancestor/viewBox mapping. "
                "dx and dy must be finite numbers; booleans are rejected."
            ),
        },
    },
    "limitations": [
        "Input and output are UTF-8 XML (an input BOM is accepted); other declared encodings are rejected.",
        "The root must be svg in the SVG namespace or without a namespace. DTDs and entities are rejected.",
        "Every id must be nonempty and unique. Renaming ids and editing namespaced attributes are unsupported.",
        "The profile edits XML structure; it does not validate all SVG attribute or rendering semantics.",
        "CSS rules or inline styles can override transform and presentation attributes, including fill.",
        "No CSS cascade, animation, external resource, geometry, screen position or rendered bounds evaluation.",
        "Text with child nodes requires editing an identified leaf tspan or restructuring outside this profile.",
        "Serialization preserves other nodes but can normalize XML declaration, quotes, entities and line endings.",
        "Summary lists at most 200 identified elements and clips long id and viewport strings to 200 characters.",
    ],
    "references": ["https://www.w3.org/TR/SVG/coords.html"],
}


def _elements(document):
    return (element for element in document.getroot().iter() if isinstance(element.tag, str))


def _index(document):
    found = {}
    for element in _elements(document):
        object_id = element.get("id")
        if object_id is None:
            continue
        if not object_id:
            raise FileEditError("INVALID_SVG", "SVG id must be nonempty")
        if object_id in found:
            raise FileEditError("DUPLICATE_ID", f"SVG id is not unique: {object_id}")
        found[object_id] = element
    return found


def parse(data: bytes):
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError as exc:
        raise FileEditError("INVALID_SVG", "SVG input must be UTF-8") from exc
    declaration = re.match(r"<\?xml\b[^?]*\bencoding\s*=\s*(['\"])(.*?)\1", text)
    if declaration and declaration.group(2).lower() not in {"utf-8", "utf8"}:
        raise FileEditError("INVALID_SVG", "SVG XML declaration must specify UTF-8")
    parser = etree.XMLParser(
        no_network=True,
        resolve_entities=False,
        load_dtd=False,
        remove_blank_text=False,
        remove_comments=False,
        strip_cdata=False,
    )
    try:
        document = etree.parse(io.BytesIO(data), parser)
    except (etree.XMLSyntaxError, ValueError) as exc:
        raise FileEditError("INVALID_SVG", str(exc)) from exc
    if document.docinfo.doctype or any(isinstance(node, etree._Entity) for node in document.getroot().iter()):
        raise FileEditError("INVALID_SVG", "SVG DTDs and entities are unsupported")
    root = etree.QName(document.getroot())
    if root.localname != "svg" or root.namespace not in {None, SVG_NAMESPACE}:
        raise FileEditError("INVALID_SVG", "Root must be an SVG svg element")
    _index(document)
    return document


def _tag(element):
    name = etree.QName(element)
    return name.localname if name.namespace in {None, SVG_NAMESPACE} else element.tag


def summary(document) -> dict:
    root = document.getroot()
    indexed = _index(document)
    entries = list(indexed.items())[:200]
    dimensions = {name: root.get(name) for name in ("width", "height", "viewBox")}
    clipped = any(value is not None and len(value) > 200 for value in dimensions.values())
    clipped = clipped or any(len(object_id) > 200 for object_id, _ in entries)
    return {
        **{name: value[:200] if value is not None else None for name, value in dimensions.items()},
        "element_count": sum(1 for _ in _elements(document)),
        "id_count": len(indexed),
        "elements": [{"id": object_id[:200], "tag": _tag(element)} for object_id, element in entries],
        "truncated": len(indexed) > 200 or clipped,
    }


def _target(document, object_id):
    if not isinstance(object_id, str) or not object_id:
        raise FileEditError("INVALID_TARGET", "SVG target id must be a nonempty string")
    target = _index(document).get(object_id)
    if target is None:
        raise FileEditError("TARGET_NOT_FOUND", f"SVG id not found: {object_id}")
    return target


def inspect(document, object_id: str | None = None) -> dict:
    if object_id is None:
        return {"summary": summary(document)}
    target = _target(document, object_id)
    children = [child for child in target if isinstance(child.tag, str)]
    return {
        "target": {
            "id": object_id,
            "tag": _tag(target),
            "attributes": dict(target.attrib),
            "text": target.text or "",
            "children": [{"id": child.get("id"), "tag": _tag(child)} for child in children[:200]],
            "child_count": len(children),
            "children_truncated": len(children) > 200,
        }
    }


def _attribute_name(value):
    if not isinstance(value, str) or not value or ":" in value or "{" in value or "}" in value:
        raise FileEditError("INVALID_STEP", "Attribute name must be an unqualified XML name")
    try:
        etree.QName(value)
    except ValueError as exc:
        raise FileEditError("INVALID_STEP", "Attribute name must be an unqualified XML name") from exc
    if value in {"id", "xmlns"}:
        raise FileEditError("INVALID_STEP", f"SVG {value} cannot be modified or removed")
    return value


def _number(value):
    if type(value) not in (int, float):
        raise FileEditError("INVALID_STEP", "Translation dx and dy must be finite numbers")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise FileEditError("INVALID_STEP", "Translation dx and dy must be finite numbers")
    return str(value)


def edit(document, step: dict):
    if not isinstance(step, dict) or not isinstance(step.get("op"), str) or step["op"] not in OPS:
        raise FileEditError("INVALID_STEP", "Unsupported SVG operation")
    op = step["op"]
    fields = {
        "set-attribute": {"op", "id", "name", "value"},
        "remove-attribute": {"op", "id", "name"},
        "set-text": {"op", "id", "text"},
        "translate": {"op", "id", "dx", "dy"},
    }[op]
    _fields(step, fields, fields)
    target = _target(document, step["id"])
    try:
        if op in {"set-attribute", "remove-attribute"}:
            name = _attribute_name(step["name"])
            if op == "set-attribute":
                if not isinstance(step["value"], str):
                    raise FileEditError("INVALID_STEP", "SVG attribute value must be a string")
                target.set(name, step["value"])
            elif name not in target.attrib:
                raise FileEditError("TARGET_NOT_FOUND", f"SVG attribute not found: {name}")
            else:
                del target.attrib[name]
        elif op == "set-text":
            if not isinstance(step["text"], str):
                raise FileEditError("INVALID_STEP", "SVG text must be a string")
            if _tag(target) not in {"text", "tspan", "title", "desc"} or len(target):
                raise FileEditError("INVALID_TARGET", "set-text requires a leaf text, tspan, title or desc")
            target.text = step["text"]
        else:
            translation = f"translate({_number(step['dx'])} {_number(step['dy'])})"
            existing = target.get("transform", "")
            if existing.strip() and existing.strip() != "none":
                translation += " " + existing
            target.set("transform", translation)
    except (ValueError, TypeError) as exc:
        raise FileEditError("INVALID_STEP", f"Invalid SVG XML value: {exc}") from exc
    return document


def serialize(document) -> bytes:
    return etree.tostring(document, encoding="utf-8", xml_declaration=True)
