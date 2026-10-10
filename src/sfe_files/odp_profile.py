"""Native OpenDocument Presentation text and explicit geometry operations."""

from __future__ import annotations

import math
import re
from decimal import Decimal

from .odf_text import (
    NS,
    attributes,
    compact,
    paragraph_reason,
    paragraph_view,
    q,
    replace_paragraph,
    validate_text,
)
from .plan_ops import FileEditError, _fields, _pointer_parts
from .zip_document import open_odf

PACKAGE = True
OPS = {"set-text", "move", "resize"}
GEOMETRY_TAGS = {q("draw:frame"), q("draw:rect"), q("draw:ellipse")}
UNITS = {"cm": Decimal(1), "mm": Decimal("0.1"), "in": Decimal("2.54"),
         "pt": Decimal("2.54") / 72, "pc": Decimal("2.54") / 6}
CONTRACT = {
    "addressing": (
        "Slides are 1-based: /slides/1. Objects use a unique draw:name within that page: "
        "/slides/1/objects/<escaped-name>. Escape / as ~1 and ~ as ~0. Notes and master pages are excluded."
    ),
    "operations": {
        "set-text": {
            "example": {"op": "set-text", "slide": 1, "object": "title", "text": "Commissioning approved"},
            "description": "Replace one ordinary text paragraph; retain paragraph attributes and first span style.",
        },
        "move": {
            "example": {"op": "move", "slide": 1, "object": "status", "x": 2.5, "y": 5.0},
            "description": "Set x and y in centimeters for a standalone frame, rectangle or ellipse.",
        },
        "resize": {
            "example": {"op": "resize", "slide": 1, "object": "status", "width": 20, "height": 2.5},
            "description": "Set positive width and height in centimeters for the same standalone object types.",
        },
    },
    "limitations": [
        "Only explicit geometry is edited. Grouped, transformed, relative-size or unsupported shape geometry is rejected.",
        (
            "Text edits require exactly one plain text paragraph. Links, bookmarks, fields, lists, tables and "
            "other complex inline content are rejected; mixed spans collapse to the first style, visible in preview."
        ),
        "Geometry reads cm, mm, in, pt and pc; writes finite numeric centimeter values. Shape names must be unique per page.",
        "Slide masters, styles, notes, images and untouched package payloads are preserved. No slide rendering is performed.",
    ],
    "standard": "https://docs.oasis-open.org/office/OpenDocument/v1.3/os/part3-schema/OpenDocument-v1.3-os-part3-schema.html",
}


def parse(data: bytes):
    document = open_odf(data, "odp")
    if _body(document) is None:
        raise FileEditError("INVALID_ODP", "Expected office:body/office:presentation")
    return document


def _body(document):
    return document.xml("content.xml").find("office:body/office:presentation", NS)


def _slides(document):
    return _body(document).findall("draw:page", NS)


def _slide(document, number):
    if type(number) is not int or number < 1:
        raise FileEditError("INVALID_STEP", "slide must be a 1-based positive integer")
    slides = _slides(document)
    if number > len(slides):
        raise FileEditError("TARGET_NOT_FOUND", f"Slide {number} does not exist")
    return slides[number - 1]


def _objects(page):
    return [node for node in page.iterdescendants() if isinstance(node.tag, str)
            and node.tag.startswith("{" + NS["draw"] + "}") and q("draw:name") in node.attrib
            and not any(parent.tag == q("presentation:notes") for parent in node.iterancestors())]


def _object(page, name):
    if not isinstance(name, str) or not name:
        raise FileEditError("INVALID_STEP", "object must be a nonempty draw:name")
    matches = [node for node in _objects(page) if node.get(q("draw:name")) == name]
    if len(matches) != 1:
        raise FileEditError("TARGET_NOT_UNIQUE", f"Expected one object named {name}; found {len(matches)}")
    return matches[0]


def _length(value):
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+))(cm|mm|in|pt|pc)", value)
    return float(Decimal(match[1]) * UNITS[match[2]]) if match else None


def _geometry_reason(page, node):
    if node.tag not in GEOMETRY_TAGS or node.getparent() is not page:
        return "Geometry requires a standalone frame, rectangle or ellipse"
    if q("draw:transform") in node.attrib:
        return "Transformed object geometry is unsupported"
    if any(key in node.attrib for key in (q("style:rel-width"), q("style:rel-height"))):
        return "Relative object dimensions are unsupported"
    values = {name: _length(node.get(q("svg:" + name))) for name in ("x", "y", "width", "height")}
    if any(value is None or not math.isfinite(value) for value in values.values()):
        return "Geometry requires explicit x, y, width and height in supported units"
    if values["width"] <= 0 or values["height"] <= 0:
        return "Object width and height must be positive"
    return None


