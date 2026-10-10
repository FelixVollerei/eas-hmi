"""Edit standalone PowerPoint shapes through their OOXML parts."""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass

from lxml import etree

from .ooxml import Package
from .plan_ops import FileEditError, _fields

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS = {"p": P, "a": A, "r": R}
OPS = {"set-text", "move", "resize"}
CONTRACT = {
    "addressing": (
        "Slides are one-based in presentation slide-list order, resolved through relationships. "
        "shape_id is the slide-local cNvPr integer id, not a shape index. "
        "Inspect /slides/1 or /slides/1/shapes/2 with --pointer."
    ),
    "units": "EMU integers: 914400 EMU = 1 inch; 12700 EMU = 1 point.",
    "operations": {
        "set-text": {
            "example": {"op": "set-text", "slide": 1, "shape_id": 2, "text": "Status: Ready"},
            "description": "Replace one plain paragraph, preserving its paragraph and first-run properties.",
        },
        "move": {
            "example": {"op": "move", "slide": 1, "shape_id": 2, "x": 914400, "y": 2286000},
            "description": "Set the stored shape offset in slide EMU coordinates; existing rotation is retained.",
        },
        "resize": {
            "example": {"op": "resize", "slide": 1, "shape_id": 2, "width": 8229600, "height": 1371600},
            "description": "Set positive width and height in EMU; image crop and all other properties remain.",
        },
    },
    "limitations": [
        "Supports Transitional PPTX. Legacy PPT, Strict OOXML, encrypted, signed and macro packages are unsupported.",
        "Geometry edits require standalone p:sp or p:pic with explicit a:xfrm offset and extent values.",
        "Grouped shapes, charts, tables, connectors and geometry inherited from slide layouts are inspectable only.",
        "set-text accepts one paragraph with uniformly styled plain runs; it rejects line breaks in new text.",
        "Fields, hyperlinks, mixed run styles, multiple paragraphs and complex text are rejected for set-text.",
        "Theme, master, layout, notes, media and other untouched ZIP member payloads are preserved byte-for-byte.",
        "No font layout, overflow, animation, PowerPoint recalculation or native PowerPoint compatibility verification.",
        "Resizing a picture can change its aspect ratio; resizing text does not reflow or auto-fit it here.",
    ],
    "references": [
        "https://learn.microsoft.com/en-us/office/open-xml/presentation/working-with-presentation-slides",
        "https://learn.microsoft.com/en-us/office/open-xml/presentation/how-to-get-all-the-text-in-a-slide-in-a-presentation",
    ],
}


@dataclass
class Document:
    package: Package
    slides: list[str]


def _integer(value, label, *, positive=False):
    if type(value) is not int or (positive and value <= 0):
        raise FileEditError("INVALID_STEP", f"{label} must be {'a positive' if positive else 'an'} integer")
    return value


def _xml_integer(value, label):
    if value is None or not re.fullmatch(r"-?[0-9]+", value):
        raise FileEditError("INVALID_PPTX", f"Invalid integer {label}: {value}")
    return int(value)


def parse(data: bytes) -> Document:
    package = Package.open(data, "pptx")
    presentation = package.xml(package.main_part)
    if presentation.tag != f"{{{P}}}presentation":
        raise FileEditError("INVALID_PPTX", "Expected a Transitional PresentationML presentation")
    related = package.related(package.main_part)
    slides = []
    ids = set()
    for item in presentation.findall("p:sldIdLst/p:sldId", NS):
        slide_id = _xml_integer(item.get("id"), "slide id")
        relationship = item.get(f"{{{R}}}id")
        part = related.get(relationship)
        if slide_id <= 0 or part is None or part in slides or slide_id in ids:
            raise FileEditError(
                "INVALID_PPTX", "Slide relationship is missing or slide id/part is duplicated"
            )
        ids.add(slide_id)
        root = package.xml(part)
        if root.tag != f"{{{P}}}sld" or root.find("p:cSld/p:spTree", NS) is None:
            raise FileEditError("INVALID_PPTX", "Expected a slide with a shape tree")
        shape_ids = set()
        for props in root.findall(".//p:cNvPr", NS):
            shape_id = _xml_integer(props.get("id"), "shape id")
            if shape_id <= 0 or shape_id in shape_ids:
                raise FileEditError("INVALID_PPTX", "Shape ids must be positive and unique within each slide")
            shape_ids.add(shape_id)
        slides.append(part)
    return Document(package, slides)


