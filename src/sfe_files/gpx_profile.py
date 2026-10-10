"""Address ordinary GPX 1.1 waypoints, route points and track points."""

from __future__ import annotations

import math
from datetime import datetime

from .plan_ops import FileEditError, _fields, _pointer_parts
from .xml_document import read_xml

NS = "http://www.topografix.com/GPX/1/1"
Q = "{" + NS + "}"
OPS = {"set-name", "set-point", "set-time"}
CONTRACT = {
    "addressing": "Pointers: /waypoints/1, /routes/1/points/2, /tracks/1/segments/1/points/2 (all 1-based).",
    "operations": {
        "set-name": {"example": {"op": "set-name", "target": "/waypoints/1", "value": "Pump station"}},
        "set-point": {"example": {"op": "set-point", "target": "/routes/1/points/2", "latitude": 13.7, "longitude": 100.5}},
        "set-time": {"example": {"op": "set-time", "target": "/tracks/1/segments/1/points/2", "value": "2026-09-27T09:00:00Z"}},
    },
    "limitations": [
        "GPX 1.1 only; point targets must already exist. No route/track creation or sorting.",
        "set-name and set-time require the corresponding existing element, so GPX schema element order remains unchanged.",
        "Track segments, extensions and unrelated metadata remain in place; XML formatting may normalize.",
    ],
    "reference": "https://www.topografix.com/gpx/1/1/",
}


def _bad(message, code="INVALID_GPX"):
    raise FileEditError(code, message)


def parse(data):
    document = read_xml(data, code="INVALID_GPX")
    if document.root.tag != Q + "gpx" or document.root.get("version") != "1.1":
        _bad("Expected GPX 1.1 root")
    return document


def _nth(items, part):
    if not part.isdigit() or int(part) < 1 or int(part) > len(items):
        _bad("Point address is out of range", "TARGET_NOT_FOUND")
    return items[int(part) - 1]


def _target(document, pointer):
    parts = _pointer_parts(pointer)
    root = document.root
    if len(parts) == 2 and parts[0] == "waypoints":
        return _nth(root.findall(Q + "wpt"), parts[1])
    if len(parts) == 4 and parts[:1] == ["routes"] and parts[2] == "points":
        route = _nth(root.findall(Q + "rte"), parts[1])
        return _nth(route.findall(Q + "rtept"), parts[3])
    if len(parts) == 6 and parts[0] == "tracks" and parts[2] == "segments" and parts[4] == "points":
        track = _nth(root.findall(Q + "trk"), parts[1])
        segment = _nth(track.findall(Q + "trkseg"), parts[3])
        return _nth(segment.findall(Q + "trkpt"), parts[5])
    _bad("Expected a waypoint, route point or track point pointer", "INVALID_INSPECT")


def _view(node):
    return {"latitude": float(node.get("lat")), "longitude": float(node.get("lon")),
            "name": node.findtext(Q + "name"), "time": node.findtext(Q + "time")}


def summary(document):
    root = document.root
    return {"waypoints": len(root.findall(Q + "wpt")), "routes": len(root.findall(Q + "rte")),
            "tracks": len(root.findall(Q + "trk"))}


def inspect(document, pointer=""):
    return {"pointer": pointer, "value": _view(_target(document, pointer)) if pointer else summary(document)}


def projection(document):
    root = document.root
    return {
        "waypoints": [_view(x) for x in root.findall(Q + "wpt")],
        "routes": [[_view(x) for x in route.findall(Q + "rtept")] for route in root.findall(Q + "rte")],
        "tracks": [[[_view(x) for x in segment.findall(Q + "trkpt")]
                    for segment in track.findall(Q + "trkseg")] for track in root.findall(Q + "trk")],
    }


def edit(document, step):
    op = step["op"]
    fields = {"op", "target", "latitude", "longitude"} if op == "set-point" else {"op", "target", "value"}
    _fields(step, fields, fields)
    node = _target(document, step["target"])
    if op == "set-point":
        lat, lon = step["latitude"], step["longitude"]
        if any(type(x) not in {int, float} or not math.isfinite(x) for x in (lat, lon)):
            _bad("Coordinates must be finite numbers", "INVALID_STEP")
        if not -90 <= lat <= 90 or not -180 <= lon <= 180:
            _bad("Coordinates are outside GPX latitude/longitude bounds", "INVALID_STEP")
        node.set("lat", str(lat))
        node.set("lon", str(lon))
    else:
        value = step["value"]
        if not isinstance(value, str) or not value:
            _bad("value must be a nonempty string", "INVALID_STEP")
        child = node.find(Q + ("name" if op == "set-name" else "time"))
        if child is None:
            _bad("Target does not have this existing field", "TARGET_NOT_FOUND")
        if op == "set-time":
            try:
                stamp = datetime.fromisoformat(value)
            except ValueError as exc:
                raise FileEditError("INVALID_STEP", "time must be an ISO 8601 timestamp") from exc
            if stamp.tzinfo is None:
                _bad("time must include a timezone", "INVALID_STEP")
        child.text = value
    document.dirty = True
    return document


def serialize(document):
    return document.serialize()
