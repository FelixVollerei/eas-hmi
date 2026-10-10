"""Modelspace handle edits for ordinary ASCII DXF 2D entities."""

from __future__ import annotations

import io
import math
import re
from dataclasses import dataclass

import ezdxf
from ezdxf import DXFStructureError

from .plan_ops import FileEditError, _fields, _pointer_parts

OPS = {"set-text", "translate", "set-layer"}
CONTRACT = {
    "addressing": "Inspect /entities/<hex-handle>; only modelspace LINE, TEXT and LWPOLYLINE entities are editable.",
    "operations": {
        "set-text": {"example": {"op": "set-text", "handle": "32", "value": "Pump ready"}},
        "translate": {"example": {"op": "translate", "handle": "31", "dx": 10, "dy": 5}},
        "set-layer": {"example": {"op": "set-layer", "handle": "31", "layer": "REVIEWED"}},
    },
    "limitations": [
        "ASCII DXF only; modelspace LINE, TEXT and LWPOLYLINE. DWG, binary DXF and block content are not edited.",
        "translate applies a 2D offset to supported geometry; bulges and closed polyline flags remain unchanged.",
        "set-layer requires an existing layer; text replacement uses DXF TEXT content, not rich MTEXT.",
        "The library rewrites the DXF text; unchanged entities retain semantic values, not original byte layout.",
    ],
    "reference": "https://help.autodesk.com/view/OARX/2023/ENU/?guid=GUID-235B22E0-A567-4CF6-92D3-38A2306D73F3",
}


@dataclass
class Document:
    raw: bytes
    drawing: ezdxf.document.Drawing
    dirty: bool = False


def _bad(message, code="INVALID_DXF"):
    raise FileEditError(code, message)


def parse(data):
    if b"\x00" in data[:1024]:
        _bad("Binary DXF is unsupported", "UNSUPPORTED_DXF")
    try:
        text = data.decode("utf-8-sig").replace("\r\n", "\n")
        drawing = ezdxf.read(io.StringIO(text))
        if drawing.audit().has_errors:
            _bad("DXF audit found structural errors")
        created = re.search(r"\$TDCREATE\s*\n\s*40\s*\n([0-9.]+)", text)
        drawing.header["$TDCREATE"] = float(created[1]) if created else 2451545.0
        return Document(data, drawing)
    except (UnicodeError, DXFStructureError, ValueError) as exc:
        raise FileEditError("INVALID_DXF", str(exc)) from exc


def _entities(document):
    return {entity.dxf.handle: entity for entity in document.drawing.modelspace()
            if entity.dxftype() in {"LINE", "TEXT", "LWPOLYLINE"}}


def _entity(document, handle):
    if not isinstance(handle, str) or not handle:
        _bad("handle must be a hex string", "INVALID_STEP")
    try:
        return _entities(document)[handle.upper()]
    except KeyError as exc:
        raise FileEditError("TARGET_NOT_FOUND", f"Supported modelspace handle {handle} not found") from exc


def _view(entity):
    common = {"handle": entity.dxf.handle, "type": entity.dxftype(), "layer": entity.dxf.layer}
    if entity.dxftype() == "LINE":
        common["start"] = list(entity.dxf.start)[:2]
        common["end"] = list(entity.dxf.end)[:2]
    elif entity.dxftype() == "TEXT":
        common["text"] = entity.dxf.text
        common["insert"] = list(entity.dxf.insert)[:2]
    else:
        common["points"] = [[x, y, bulge] for x, y, _start, _end, bulge in entity.get_points()]
        common["closed"] = entity.closed
    return common


def summary(document):
    entities = _entities(document)
    return {"entity_count": len(entities), "handles": list(entities)[:100],
            "layers": [layer.dxf.name for layer in document.drawing.layers]}


def inspect(document, pointer=""):
    parts = _pointer_parts(pointer)
    if not parts:
        return {"pointer": pointer, "value": summary(document)}
    if len(parts) != 2 or parts[0] != "entities":
        _bad("Use /entities/<handle>", "INVALID_INSPECT")
    return {"pointer": pointer, "value": _view(_entity(document, parts[1]))}


def projection(document):
    return {"entities": [_view(entity) for entity in _entities(document).values()]}


def _finite(value):
    if type(value) not in {int, float} or not math.isfinite(value):
        _bad("Offset must be a finite number", "INVALID_STEP")
    return value


def edit(document, step):
    op = step["op"]
    fields = {"set-text": {"op", "handle", "value"}, "translate": {"op", "handle", "dx", "dy"},
              "set-layer": {"op", "handle", "layer"}}[op]
    _fields(step, fields, fields)
    entity = _entity(document, step["handle"])
    if op == "set-text":
        if entity.dxftype() != "TEXT" or not isinstance(step["value"], str) or "\n" in step["value"]:
            _bad("set-text requires TEXT and a single-line value", "INVALID_STEP")
        entity.dxf.text = step["value"]
    elif op == "set-layer":
        layer = step["layer"]
        if not isinstance(layer, str) or layer not in document.drawing.layers:
            _bad("layer must already exist", "INVALID_STEP")
        entity.dxf.layer = layer
    else:
        dx, dy = _finite(step["dx"]), _finite(step["dy"])
        if entity.dxftype() == "LINE":
            for name in ("start", "end"):
                x, y, z = getattr(entity.dxf, name)
                setattr(entity.dxf, name, (x + dx, y + dy, z))
        elif entity.dxftype() == "TEXT":
            x, y, z = entity.dxf.insert
            entity.dxf.insert = (x + dx, y + dy, z)
        else:
            entity.set_points([(x + dx, y + dy, a, b, bulge)
                               for x, y, a, b, bulge in entity.get_points()], format="xyseb")
    document.dirty = True
    return document


def serialize(document):
    if not document.dirty:
        return document.raw
    output = io.StringIO()
    # ezdxf refreshes GUID/timestamp metadata on every write; keep source metadata
    # so preview and apply produce the same bytes for the same base and plan.
    document.drawing._update_metadata = lambda: None
    document.drawing.write(output)
    return output.getvalue().encode("utf-8")
