"""Conservative import: preserve artwork, reject unaccounted semantic edits."""

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass

from lxml import etree

from ..errors import EngineeringError
from ..geometry import decompose, multiply, parse_transform, scaling, translation
from ..model import Binding, Equipment, Node, Page, Point, Project, Template
from .common import XLINK, css_declarations, length, local, safe_parse
from .opaque import render_payload, restore_payload
from .renderer import SUPPORTED_KINDS, render
from .synchronizer import frame_of, identity_map, parent_identity, plain_text, plan_sync


@dataclass
class ImportResult:
    project: Project
    warnings: list[str]


def dimensions(root):
    viewbox = root.get("viewBox")
    values = [length(v) for v in re.split(r"[\s,]+", viewbox.strip())] if viewbox else None
    if values is not None and (len(values) != 4 or values[2] <= 0 or values[3] <= 0):
        raise EngineeringError("INVALID_SVG", "viewBox requires four numbers and positive size")
    w = length(root.get("width")) if root.get("width") else (values[2] if values else 300)
    h = length(root.get("height")) if root.get("height") else (values[3] if values else 150)
    if w <= 0 or h <= 0:
        raise EngineeringError("INVALID_SVG", "SVG dimensions must be positive")
    return w, h


def semantic_import(root):
    if root.get("data-eas-format") != "1" or root.get("data-eas-artifact"):
        raise EngineeringError(
            "UNSUPPORTED_SEMANTIC_IMPORT",
            "Import requires an editable EAS semantic SVG (format 1), not an asset fragment",
        )
    project_id, page_id = root.get("data-eas-project"), root.get("data-eas-page")
    if not project_id or not page_id:
        raise EngineeringError("INVALID_SVG", "Missing project/page identity")
    w, h = dimensions(root)
    background = root.find('./*[@data-eas-part="background"]')
    page = Page(
        id=page_id,
        name=page_id,
        width=w,
        height=h,
        background=background.get("fill") if background is not None else "none",
    )
    project = Project(id=project_id, name=project_id, pages=[page])
    equipment_ids, point_ids, template_roles = set(), set(), {}
    for order, (node_id, element) in enumerate(identity_map(root).items()):
        kind = element.get("data-eas-kind")
        if kind not in SUPPORTED_KINDS:
            raise EngineeringError("UNSUPPORTED_SEMANTIC_IMPORT", f"Unrecognized kind for {node_id}: {kind}")
        frame = frame_of(element)
        viewbox = [length(v) for v in frame.get("viewBox", "").split()]
        if len(viewbox) != 4 or viewbox[:2] != [0, 0] or min(viewbox[2:]) <= 0:
            raise EngineeringError("UNSUPPORTED_SEMANTIC_IMPORT", "Expected origin-based intrinsic frame")
        cw, ch = viewbox[2:]
        x, y, width, height = (length(frame.get(k)) for k in ("x", "y", "width", "height"))
        if width < 0 or height < 0:
            raise EngineeringError("INVALID_SVG", "Negative semantic frame size")
        if width == 0 or height == 0:
            # Preserve the canonical renderer's zero frame, but reject singular matrix edits.
            angle = 0.0
            if element.get("transform"):
                raise EngineeringError(
                    "UNSUPPORTED_SEMANTIC_IMPORT", "Transformed zero-size import is ambiguous"
                )
        else:
            x, y, angle, sx, sy = decompose(
                multiply(
                    parse_transform(element.get("transform")),
                    multiply(translation(x, y), scaling(width / cw, height / ch)),
                )
            )
            width, height = sx * cw, sy * ch
        style = css_declarations(element.get("style"))
        if set(style) - {"display", "visibility"}:
            raise EngineeringError("UNSUPPORTED_SEMANTIC_IMPORT", "Unsupported style on semantic container")
        raw_bindings = json.loads(element.get("data-eas-bindings", "{}"))
        if not isinstance(raw_bindings, dict) or any(
            not isinstance(k, str) or not isinstance(v, str) for k, v in raw_bindings.items()
        ):
            raise EngineeringError("INVALID_SVG", "Bindings must map role strings to point IDs")
        node = Node(
            id=node_id,
            kind=kind,
            page_id=page_id,
            parent_id=parent_identity(element),
            x=x,
            y=y,
            width=width,
            height=height,
            rotation=angle,
            content_width=cw,
            content_height=ch,
            z_index=order,
            equipment_ref=element.get("data-eas-equipment"),
            template_ref=element.get("data-eas-template"),
            source_ref=element.get("data-eas-source"),
            target_ref=element.get("data-eas-target"),
            locked=element.get("data-eas-locked") == "true",
            visible=style.get("display", element.get("display")) != "none"
            and style.get("visibility", element.get("visibility")) != "hidden",
            bindings=[Binding(role=k, point_ref=v) for k, v in raw_bindings.items()],
        )
        owned = [e for e in frame if e.get("data-eas-part") in ("body", "opaque")]
        if len(owned) != (0 if kind == "group" else 1):
            raise EngineeringError(
                "UNSUPPORTED_SEMANTIC_IMPORT", f"Unexpected artwork structure at {node_id}"
            )
        if owned:
            body = owned[0]
            node.style = css_declarations(body.get("style"))
            if kind == "opaque_svg":
                node.payload = restore_payload(body, node_id)
            elif kind == "shape":
                node.shape = local(body)
            elif kind == "text":
                node.text = plain_text(body)
            elif kind == "image_asset":
                node.image_uri = body.get(f"{{{XLINK}}}href") or body.get("href")
            elif kind in ("equipment_card", "data_slot", "status_indicator"):
                caption = body.find('./*[@data-eas-part="caption"]')
                if caption is None:
                    raise EngineeringError("UNSUPPORTED_SEMANTIC_IMPORT", "Component caption missing")
                node.text = plain_text(caption)
        if node.equipment_ref:
            equipment_ids.add(node.equipment_ref)
        point_ids.update(raw_bindings.values())
        if node.template_ref:
            template_roles.setdefault(node.template_ref, set()).update(raw_bindings)
        page.nodes.append(node)
    unresolved = {"unresolved_import": True}
    project.equipment = [
        Equipment(id=i, name=i, type="unknown", metadata=unresolved) for i in sorted(equipment_ids)
    ]
    project.points = [Point(id=i, tag="", datatype="unknown", metadata=unresolved) for i in sorted(point_ids)]
    project.templates = [
        Template(id=i, optional_bindings=sorted(roles), metadata=unresolved)
        for i, roles in sorted(template_roles.items())
    ]
    edited = deepcopy(root)
    edited.set("data-eas-revision", "0")
    # The synchronizer accounts for the complete source tree. Unknown artwork,
    # reparenting and unsupported styles fail instead of disappearing on re-render.
    project = plan_sync(project, etree.tostring(edited)).candidate
    return ImportResult(
        project,
        [
            "SVG contains visual identity only: names, metadata, binding expressions, point tags/datatypes and template requirements must be enriched from engineering data."
        ],
    )