def _slide(document, number):
    _integer(number, "slide", positive=True)
    if number > len(document.slides):
        raise FileEditError("TARGET_NOT_FOUND", f"Slide not found: {number}")
    part = document.slides[number - 1]
    return part, document.package.xml(part)


def _shapes(root):
    return root.xpath(".//p:sp | .//p:pic | .//p:grpSp | .//p:graphicFrame | .//p:cxnSp", namespaces=NS)


def _props(shape):
    matches = shape.xpath("./*/p:cNvPr", namespaces=NS)
    if len(matches) != 1:
        raise FileEditError("INVALID_PPTX", "Shape must have exactly one cNvPr")
    return matches[0]


def _standalone(shape):
    return shape.tag in {f"{{{P}}}sp", f"{{{P}}}pic"} and shape.getparent().tag == f"{{{P}}}spTree"


def _geometry(shape):
    xfrm = shape.find("p:spPr/a:xfrm", NS)
    if xfrm is None:
        xfrm = shape.find("p:xfrm", NS)
    if xfrm is None:
        return None
    offset, extent = xfrm.find("a:off", NS), xfrm.find("a:ext", NS)
    if offset is None or extent is None:
        return None
    return {
        "x": _xml_integer(offset.get("x"), "x"),
        "y": _xml_integer(offset.get("y"), "y"),
        "width": _xml_integer(extent.get("cx"), "width"),
        "height": _xml_integer(extent.get("cy"), "height"),
    }


def _plain_text_parts(shape):
    if not _standalone(shape) or shape.tag != f"{{{P}}}sp":
        raise FileEditError("UNSUPPORTED_TARGET", "set-text requires a standalone text shape")
    body = shape.find("p:txBody", NS)
    if body is None or body.xpath(".//a:hlinkClick | .//a:hlinkMouseOver", namespaces=NS):
        raise FileEditError("UNSUPPORTED_TARGET", "Text body is absent or contains hyperlinks")
    paragraphs = body.findall("a:p", NS)
    if len(paragraphs) != 1:
        raise FileEditError("UNSUPPORTED_TARGET", "set-text requires exactly one plain paragraph")
    paragraph = paragraphs[0]
    if any(child.tag not in {f"{{{A}}}pPr", f"{{{A}}}r", f"{{{A}}}endParaRPr"} for child in paragraph):
        raise FileEditError("UNSUPPORTED_TARGET", "Fields, breaks and complex text are unsupported")
    runs = paragraph.findall("a:r", NS)
    style = None
    for index, run in enumerate(runs):
        if (
            any(child.tag not in {f"{{{A}}}rPr", f"{{{A}}}t"} for child in run)
            or len(run.findall("a:t", NS)) != 1
        ):
            raise FileEditError("UNSUPPORTED_TARGET", "Complex text run is unsupported")
        props = run.find("a:rPr", NS)
        encoded = etree.tostring(props, method="c14n") if props is not None else None
        if index and encoded != style:
            raise FileEditError("UNSUPPORTED_TARGET", "Mixed text run styles are unsupported")
        style = encoded
    return body, paragraph, runs


def _shape_view(shape):
    props = _props(shape)
    paragraphs = shape.findall("p:txBody/a:p", NS)
    text = "\n".join(
        "".join(
            "\n" if node.tag == f"{{{A}}}br" else node.text or ""
            for node in p.iter()
            if node.tag in {f"{{{A}}}t", f"{{{A}}}br"}
        )
        for p in paragraphs
    )
    try:
        _plain_text_parts(shape)
        editable_text = True
    except FileEditError:
        editable_text = False
    geometry = _geometry(shape)
    return {
        "shape_id": _xml_integer(props.get("id"), "shape id"),
        "name": props.get("name", ""),
        "type": etree.QName(shape).localname,
        "text": text,
        **(geometry or {"x": None, "y": None, "width": None, "height": None}),
        "grouped": any(parent.tag == f"{{{P}}}grpSp" for parent in shape.iterancestors()),
        "editable_text": editable_text,
        "editable_geometry": _standalone(shape) and geometry is not None,
    }


def _slide_view(document, number):
    part, root = _slide(document, number)
    return {"slide": number, "part": part, "shapes": [_shape_view(shape) for shape in _shapes(root)]}


