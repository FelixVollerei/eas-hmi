"""Unique Placemark-ID edits for simple KML 2.2 point features."""

from __future__ import annotations

import math

from .plan_ops import FileEditError, _fields, _pointer_parts
from .xml_document import read_xml

NS = "http://www.opengis.net/kml/2.2"
Q = "{" + NS + "}"
OPS = {"set-name", "set-description", "set-point"}
CONTRACT = {
    "addressing": "Inspect /placemarks/<escaped-id>; ids must be unique KML Placemark id attributes.",
    "operations": {
        "set-name": {"example": {"op": "set-name", "id": "pump", "value": "Feed pump"}},
        "set-description": {"example": {"op": "set-description", "id": "pump", "value": "Reviewed"}},
        "set-point": {"example": {"op": "set-point", "id": "pump", "longitude": 100.5, "latitude": 13.7}},
    },
    "limitations": [
        "KML 2.2 only; edit existing named/description fields and a direct ordinary Point geometry.",
        "Complex MultiGeometry, gx extensions, shared styles, camera/view and altitude mode are not edited.",
        "Point edits reject Region/LatLonBox or other bounds; external resources are neither fetched nor modified.",
    ],
    "reference": "https://www.ogc.org/standards/kml/",
}


def _bad(message, code="INVALID_KML"):
    raise FileEditError(code, message)


def parse(data):
    document = read_xml(data, code="INVALID_KML")
    if document.root.tag != Q + "kml":
        _bad("Expected KML 2.2 root")
    ids = [node.get("id") for node in document.root.iter(Q + "Placemark")]
    if any(not x for x in ids) or len(ids) != len(set(ids)):
        _bad("Placemarks must have unique nonempty ids")
    return document


def _mark(document, ident):
    if not isinstance(ident, str) or not ident:
        _bad("id must be a nonempty string", "INVALID_STEP")
    found = [x for x in document.root.iter(Q + "Placemark") if x.get("id") == ident]
    if len(found) != 1:
        _bad("Placemark ID not found", "TARGET_NOT_FOUND")
    return found[0]


def _view(node):
    point = node.find(Q + "Point")
    return {"id": node.get("id"), "name": node.findtext(Q + "name"),
            "description": node.findtext(Q + "description"),
            "coordinates": point.findtext(Q + "coordinates") if point is not None else None}


def summary(document):
    return {"placemark_count": len(list(document.root.iter(Q + "Placemark"))),
            "ids": [x.get("id") for x in document.root.iter(Q + "Placemark")][:100]}


def inspect(document, pointer=""):
    if not pointer:
        return {"pointer": pointer, "value": summary(document)}
    parts = _pointer_parts(pointer)
    if len(parts) != 2 or parts[0] != "placemarks":
        _bad("Use /placemarks/<id>", "INVALID_INSPECT")
    return {"pointer": pointer, "value": _view(_mark(document, parts[1]))}


def projection(document):
    return {"placemarks": [_view(x) for x in document.root.iter(Q + "Placemark")]}


def edit(document, step):
    op = step["op"]
    fields = {"op", "id", "longitude", "latitude"} if op == "set-point" else {"op", "id", "value"}
    _fields(step, fields, fields)
    node = _mark(document, step["id"])
    if op == "set-point":
        if node.find(Q + "MultiGeometry") is not None or node.find(".//" + Q + "Region") is not None:
            _bad("Complex geometry or bounds are unsupported", "UNSUPPORTED_KML")
        point = node.find(Q + "Point")
        if point is None or point.find(Q + "coordinates") is None:
            _bad("set-point needs one direct Point", "UNSUPPORTED_KML")
        lat, lon = step["latitude"], step["longitude"]
        if any(type(x) not in {int, float} or not math.isfinite(x) for x in (lat, lon)):
            _bad("Coordinates must be finite numbers", "INVALID_STEP")
        if not -90 <= lat <= 90 or not -180 <= lon <= 180:
            _bad("Coordinates outside longitude/latitude bounds", "INVALID_STEP")
        coords = point.find(Q + "coordinates")
        original = (coords.text or "").strip().split(",")
        if len(original) not in {2, 3} or any(" " in part for part in original):
            _bad("Only one Point coordinate tuple is supported", "UNSUPPORTED_KML")
        coords.text = ",".join([str(lon), str(lat), *original[2:]])
    else:
        value = step["value"]
        if not isinstance(value, str):
            _bad("value must be text", "INVALID_STEP")
        field = node.find(Q + ("name" if op == "set-name" else "description"))
        if field is None:
            _bad("Target field must already exist", "TARGET_NOT_FOUND")
        if len(field):
            _bad("Markup inside target field is unsupported", "UNSUPPORTED_KML")
        field.text = value
    document.dirty = True
    return document


def serialize(document):
    return document.serialize()