def ordinary_import(root, data):
    w, h = dimensions(root)
    fingerprint = hashlib.sha256(data).hexdigest()[:16]
    page_id = "import-" + fingerprint
    page = Page(id=page_id, name="Imported SVG", width=w, height=h, background="none")
    project = Project(id="SVG_" + fingerprint, name="Imported SVG", pages=[page])
    artwork = [
        e
        for e in root
        if local(e) not in ("defs", "metadata", "title", "desc", "style", "namedview")
        and isinstance(e.tag, str)
    ]
    top_ids = []
    for index, element in enumerate(artwork):
        original = element.get("id")
        stable = (
            original
            if original and not any(c.isspace() for c in original)
            else f"IMPORTED_{fingerprint}_{index + 1}"
        )
        element.set("id", stable)
        top_ids.append(stable)
    # Split only independent top-level artwork. Shared CSS, root compositing,
    # transforms and cross-object references retain one composite opaque node.
    root_keys = {etree.QName(k).localname for k in root.attrib}
    complex_root = bool(root_keys - {"id", "width", "height", "viewBox", "version", "preserveAspectRatio"})
    cross_refs = False
    ownership = {
        child.get("id"): index
        for index, e in enumerate(artwork)
        for child in e.iter()
        if isinstance(child.tag, str) and child.get("id")
    }
    for index, e in enumerate(artwork):
        text = etree.tostring(e, encoding="unicode")
        for ref in re.findall(r'(?:url\(\s*[\'"]?#|href=[\'"]#)([^\s\)\'"]+)', text):
            if ref in ownership and ownership[ref] != index:
                cross_refs = True
    # Definitions can themselves reference visible siblings (for example a <use>
    # inside a clipPath). Keep that dependency graph intact as one composite.
    for e in root:
        if e not in artwork:
            text = etree.tostring(e, encoding="unicode")
            if any(
                ref in ownership for ref in re.findall(r'(?:url\(\s*[\'"]?#|href=[\'"]#)([^\s\)\'"]+)', text)
            ):
                cross_refs = True
    combined = complex_root or cross_refs or any(local(e) == "style" for e in root.iter()) or not artwork
    groups = [("IMPORTED_" + fingerprint, None)] if combined else list(zip(top_ids, artwork))
    for order, (node_id, selected) in enumerate(groups):
        payload = deepcopy(root)
        if selected is not None:
            for index, element in reversed(list(enumerate(root))):
                if element in artwork and element is not selected:
                    payload.remove(payload[index])
        text = etree.tostring(payload, encoding="unicode")
        # Check the exact preservation backend before reporting a successful import.
        render_payload(text, node_id)
        page.nodes.append(
            Node(
                id=node_id,
                kind="opaque_svg",
                page_id=page_id,
                width=w,
                height=h,
                content_width=w,
                content_height=h,
                z_index=order,
                payload=text,
                metadata={
                    "source_sha256": hashlib.sha256(data).hexdigest(),
                    "source_top_level_ids": top_ids if combined else [node_id],
                },
            )
        )
    warnings = ["Ordinary SVG imported as opaque artwork; internal paths are preserved, not interpreted."]
    if combined:
        warnings.append(
            "Shared styling/compositing/references retained as one opaque node; stable top-level XML IDs are recorded in metadata."
        )
    return ImportResult(project, warnings)


def import_svg(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    root = safe_parse(data)
    xml_ids = [e.get("id") for e in root.iter() if isinstance(e.tag, str) and e.get("id")]
    if len(xml_ids) != len(set(xml_ids)):
        raise EngineeringError("DUPLICATE_ID", "Duplicate SVG XML IDs are ambiguous")
    result = (
        semantic_import(root)
        if root.get("data-eas-format") or identity_map(root)
        else ordinary_import(root, data)
    )
    # Verify supported renderer and references now, never after committing.
    for page in result.project.pages:
        render(result.project, page.id)
    return result


def append_import(project, imported):
    for page in imported.pages:
        if page.id in {p.id for p in project.pages}:
            raise EngineeringError("DUPLICATE_ID", f"Page already exists: {page.id}")
        project.pages.append(page.model_copy(deep=True))
    for registry in ("equipment", "points", "templates"):
        existing = getattr(project, registry)
        known = {item.id for item in existing}
        existing.extend(
            item.model_copy(deep=True) for item in getattr(imported, registry) if item.id not in known
        )
