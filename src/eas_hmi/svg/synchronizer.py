"""Plan supported human edits without writing storage or mutating the source model.

The complete edited tree must be accounted for: after projecting supported changes,
its neutralized tree must equal the renderer baseline. Unknown edits cannot vanish.
"""

import math
from copy import deepcopy
from dataclasses import dataclass

from lxml import etree

from ..errors import EngineeringError
from ..geometry import decompose, multiply, node_matrix, parse_transform, scaling, translation
from ..model import Project, canonical_hash
from ..operations.history import semantic_diff
from ..validation import validate
from .common import INKSCAPE, css_declarations, length, safe_parse, signature, tag
from .opaque import restore_payload
from .renderer import render_tree

SODIPODI = "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd"


def unsupported(node, attribute, message):
    return EngineeringError(
        "UNSUPPORTED_HUMAN_EDIT",
        f"UNSUPPORTED HUMAN EDIT node={node} attribute={attribute}: {message}",
        {"node": node, "attribute": attribute},
    )


@dataclass
class SyncPlan:
    candidate: Project
    changes: list
    revision: int
    baseline_hash: str

    def apply(self, current):
        if current.revision != self.revision or canonical_hash(current) != self.baseline_hash:
            raise EngineeringError("REVISION_CONFLICT", "Engineering state changed after sync planning")
        return self.candidate.model_copy(deep=True)


def identity_map(root):
    result = {}
    for e in root.iter():
        id = e.get("data-eas-id") if isinstance(e.tag, str) else None
        if id is not None:
            if id in result:
                raise unsupported(id, "data-eas-id", "Duplicate semantic ID")
            result[id] = e
    return result


def clear_editor_metadata(root):
    # Only known non-rendering editor bookkeeping is ignored. Artwork metadata
    # inside opaque is preserved and is compared separately.
    for e in list(root.iter()):
        if not isinstance(e.tag, str):
            continue
        if any(a.get("data-eas-part") == "opaque" for a in e.iterancestors()):
            continue
        if e.tag in (f"{{{SODIPODI}}}namedview", tag("metadata")) or (e.tag == tag("defs") and len(e) == 0):
            if e.getparent() is not None:
                e.getparent().remove(e)
            continue
        for key in list(e.attrib):
            ns = etree.QName(key).namespace
            if ns in (INKSCAPE, SODIPODI):
                e.attrib.pop(key)
        if not e.get("data-eas-id") and e.get("data-eas-part") != "opaque":
            e.attrib.pop("id", None)


def close_matrix(a, b):
    return all(math.isclose(x, y, rel_tol=1e-10, abs_tol=1e-9) for x, y in zip(a, b))


def frame_of(element):
    frames = [e for e in element if e.tag == tag("svg") and e.get("data-eas-part") == "frame"]
    if len(frames) != 1:
        raise unsupported(element.get("data-eas-id"), "frame", "Expected exactly one intrinsic viewport")
    return frames[0]


def parent_identity(element):
    return next((a.get("data-eas-id") for a in element.iterancestors() if a.get("data-eas-id")), None)


def plain_text(element):
    # One logical line, optionally represented by unstyled tspans. Positional
    # resets to the text origin are harmless; any other spacing/style is rejected.
    output = [element.text or ""]
    spans = list(element)
    for span in spans:
        if span.tag != tag("tspan") or len(span):
            raise EngineeringError(
                "UNSUPPORTED_HUMAN_EDIT", "Only plain text and simple tspans are supported"
            )
        for key, value in span.attrib.items():
            if key == "id" or etree.QName(key).namespace in (INKSCAPE, SODIPODI):
                continue
            if (
                key in ("x", "y")
                and len(spans) == 1
                and not (element.text or "").strip()
                and length(value) == length(element.get(key, "0"))
            ):
                continue
            raise EngineeringError("UNSUPPORTED_HUMAN_EDIT", "Positioned or styled tspan is unsupported")
        output.extend([span.text or "", span.tail or ""])
    text = "".join(output)
    if "\n" in text or "\r" in text:
        raise EngineeringError("UNSUPPORTED_HUMAN_EDIT", "Multiline text needs an explicit text layout model")
    return text


