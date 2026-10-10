"""Selected mxCell edits in uncompressed and compressed draw.io pages."""

from __future__ import annotations

import base64
import math
import zlib
from dataclasses import dataclass
from urllib.parse import quote, unquote

from lxml import etree

from .plan_ops import FileEditError, _fields, _pointer_parts
from .xml_document import XmlDocument, read_xml

OPS = {"set-label", "move", "resize", "connect"}
CONTRACT = {
    "addressing": "Inspect /pages/<1-based-page>/cells/<escaped-mxCell-id>; plans use page and id.",
    "operations": {
        "set-label": {"example": {"op": "set-label", "page": 1, "id": "pump", "value": "Feed pump"}},
        "move": {"example": {"op": "move", "page": 1, "id": "pump", "x": 80, "y": 120}},
        "resize": {"example": {"op": "resize", "page": 1, "id": "pump", "width": 120, "height": 60}},
        "connect": {"example": {"op": "connect", "page": 1, "id": "link", "source": "pump", "target": "tank"}},
    },
    "limitations": [
        "mxfile pages with direct mxCell objects and inline or URL-escaped raw-DEFLATE mxGraphModel payloads.",
        "Vertex geometry must use explicit absolute x/y/width/height; groups, relative geometry and wrappers are read-only.",
        "connect changes edge endpoints but does not reroute custom waypoints or validate visual layout.",
        "Unchanged pages keep their encoded payload; changed compressed pages are recompressed.",
    ],
    "reference": "https://www.drawio.com/docs/manual/advanced/diagram-source-edit/",
}


@dataclass
class Page:
    host: etree._Element | None
    graph: XmlDocument
    compressed: bool
    name: str
    dirty: bool = False


@dataclass
class Document:
    outer: XmlDocument
    pages: list[Page]


def _bad(message, code="INVALID_DRAWIO"):
    raise FileEditError(code, message)


def _inflate(value):
    if not value or len(value) > 8_000_000:
        _bad("Compressed page is empty or too large")
    try:
        encoded = base64.b64decode(value, validate=True)
        inflater = zlib.decompressobj(-15)
        plain = inflater.decompress(encoded, 16_000_001)
        if len(plain) > 16_000_000 or not inflater.eof or inflater.unconsumed_tail:
            _bad("Compressed page exceeds supported size")
        return unquote(plain.decode("utf-8")).encode("utf-8")
    except (ValueError, UnicodeError, zlib.error) as exc:
        raise FileEditError("INVALID_DRAWIO", "Cannot decode compressed diagram") from exc


def _deflate(raw):
    escaped = quote(raw.decode("utf-8"), safe="~()*!.'-").encode("utf-8")
    compressor = zlib.compressobj(level=9, wbits=-15)
    return base64.b64encode(compressor.compress(escaped) + compressor.flush()).decode("ascii")


def parse(data):
    outer = read_xml(data, code="INVALID_DRAWIO")
    root = outer.root
    pages = []
    if root.tag == "mxGraphModel":
        pages.append(Page(None, outer, False, "Page-1"))
    elif root.tag == "mxfile":
        for number, node in enumerate(root.findall("diagram"), 1):
            embedded = node.find("mxGraphModel")
            if embedded is not None:
                graph = read_xml(etree.tostring(embedded), code="INVALID_DRAWIO")
                compressed = False
            else:
                graph = read_xml(_inflate((node.text or "").strip()), code="INVALID_DRAWIO")
                compressed = True
            pages.append(Page(node, graph, compressed, node.get("name") or f"Page-{number}"))
    else:
        _bad("Expected mxfile or mxGraphModel root")
    if not pages or len(pages) > 100:
        _bad("Expected 1..100 diagram pages")
    for page in pages:
        if page.graph.root.tag != "mxGraphModel" or page.graph.root.find("root") is None:
            _bad("Page must contain mxGraphModel/root")
        _cells(page)
    return Document(outer, pages)


def _cells(page):
    cells = page.graph.root.findall("root/mxCell")
    ids = [cell.get("id") for cell in cells]
    if any(not ident for ident in ids) or len(ids) != len(set(ids)):
        _bad("Direct mxCell IDs must be unique and nonempty")
    return {cell.get("id"): cell for cell in cells}