def projection(document) -> dict:
    size = document.package.xml(document.package.main_part).find("p:sldSz", NS)
    return {
        "units": "EMU",
        "slide_size": {
            "width": _xml_integer(size.get("cx"), "slide width") if size is not None else None,
            "height": _xml_integer(size.get("cy"), "slide height") if size is not None else None,
        },
        "slides": [_slide_view(document, number) for number in range(1, len(document.slides) + 1)],
    }


def summary(document) -> dict:
    slides = []
    for number in range(1, min(len(document.slides), 100) + 1):
        view = _slide_view(document, number)
        slides.append(
            {
                "slide": number,
                "shape_count": len(view["shapes"]),
                "text": "\n".join(shape["text"] for shape in view["shapes"] if shape["text"])[:500],
            }
        )
    size = document.package.xml(document.package.main_part).find("p:sldSz", NS)
    return {
        "units": "EMU",
        "slide_count": len(document.slides),
        "slides": slides,
        "slide_size": {
            "width": _xml_integer(size.get("cx"), "slide width") if size is not None else None,
            "height": _xml_integer(size.get("cy"), "slide height") if size is not None else None,
        },
        "truncated": len(document.slides) > 100,
    }


def inspect(document, pointer: str = "") -> dict:
    if pointer == "":
        return {"summary": summary(document)}
    match = re.fullmatch(r"/slides/([1-9][0-9]*)(?:/shapes/([1-9][0-9]*))?", pointer)
    if match is None:
        raise FileEditError("INVALID_POINTER", "Use /slides/1 or /slides/1/shapes/2")
    slide = _slide_view(document, int(match.group(1)))
    if match.group(2) is None:
        return {"pointer": pointer, "value": slide}
    for shape in slide["shapes"]:
        if shape["shape_id"] == int(match.group(2)):
            return {"pointer": pointer, "value": shape}
    raise FileEditError("TARGET_NOT_FOUND", f"Shape not found: {match.group(2)}")


def edit(document, step: dict):
    if not isinstance(step, dict) or not isinstance(step.get("op"), str) or step["op"] not in OPS:
        raise FileEditError("INVALID_STEP", "Unsupported PPTX operation")
    op = step["op"]
    fields = {"op", "slide", "shape_id"} | {
        "set-text": {"text"},
        "move": {"x", "y"},
        "resize": {"width", "height"},
    }[op]
    _fields(step, fields, fields)
    shape_id = _integer(step["shape_id"], "shape_id", positive=True)
    part, root = _slide(document, step["slide"])
    matches = [
        shape for shape in _shapes(root) if _xml_integer(_props(shape).get("id"), "shape id") == shape_id
    ]
    if len(matches) != 1:
        raise FileEditError("TARGET_NOT_FOUND", f"Shape not found: {shape_id}")
    shape = matches[0]
    if op == "set-text":
        text = step["text"]
        if not isinstance(text, str) or "\n" in text or "\r" in text:
            raise FileEditError(
                "INVALID_STEP", "text must be a string containing one paragraph without line breaks"
            )
        body, paragraph, runs = _plain_text_parts(shape)
        replacement = deepcopy(paragraph)
        for run in replacement.findall("a:r", NS):
            replacement.remove(run)
        new_run = etree.Element(f"{{{A}}}r")
        if runs and runs[0].find("a:rPr", NS) is not None:
            new_run.append(deepcopy(runs[0].find("a:rPr", NS)))
        node = etree.SubElement(new_run, f"{{{A}}}t")
        try:
            node.text = text
        except ValueError as exc:
            raise FileEditError("INVALID_STEP", "text contains invalid XML characters") from exc
        if text != text.strip():
            node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        end = replacement.find("a:endParaRPr", NS)
        replacement.insert(list(replacement).index(end) if end is not None else len(replacement), new_run)
        body.replace(paragraph, replacement)
    else:
        if not _standalone(shape) or _geometry(shape) is None:
            raise FileEditError(
                "UNSUPPORTED_TARGET", "Geometry requires standalone shape/picture with explicit transform"
            )
        attrs = ("x", "y") if op == "move" else ("width", "height")
        values = [_integer(step[name], name, positive=op == "resize") for name in attrs]
        xfrm = shape.find("p:spPr/a:xfrm", NS)
        target = xfrm.find("a:off" if op == "move" else "a:ext", NS)
        for name, value in zip(("x", "y") if op == "move" else ("cx", "cy"), values):
            target.set(name, str(value))
    document.package.mark_changed(part)
    return document


def serialize(document) -> bytes:
    return document.package.serialize()