def plan_sync(project, data):
    root = safe_parse(data)
    page_id = root.get("data-eas-page")
    if root.get("data-eas-project") != project.id:
        raise EngineeringError("PROJECT_MISMATCH", "SVG belongs to another project")
    if root.get("data-eas-revision") != str(project.revision):
        raise EngineeringError("REVISION_CONFLICT", "SVG render revision is stale or invalid")
    baseline = render_tree(project, page_id)
    base_map, edit_map = identity_map(baseline), identity_map(root)
    if base_map.keys() != edit_map.keys():
        missing, added = sorted(base_map.keys() - edit_map.keys()), sorted(edit_map.keys() - base_map.keys())
        raise unsupported((missing or added)[0], "data-eas-id", f"Missing IDs {missing}; new IDs {added}")
    xml_ids = [e.get("id") for e in root.iter() if isinstance(e.tag, str) and e.get("id")]
    if len(xml_ids) != len(set(xml_ids)):
        raise unsupported("document", "id", "Duplicate XML ID")
    candidate = project.model_copy(deep=True)
    nodes = {n.id: n for n in candidate.all_nodes()}

    for id in sorted(base_map):
        expected, edited, node = base_map[id], edit_map[id], nodes[id]
        if edited.tag != expected.tag:
            raise unsupported(id, "element", "Semantic viewport type changed")
        expected_frame, edited_frame = frame_of(expected), frame_of(edited)
        expected_parent = parent_identity(expected)
        actual_parent = parent_identity(edited)
        if expected_parent != actual_parent:
            raise unsupported(id, "parent", "Reparenting is not supported")
        if edited_frame.get("viewBox") != expected_frame.get("viewBox"):
            raise unsupported(id, "viewBox", "Intrinsic content frame cannot be changed by SVG sync")
        editable_attrs = ("transform", "display", "visibility", "style")
        active_attr = "geometry"
        try:
            geom = {}
            for key in ("x", "y", "width", "height"):
                active_attr = key
                geom[key] = length(edited_frame.get(key))
            if geom["width"] < 0 or geom["height"] < 0:
                raise EngineeringError("UNSUPPORTED_HUMAN_EDIT", "Negative viewport size")
            active_attr = "transform"
            matrix = multiply(
                parse_transform(edited.get("transform")),
                multiply(
                    translation(geom["x"], geom["y"]),
                    scaling(geom["width"] / node.content_width, geom["height"] / node.content_height),
                ),
            )
            active_attr = "style"
            style = css_declarations(edited.get("style"))
            if set(style) - {"display", "visibility"}:
                raise EngineeringError(
                    "UNSUPPORTED_HUMAN_EDIT", "Only display/visibility viewport CSS is supported"
                )
            display = style.get("display", edited.get("display", "inline"))
            visibility = style.get("visibility", edited.get("visibility", "visible"))
            if display not in ("none", "inline") or visibility not in ("hidden", "visible"):
                raise EngineeringError("UNSUPPORTED_HUMAN_EDIT", "Unsupported visibility value")
            node.visible = display != "none" and visibility != "hidden"

            owned_expected = [e for e in expected_frame if e.get("data-eas-part") in ("body", "opaque")]
            owned_edited = [e for e in edited_frame if e.get("data-eas-part") in ("body", "opaque")]
            if len(owned_expected) != len(owned_edited):
                raise EngineeringError("UNSUPPORTED_HUMAN_EDIT", "Node body added, removed or marker lost")
            active_attr = "content"
            if node.kind == "opaque_svg":
                if signature(owned_expected[0]) != signature(owned_edited[0]):
                    node.payload = restore_payload(owned_edited[0], id)
                    edited_frame.replace(owned_edited[0], deepcopy(owned_expected[0]))
            elif owned_expected:
                eb, ab = owned_expected[0], owned_edited[0]
                if ab.tag != eb.tag:
                    raise EngineeringError("UNSUPPORTED_HUMAN_EDIT", "Primitive type changed")
                if node.kind == "text":
                    node.text = plain_text(ab)
                    dx = length(ab.get("x")) - length(eb.get("x"))
                    dy = length(ab.get("y")) - length(eb.get("y"))
                    matrix = multiply(matrix, translation(dx, dy))
                    ab.text = eb.text
                    for child in list(ab):
                        ab.remove(child)
                    for key in ("x", "y"):
                        ab.set(key, eb.get(key))
                elif node.kind in ("equipment_card", "data_slot", "status_indicator"):
                    captions = ab.xpath(
                        './svg:text[@data-eas-part="caption"]', namespaces={"svg": etree.QName(ab).namespace}
                    )
                    expected_caption = eb.find('.//*[@data-eas-part="caption"]')
                    if len(captions) != 1:
                        raise EngineeringError(
                            "UNSUPPORTED_HUMAN_EDIT", "Expected exactly one component caption"
                        )
                    caption = captions[0]
                    node.text = plain_text(caption)
                    caption.text = expected_caption.text
                    for child in list(caption):
                        caption.remove(child)
                elif node.kind == "shape" and node.shape == "rect":
                    x, y, w, h = (length(ab.get(k)) for k in ("x", "y", "width", "height"))
                    if w <= 0 or h <= 0:
                        raise EngineeringError(
                            "UNSUPPORTED_HUMAN_EDIT", "Zero/negative primitive resize is ambiguous"
                        )
                    primitive_changed = (x, y, w, h) != (0, 0, node.content_width, node.content_height)
                    has_children = any(n.parent_id == id for n in project.all_nodes())
                    if primitive_changed and (has_children or node.style.get("stroke", "none") != "none"):
                        raise EngineeringError(
                            "UNSUPPORTED_HUMAN_EDIT",
                            "Primitive edits with stroke or semantic children cannot be losslessly projected; resize the frame instead",
                        )
                    matrix = multiply(
                        matrix,
                        multiply(translation(x, y), scaling(w / node.content_width, h / node.content_height)),
                    )
                    for key in ("x", "y", "width", "height"):
                        ab.set(key, eb.get(key))
                if signature(ab) != signature(eb):
                    # IDs assigned by a normal editor have no engineering meaning.
                    ab.attrib.pop("id", None)
                    if signature(ab) != signature(eb):
                        raise EngineeringError(
                            "UNSUPPORTED_HUMAN_EDIT", "Unaccounted primitive attributes or styling"
                        )
            active_attr = "transform"
            original_matrix = node_matrix(nodes[id])
            if not close_matrix(matrix, original_matrix):
                if node.width == 0 or node.height == 0:
                    raise EngineeringError(
                        "UNSUPPORTED_HUMAN_EDIT", "Editing a zero-size viewport is ambiguous"
                    )
                x, y, angle, sx, sy = decompose(matrix)
                values = {
                    "x": x,
                    "y": y,
                    "rotation": angle,
                    "width": sx * node.content_width,
                    "height": sy * node.content_height,
                }
                for key, value in values.items():
                    original = getattr(node, key)
                    if key == "rotation" and math.isclose((value - original) % 360, 0, abs_tol=1e-8):
                        continue
                    if not math.isclose(value, original, rel_tol=1e-10, abs_tol=1e-9):
                        setattr(node, key, round(value, 10))
            if (
                node.locked
                and node.model_dump() != next(n for n in project.all_nodes() if n.id == id).model_dump()
            ):
                raise EngineeringError("NODE_LOCKED", "Human editing a locked node is not supported")
        except EngineeringError as exc:
            raise unsupported(id, active_attr, str(exc)) from exc
        for key in editable_attrs:
            if key in expected.attrib:
                edited.set(key, expected.get(key))
            else:
                edited.attrib.pop(key, None)
        for key in ("x", "y", "width", "height"):
            edited_frame.set(key, expected_frame.get(key))

    clear_editor_metadata(root)
    clear_editor_metadata(baseline)
    if signature(root) != signature(baseline):
        raise unsupported(
            "document", "structure", "Unaccounted document, hierarchy, order, or semantic metadata edits"
        )
    issues = validate(candidate)
    if any(i["severity"] == "ERROR" for i in issues):
        raise EngineeringError("VALIDATION_FAILED", "Human edits violate model invariants", issues)
    return SyncPlan(candidate, semantic_diff(project, candidate), project.revision, canonical_hash(project))
