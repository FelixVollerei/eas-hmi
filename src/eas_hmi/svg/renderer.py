import base64
from io import BytesIO

from lxml import etree
from PIL import Image, UnidentifiedImageError

from ..errors import EngineeringError
from ..model.core import canonical_json
from ..validation import validate
from .common import SVG, XLINK, number, tag, xml_id
from .opaque import render_payload

SUPPORTED_KINDS = {
    "group",
    "shape",
    "text",
    "opaque_svg",
    "image_asset",
    "data_slot",
    "status_indicator",
    "equipment_card",
    "connection",
}


def validate_image_uri(uri):
    if not isinstance(uri, str) or not uri.startswith(("data:image/png;base64,", "data:image/jpeg;base64,")):
        raise EngineeringError("INVALID_IMAGE_ASSET", "Embed a PNG/JPEG data URI in image_uri")
    try:
        encoded = uri.split(",", 1)[1]
        if len(encoded) > 20_000_000:
            raise ValueError("Image exceeds 20 MB encoded limit")
        data = base64.b64decode(encoded, validate=True)
        with Image.open(BytesIO(data)) as image:
            expected = "PNG" if uri.startswith("data:image/png;") else "JPEG"
            if image.format != expected:
                raise ValueError("Image MIME type does not match file")
            image.verify()
    except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise EngineeringError("INVALID_IMAGE_ASSET", str(exc)) from exc


def body(node):
    if node.kind == "group":
        return []
    if node.kind == "opaque_svg":
        return [render_payload(node.payload, node.id)]
    style = {"fill": "#dbe7ff", **node.style}
    attrs = {
        "id": xml_id(node.id) + "-body",
        "data-eas-part": "body",
        "style": ";".join(f"{k}:{v}" for k, v in sorted(style.items())),
    }
    if node.kind == "text":
        attrs.update({"x": "0", "y": "20", "font-family": "sans-serif", "font-size": "20"})
        element = etree.Element(tag("text"), attrs)
        element.text = node.text
    elif node.kind == "shape":
        if node.shape == "rect":
            attrs.update(x="0", y="0", width=number(node.content_width), height=number(node.content_height))
        elif node.shape == "ellipse":
            attrs.update(
                cx=number(node.content_width / 2),
                cy=number(node.content_height / 2),
                rx=number(node.content_width / 2),
                ry=number(node.content_height / 2),
            )
        else:
            attrs.update(x1="0", y1="0", x2=number(node.content_width), y2=number(node.content_height))
        element = etree.Element(tag(node.shape), attrs)
    elif node.kind == "image_asset":
        validate_image_uri(node.image_uri)
        attrs.update(
            x="0",
            y="0",
            width=number(node.content_width),
            height=number(node.content_height),
            preserveAspectRatio="none",
        )
        attrs[f"{{{XLINK}}}href"] = node.image_uri
        element = etree.Element(tag("image"), attrs)
    elif node.kind == "connection":
        attrs["style"] = ";".join(
            f"{k}:{v}"
            for k, v in sorted({"fill": "none", "stroke": "#7793b6", "stroke-width": 2, **node.style}.items())
        )
        attrs.update(x1="0", y1="0", x2=number(node.content_width), y2=number(node.content_height))
        element = etree.Element(tag("line"), attrs)
    elif node.kind in ("equipment_card", "data_slot", "status_indicator"):
        defaults = {"fill": "#23364f", "stroke": "#55718f", "stroke-width": 1}
        if node.kind == "status_indicator":
            defaults["fill"] = "#718096"  # Neutral design state, never a live equipment reading.
        attrs["style"] = ";".join(f"{k}:{v}" for k, v in sorted({**defaults, **node.style}.items()))
        element = etree.Element(tag("g"), attrs)
        shape_attrs = {"id": xml_id(node.id) + "-surface"}
        if node.kind == "status_indicator":
            shape_attrs.update(
                cx=number(node.content_width / 2),
                cy=number(node.content_height / 2),
                rx=number(node.content_width / 2),
                ry=number(node.content_height / 2),
            )
            etree.SubElement(element, tag("ellipse"), shape_attrs)
        else:
            shape_attrs.update(
                x="0",
                y="0",
                width=number(node.content_width),
                height=number(node.content_height),
                rx="6" if node.kind == "equipment_card" else "0",
            )
            etree.SubElement(element, tag("rect"), shape_attrs)
        caption = etree.SubElement(
            element,
            tag("text"),
            {
                "id": xml_id(node.id) + "-caption",
                "data-eas-part": "caption",
                "x": "8",
                "y": number(min(24, node.content_height * 0.7)),
                "font-family": "sans-serif",
                "font-size": "16",
                "fill": "#edf4ff",
                "stroke": "none",
            },
        )
        caption.text = node.text
    else:
        raise EngineeringError("UNSUPPORTED_NODE_KIND", f"Renderer does not implement {node.kind}")
    return [element]


