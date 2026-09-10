import math
import re

import tinycss2
from lxml import etree

from ..errors import EngineeringError

SVG = "http://www.w3.org/2000/svg"
XLINK = "http://www.w3.org/1999/xlink"
INKSCAPE = "http://www.inkscape.org/namespaces/inkscape"
NS = {"svg": SVG}


def tag(name):
    return f"{{{SVG}}}{name}"


def xml_id(value):
    return "node-" + value.encode("utf-8").hex()


def number(value):
    if not math.isfinite(value):
        raise EngineeringError("INVALID_GEOMETRY", "Non-finite SVG number")
    return format(0.0 if abs(value) < 1e-12 else value, ".15g")


def length(value):
    value = (value or "").strip()
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?(?:px)?", value):
        raise EngineeringError("UNSUPPORTED_HUMAN_EDIT", f"Expected numeric SVG length or px: {value}")
    result = float(value.removesuffix("px"))
    if not math.isfinite(result):
        raise EngineeringError("UNSUPPORTED_HUMAN_EDIT", "Non-finite SVG length")
    return result


def local(element):
    return etree.QName(element).localname if isinstance(element.tag, str) else ""


def css_declarations(text):
    result = {}
    for item in tinycss2.parse_declaration_list(text or "", skip_comments=True, skip_whitespace=True):
        if item.type != "declaration" or item.important:
            raise EngineeringError("UNSUPPORTED_HUMAN_EDIT", "Invalid or important CSS declaration")
        result[item.lower_name] = tinycss2.serialize(item.value).strip()
    return result


def css_references(tokens):
    refs = set()
    for token in tokens:
        if token.type == "error":
            raise EngineeringError("UNSAFE_SVG", "Malformed CSS cannot be safely rasterized")
        if token.type == "at-keyword" and token.value.lower() in ("import", "font-face"):
            raise EngineeringError(
                "EXTERNAL_RESOURCE", "CSS imports and embedded font loading are unsupported"
            )
        if token.type == "url":
            value = token.value
        elif token.type == "function" and token.lower_name == "url":
            arguments = [t for t in token.arguments if t.type not in ("whitespace", "comment")]
            if len(arguments) != 1 or arguments[0].type != "string":
                raise EngineeringError("UNSAFE_SVG", "Invalid CSS URL")
            value = arguments[0].value
        else:
            nested = getattr(token, "content", getattr(token, "arguments", []))
            refs.update(css_references(nested))
            continue
        if not value.startswith("#"):
            raise EngineeringError("EXTERNAL_RESOURCE", "External CSS URLs are unsupported")
        refs.add(value[1:])
    return refs


def safe_parse(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    if len(data) > 20_000_000:
        raise EngineeringError("UNSAFE_SVG", "SVG input exceeds the 20 MB experiment limit")
    try:
        parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)
        root = etree.fromstring(data, parser)
    except etree.XMLSyntaxError as exc:
        raise EngineeringError("INVALID_SVG", str(exc)) from exc
    if root.getroottree().docinfo.doctype or root.getroottree().xpath("//processing-instruction()"):
        raise EngineeringError("UNSAFE_SVG", "DTD and processing instructions are unsupported")
    if root.tag != tag("svg"):
        raise EngineeringError("INVALID_SVG", "A namespaced SVG root is required")
    references = set()
    ids = set()
    for e in root.iter():
        if not isinstance(e.tag, str):
            continue
        if local(e) in {"script", "foreignObject", "animate", "animateMotion", "animateTransform", "set"}:
            raise EngineeringError("UNSAFE_SVG", f"Active SVG element unsupported: {local(e)}")
        if e.get("id"):
            ids.add(e.get("id"))
        if local(e) == "style":
            references.update(css_references(tinycss2.parse_component_value_list(e.text or "")))
        for name, value in e.attrib.items():
            key = etree.QName(name).localname
            if key.lower().startswith("on"):
                raise EngineeringError("UNSAFE_SVG", f"Event attribute unsupported: {key}")
            if key == "href" and value and not value.startswith("#"):
                if not (
                    local(e) == "image"
                    and value.startswith(("data:image/png;base64,", "data:image/jpeg;base64,"))
                ):
                    raise EngineeringError(
                        "EXTERNAL_RESOURCE", "Only local #references and embedded PNG/JPEG are supported"
                    )
            if key == "href" and value.startswith("#"):
                references.add(value[1:])
            if key in (
                "style",
                "fill",
                "stroke",
                "clip-path",
                "mask",
                "filter",
                "marker",
                "marker-start",
                "marker-mid",
                "marker-end",
                "cursor",
            ):
                references.update(css_references(tinycss2.parse_component_value_list(value)))
            if key == "base":
                raise EngineeringError("EXTERNAL_RESOURCE", "xml:base is unsupported")
    if references - ids:
        raise EngineeringError(
            "INVALID_SVG_REFERENCE", f"Unresolved SVG references: {sorted(references - ids)}"
        )
    return root


def signature(element):
    """XML tree identity ignoring prefixes, attr order, comments, indentation only.

    Text/tspan/style whitespace is content and is never globally stripped.
    """
    if not isinstance(element.tag, str):
        return None
    text = element.text or ""
    if local(element) not in ("text", "tspan", "style") and not text.strip():
        text = ""
    attrs = tuple(sorted(element.attrib.items()))
    children = tuple(
        (signature(c), c.tail if (c.tail or "").strip() else "") for c in element if isinstance(c.tag, str)
    )
    return element.tag, attrs, text, children