def _page(document, number):
    if type(number) is not int or not 1 <= number <= len(document.pages):
        _bad("page must be a 1-based existing page", "TARGET_NOT_FOUND")
    return document.pages[number - 1]


def _cell(page, ident):
    if not isinstance(ident, str) or not ident:
        _bad("id must be a nonempty string", "INVALID_STEP")
    try:
        return _cells(page)[ident]
    except KeyError as exc:
        raise FileEditError("TARGET_NOT_FOUND", f"No cell with id {ident}") from exc


def _view(cell):
    geometry = cell.find("mxGeometry")
    return {"id": cell.get("id"), "value": cell.get("value"), "vertex": cell.get("vertex") == "1",
            "edge": cell.get("edge") == "1", "source": cell.get("source"), "target": cell.get("target"),
            "geometry": dict(geometry.attrib) if geometry is not None else None}


def summary(document):
    return {"page_count": len(document.pages), "pages": [
        {"number": i, "name": page.name, "cell_count": len(_cells(page))}
        for i, page in enumerate(document.pages, 1)]}


def inspect(document, pointer=""):
    parts = _pointer_parts(pointer)
    if not parts:
        return {"pointer": pointer, "value": summary(document)}
    if len(parts) != 4 or parts[0] != "pages" or parts[2] != "cells" or not parts[1].isdigit():
        _bad("Use /pages/<number>/cells/<id>", "INVALID_INSPECT")
    page = _page(document, int(parts[1]))
    return {"pointer": pointer, "value": _view(_cell(page, parts[3]))}


def projection(document):
    return {"pages": [{"name": page.name, "cells": [_view(x) for x in _cells(page).values()]}
                      for page in document.pages]}


def _number(value, *, positive=False):
    if type(value) not in {int, float} or not math.isfinite(value) or (positive and value <= 0):
        _bad("Geometry values must be finite numbers; dimensions must be positive", "INVALID_STEP")
    return str(value)


def edit(document, step):
    op = step["op"]
    field_map = {
        "set-label": {"op", "page", "id", "value"},
        "move": {"op", "page", "id", "x", "y"},
        "resize": {"op", "page", "id", "width", "height"},
        "connect": {"op", "page", "id", "source", "target"},
    }
    _fields(step, field_map[op], field_map[op])
    page = _page(document, step["page"])
    cell = _cell(page, step["id"])
    if op == "set-label":
        if cell.get("vertex") != "1" or not isinstance(step["value"], str):
            _bad("set-label needs an ordinary vertex and text", "INVALID_STEP")
        cell.set("value", step["value"])
    elif op == "connect":
        if cell.get("edge") != "1":
            _bad("connect needs an edge", "UNSUPPORTED_DRAWIO")
        for endpoint in ("source", "target"):
            other = _cell(page, step[endpoint])
            if other.get("vertex") != "1":
                _bad("Edge endpoints must be vertices", "UNSUPPORTED_DRAWIO")
            cell.set(endpoint, step[endpoint])
    else:
        geometry = cell.find("mxGeometry")
        if cell.get("vertex") != "1" or geometry is None or geometry.get("relative") == "1":
            _bad("Geometry edit needs an absolute vertex", "UNSUPPORTED_DRAWIO")
        for field in (("x", "y") if op == "move" else ("width", "height")):
            if geometry.get(field) is None:
                _bad("Geometry field must already exist", "UNSUPPORTED_DRAWIO")
            geometry.set(field, _number(step[field], positive=op == "resize"))
    page.dirty = True
    return document


def serialize(document):
    if not any(page.dirty for page in document.pages):
        return document.outer.raw
    if document.pages[0].host is None:
        return document.pages[0].graph.serialize() if document.pages[0].graph.dirty else etree.tostring(
            document.pages[0].graph.tree, encoding="UTF-8", xml_declaration=True)
    for page in document.pages:
        if not page.dirty:
            continue
        host = page.host
        raw = etree.tostring(page.graph.tree, encoding="UTF-8", xml_declaration=False)
        if page.compressed:
            host.text = _deflate(raw)
        else:
            host.replace(host.find("mxGraphModel"), page.graph.root)
    document.outer.dirty = True
    return document.outer.serialize()