def render_tree(project, page_id, background=True):
    errors = [i for i in validate(project) if i["severity"] == "ERROR"]
    if errors:
        raise EngineeringError("VALIDATION_FAILED", "Cannot render an invalid engineering model", errors)
    page = next((p for p in project.pages if p.id == page_id), None)
    if page is None:
        raise EngineeringError("PAGE_NOT_FOUND", f"Unknown page: {page_id}")
    root = etree.Element(tag("svg"), nsmap={None: SVG, "xlink": XLINK})
    root.attrib.update(
        {
            "id": "page-" + page.id.encode().hex(),
            "version": "1.1",
            "width": number(page.width),
            "height": number(page.height),
            "viewBox": f"0 0 {number(page.width)} {number(page.height)}",
            "data-eas-format": "1",
            "data-eas-project": project.id,
            "data-eas-page": page.id,
            "data-eas-revision": str(project.revision),
        }
    )
    if background:
        etree.SubElement(
            root,
            tag("rect"),
            {
                "id": "eas-background",
                "data-eas-part": "background",
                "x": "0",
                "y": "0",
                "width": number(page.width),
                "height": number(page.height),
                "fill": page.background,
            },
        )
    children = {}
    for node in page.nodes:
        children.setdefault(node.parent_id, []).append(node)

    def emit(node, parent):
        if node.kind not in SUPPORTED_KINDS:
            raise EngineeringError("UNSUPPORTED_NODE_KIND", f"Renderer does not implement {node.kind}")
        attrs = {"id": xml_id(node.id), "data-eas-id": node.id, "data-eas-kind": node.kind}
        if node.rotation:
            attrs["transform"] = f"rotate({number(node.rotation)} {number(node.x)} {number(node.y)})"
        if not node.visible:
            attrs["display"] = "none"
        if node.equipment_ref:
            attrs["data-eas-equipment"] = node.equipment_ref
        if node.bindings:
            attrs["data-eas-bindings"] = canonical_json({b.role: b.point_ref for b in node.bindings})
        if node.template_ref:
            attrs["data-eas-template"] = node.template_ref
        for field, marker in (("source_ref", "source"), ("target_ref", "target")):
            if getattr(node, field):
                attrs["data-eas-" + marker] = getattr(node, field)
        if node.locked:
            attrs["data-eas-locked"] = "true"
        element = etree.SubElement(parent, tag("g"), attrs)
        frame_attrs = {
            "id": xml_id(node.id) + "-frame",
            "version": "1.1",
            "data-eas-part": "frame",
            "x": number(node.x),
            "y": number(node.y),
            "width": number(node.width),
            "height": number(node.height),
            "viewBox": f"0 0 {number(node.content_width)} {number(node.content_height)}",
            "preserveAspectRatio": "none",
            "overflow": "visible",
        }
        # Inkscape renders zero-sized nested viewports unless explicitly hidden.
        if node.width == 0 or node.height == 0:
            frame_attrs["display"] = "none"
        frame = etree.SubElement(element, tag("svg"), frame_attrs)
        frame.extend(body(node))
        for child in sorted(children.get(node.id, []), key=lambda n: (n.z_index, n.id)):
            emit(child, frame)

    for node in sorted(children.get(None, []), key=lambda n: (n.z_index, n.id)):
        emit(node, root)
    return root


def render(project, page_id):
    return etree.tostring(render_tree(project, page_id), encoding="utf-8", xml_declaration=True)
