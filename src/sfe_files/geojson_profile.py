"""Feature-ID addressed edits for a bounded GeoJSON FeatureCollection subset."""

from __future__ import annotations

import json
import math

from .plan_ops import FileEditError, _fields, _json, _pointer_parts

OPS = {"set-property", "set-point"}
CONTRACT = {
    "addressing": "Inspect /features/<1-based-index> or /by-id/<escaped-string-id>; plans use id or index.",
    "operations": {
        "set-property": {"example": {"op": "set-property", "id": "pump", "name": "status", "value": "ready"}},
        "set-point": {"example": {"op": "set-point", "id": "pump", "longitude": 100.5, "latitude": 13.7}},
    },
    "limitations": [
        "UTF-8 FeatureCollection with at most 10000 features and unique string feature IDs.",
        "Index addresses are 1-based and guarded by the plan's byte hash; only Point coordinates may be edited.",
        "Point edits reject collection, feature or geometry bbox because those bounds would become stale.",
        "JSON serialization may normalize whitespace and key spacing; all coordinates use WGS84 longitude/latitude.",
    ],
    "reference": "https://www.rfc-editor.org/rfc/rfc7946",
}


def _bad(message, code="INVALID_GEOJSON"):
    raise FileEditError(code, message)


def parse(data):
    value = _json(data)
    if not isinstance(value, dict) or value.get("type") != "FeatureCollection":
        _bad("Expected a GeoJSON FeatureCollection")
    features = value.get("features")
    if not isinstance(features, list) or len(features) > 10000:
        _bad("features must be an array of at most 10000 items")
    ids = set()
    for feature in features:
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            _bad("Every item must be a Feature")
        if not isinstance(feature.get("properties"), dict):
            _bad("Feature properties must be an object")
        ident = feature.get("id")
        if ident is not None:
            if not isinstance(ident, str) or not ident or ident in ids:
                _bad("Feature IDs must be unique nonempty strings")
            ids.add(ident)
        geometry = feature.get("geometry")
        if geometry is not None and (not isinstance(geometry, dict) or not isinstance(geometry.get("type"), str)):
            _bad("Feature geometry must be an object or null")
    return value


def _feature(value, ident=None, index=None):
    if (ident is None) == (index is None):
        _bad("Provide exactly one of id or index", "INVALID_STEP")
    features = value["features"]
    if ident is not None:
        if not isinstance(ident, str) or not ident:
            _bad("id must be a nonempty string", "INVALID_STEP")
        found = [f for f in features if f.get("id") == ident]
        if len(found) != 1:
            _bad("Feature ID was not found", "TARGET_NOT_FOUND")
        return found[0]
    if type(index) is not int or not 1 <= index <= len(features):
        _bad("index must address an existing 1-based feature", "TARGET_NOT_FOUND")
    return features[index - 1]


def summary(value):
    return {"feature_count": len(value["features"]), "ids": [f.get("id") for f in value["features"][:100]]}


def inspect(value, pointer=""):
    parts = _pointer_parts(pointer)
    if not parts:
        return {"pointer": pointer, "value": summary(value)}
    if len(parts) == 2 and parts[0] == "features" and parts[1].isdigit():
        selected = _feature(value, index=int(parts[1]))
    elif len(parts) == 2 and parts[0] == "by-id":
        selected = _feature(value, ident=parts[1])
    else:
        _bad("Use /features/<index> or /by-id/<id>", "INVALID_INSPECT")
    return {"pointer": pointer, "value": selected}


def projection(value):
    return value


def edit(value, step):
    op = step["op"]
    if op == "set-property":
        _fields(step, {"op", "id", "index", "name", "value"}, {"op", "name", "value"})
    else:
        _fields(step, {"op", "id", "index", "longitude", "latitude"}, {"op", "longitude", "latitude"})
    feature = _feature(value, step.get("id"), step.get("index"))
    if op == "set-property":
        name = step["name"]
        if not isinstance(name, str) or not name:
            _bad("Property name must be a nonempty string", "INVALID_STEP")
        try:
            json.dumps(step["value"], allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise FileEditError("INVALID_STEP", "Property value must be JSON-compatible") from exc
        feature["properties"][name] = step["value"]
        return value
    longitude, latitude = step["longitude"], step["latitude"]
    if any(type(x) not in {int, float} or not math.isfinite(x) for x in (longitude, latitude)):
        _bad("Coordinates must be finite numbers", "INVALID_STEP")
    if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
        _bad("Longitude or latitude is out of WGS84 range", "INVALID_STEP")
    geometry = feature.get("geometry")
    if not isinstance(geometry, dict) or geometry.get("type") != "Point":
        _bad("set-point requires a Point feature", "UNSUPPORTED_GEOJSON")
    if any("bbox" in item for item in (value, feature, geometry)):
        _bad("Point with bbox cannot be edited without recomputing bounds", "UNSUPPORTED_GEOJSON")
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) not in {2, 3}:
        _bad("Point coordinates must have two or three numbers")
    geometry["coordinates"] = [longitude, latitude, *coordinates[2:]]
    return value


def serialize(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")
