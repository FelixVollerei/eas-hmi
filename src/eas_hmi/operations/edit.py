import json

from ..errors import EngineeringError
from ..model import Binding
from ..query import Query

EDITABLE = {
    "x",
    "y",
    "width",
    "height",
    "rotation",
    "z_index",
    "visible",
    "locked",
    "text",
    "equipment_ref",
    "template_ref",
    "style",
    "metadata",
    "source_ref",
    "target_ref",
}


def parse_value(value):
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return value


def targets(project, ids):
    if not ids or len(ids) != len(set(ids)):
        raise EngineeringError("INVALID_SELECTION", "Select at least one node, without duplicate IDs")
    query = Query(project)
    nodes = [query.node(object_id) for object_id in ids]
    if any(n.locked for n in nodes):
        raise EngineeringError("NODE_LOCKED", "Selection contains locked nodes")
    return nodes


def set_property(project, ids, property_name, value):
    if not ids or len(ids) != len(set(ids)):
        raise EngineeringError("INVALID_SELECTION", "Select at least one node, without duplicate IDs")
    root, _, subkey = property_name.partition(".")
    if root not in EDITABLE or (subkey and root not in ("metadata", "style")):
        raise EngineeringError("PROPERTY_NOT_EDITABLE", f"Property is not editable: {property_name}")
    # Unlock is intentionally allowed through the same audited command.
    nodes = [Query(project).node(i) for i in ids] if property_name == "locked" else targets(project, ids)
    for node in nodes:
        if subkey:
            updated = dict(getattr(node, root))
            updated[subkey] = value
            setattr(node, root, updated)
        else:
            setattr(node, root, value)


def move(project, ids, dx=0, dy=0):
    for node in targets(project, ids):
        node.x += dx
        node.y += dy


def resize(project, ids, width=None, height=None):
    if width is None and height is None:
        raise EngineeringError("INVALID_RESIZE", "Specify width or height")
    for node in targets(project, ids):
        if width is not None:
            node.width = width
        if height is not None:
            node.height = height


def bind(project, object_id, role, point_ref, expression=None):
    node = targets(project, [object_id])[0]
    if point_ref not in Query(project).points:
        raise EngineeringError("INVALID_POINT_REFERENCE", f"Point not found: {point_ref}")
    existing = next((b for b in node.bindings if b.role == role), None)
    replacement = Binding(
        role=role,
        point_ref=point_ref,
        expression=expression,
        metadata=dict(existing.metadata) if existing else {},
    )
    node.bindings = (
        [replacement if b.role == role else b for b in node.bindings]
        if existing
        else [*node.bindings, replacement]
    )


def unbind(project, object_id, role):
    node = targets(project, [object_id])[0]
    node.bindings = [b for b in node.bindings if b.role != role]


def layout_nodes(project, ids):
    nodes = targets(project, ids)
    if len({(n.page_id, n.parent_id) for n in nodes}) != 1:
        raise EngineeringError("MIXED_COORDINATE_FRAMES", "Layout nodes must share a page and parent")
    if any(abs(n.rotation % 360) > 1e-8 for n in nodes):
        raise EngineeringError("UNSUPPORTED_LAYOUT", "Align/distribute requires unrotated nodes")
    return nodes


def align(project, ids, mode):
    nodes = layout_nodes(project, ids)
    if mode not in ("left", "center-x", "top", "center-y"):
        raise EngineeringError("INVALID_ALIGN", f"Unsupported mode {mode}")
    axis, size = ("x", "width") if mode in ("left", "center-x") else ("y", "height")
    center = mode.startswith("center")
    start = min(getattr(n, axis) for n in nodes)
    end = max(getattr(n, axis) + getattr(n, size) for n in nodes)
    for node in nodes:
        setattr(node, axis, (start + end - getattr(node, size)) / 2 if center else start)


def distribute(project, ids, axis, gap, columns=None):
    nodes = layout_nodes(project, ids)
    if axis not in ("x", "y") or gap < 0 or (columns is not None and columns < 1):
        raise EngineeringError("INVALID_DISTRIBUTE", "Axis must be x/y, gap nonnegative, columns positive")
    nodes.sort(key=lambda n: (getattr(n, axis), n.id))
    if columns:
        nodes.sort(key=lambda n: n.id)
        x0, y0 = min(n.x for n in nodes), min(n.y for n in nodes)
        cell_w, cell_h = max(n.width for n in nodes), max(n.height for n in nodes)
        for i, node in enumerate(nodes):
            node.x = x0 + (i % columns) * (cell_w + gap)
            node.y = y0 + (i // columns) * (cell_h + gap)
    else:
        cursor = min(getattr(n, axis) for n in nodes)
        for node in nodes:
            setattr(node, axis, cursor)
            cursor += getattr(node, "width" if axis == "x" else "height") + gap


def batch(project, operation, selector, **values):
    """Resolve the AND selector on the transaction candidate, under its lock."""
    ids = [n.id for n in Query(project).select(**selector)]
    if operation == "move":
        move(project, ids, values.get("dx", 0), values.get("dy", 0))
    elif operation == "set":
        set_property(project, ids, values["property_name"], values["value"])
    else:
        raise EngineeringError("INVALID_BATCH_OPERATION", "Batch supports move and set only")