def _text_paragraph(node):
    if node.tag == q("draw:frame"):
        boxes = node.findall("draw:text-box", NS)
        if len(boxes) != 1 or any(child.tag not in {q("draw:text-box"), q("svg:title"), q("svg:desc")} for child in node):
            raise FileEditError("COMPLEX_TARGET", "Text edit requires an ordinary text frame")
        container = boxes[0]
    elif node.tag in {q("draw:rect"), q("draw:ellipse")}:
        container = node
    else:
        raise FileEditError("COMPLEX_TARGET", "Object has no supported text container")
    paragraphs = container.findall("text:p", NS)
    if len(paragraphs) != 1 or any(child.tag not in {q("text:p"), q("svg:title"), q("svg:desc")} for child in container):
        raise FileEditError("COMPLEX_TARGET", "set-text requires one ordinary paragraph")
    reason = paragraph_reason(paragraphs[0])
    if reason:
        raise FileEditError("COMPLEX_TARGET", reason)
    return paragraphs[0]


def _object_view(page, node):
    paragraphs = [paragraph_view(p) for p in node.iterdescendants() if p.tag in {q("text:p"), q("text:h")}]
    try:
        _text_paragraph(node)
        text_reason = None
    except FileEditError as exc:
        text_reason = str(exc)
    geometry_reason = _geometry_reason(page, node)
    return {
        "name": node.get(q("draw:name")), "type": compact(node.tag), "attributes": attributes(node),
        "text": "\n".join(p["text"] for p in paragraphs), "paragraphs": paragraphs,
        "geometry_cm": {name: _length(node.get(q("svg:" + name))) for name in ("x", "y", "width", "height")},
        "editable_text": text_reason is None, "text_reason": text_reason,
        "editable_geometry": geometry_reason is None, "geometry_reason": geometry_reason,
    }


def _slide_view(page, number):
    return {"slide": number, "name": page.get(q("draw:name")), "attributes": attributes(page),
            "objects": [_object_view(page, node) for node in _objects(page)]}


def projection(document):
    return {"units": "cm", "slides": [_slide_view(page, i) for i, page in enumerate(_slides(document), 1)]}


def summary(document):
    pages = _slides(document)
    return {"slide_count": len(pages), "units": "cm", "slides": [
        {"slide": i, "name": page.get(q("draw:name")), "objects": [node.get(q("draw:name")) for node in _objects(page)]}
        for i, page in enumerate(pages[:100], 1)
    ], "truncated": len(pages) > 100}


def inspect(document, pointer=""):
    if not isinstance(pointer, str):
        raise FileEditError("INVALID_POINTER", "pointer must be a string")
    if not pointer:
        return {"pointer": pointer, "value": summary(document)}
    parts = _pointer_parts(pointer)
    if len(parts) not in {2, 4} or parts[0] != "slides" or not re.fullmatch(r"[1-9][0-9]*", parts[1]):
        raise FileEditError("INVALID_POINTER", "Use /slides/<number> or /slides/<number>/objects/<name>")
    number = int(parts[1])
    page = _slide(document, number)
    if len(parts) == 2:
        value = _slide_view(page, number)
    elif parts[2] == "objects":
        value = _object_view(page, _object(page, parts[3]))
    else:
        raise FileEditError("INVALID_POINTER", "Use /slides/<number>/objects/<name>")
    return {"pointer": pointer, "value": value}


def _centimeters(value, positive=False):
    if type(value) not in {int, float}:
        raise FileEditError("INVALID_STEP", "Geometry values must be finite numbers in centimeters")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite or (positive and value <= 0):
        raise FileEditError("INVALID_STEP", "Geometry values must be finite; width and height must be positive")
    return format(Decimal(str(value)), "f") + "cm"


def edit(document, step):
    if not isinstance(step, dict) or not isinstance(step.get("op"), str) or step["op"] not in OPS:
        raise FileEditError("INVALID_STEP", "Unsupported ODP operation")
    op = step["op"]
    fields = {"op", "slide", "object"} | ({"text"} if op == "set-text" else {"x", "y"} if op == "move" else {"width", "height"})
    _fields(step, fields, fields)
    page = _slide(document, step["slide"])
    node = _object(page, step["object"])
    if op == "set-text":
        validate_text(step["text"])
        replace_paragraph(_text_paragraph(node), step["text"])
    else:
        reason = _geometry_reason(page, node)
        if reason:
            raise FileEditError("COMPLEX_TARGET", reason)
        names = ("x", "y") if op == "move" else ("width", "height")
        replacements = {name: _centimeters(step[name], positive=op == "resize") for name in names}
        for name, value in replacements.items():
            node.set(q("svg:" + name), value)
    document.mark_changed("content.xml")
    return document


def serialize(document):
    return document.serialize()
