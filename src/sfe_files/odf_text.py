"""Shared ODF paragraph text and conservative replacement semantics."""

from __future__ import annotations

import re

from lxml import etree

from .plan_ops import FileEditError

NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "svg": "urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "presentation": "urn:oasis:names:tc:opendocument:xmlns:presentation:1.0",
    "xml": "http://www.w3.org/XML/1998/namespace",
}


def q(name):
    prefix, local = name.split(":", 1)
    return f"{{{NS[prefix]}}}{local}"


def compact(name):
    if not isinstance(name, str):
        return "#comment-or-processing-instruction"
    part = etree.QName(name)
    prefix = next((key for key, value in NS.items() if value == part.namespace), None)
    return f"{prefix}:{part.localname}" if prefix else name


def attributes(node):
    return {compact(key): value for key, value in node.attrib.items()}


def validate_text(text):
    if not isinstance(text, str) or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]", text):
        raise FileEditError("INVALID_STEP", "text must contain valid XML characters")


def _space(node):
    count = node.get(q("text:c"), "1")
    if not re.fullmatch(r"[1-9][0-9]*", count):
        raise FileEditError("INVALID_ODF_CONTENT", "text:s requires a positive text:c")
    return " " * int(count)


def _segments(node, styles=()):
    if node.text:
        yield node.text, styles, False
    for child in node:
        if child.tag == q("text:s"):
            yield _space(child), styles, True
        elif child.tag in {q("text:tab"), q("text:line-break")}:
            yield "\t" if child.tag == q("text:tab") else "\n", styles, True
        elif child.tag == q("text:span"):
            yield from _segments(child, (*styles, dict(child.attrib)))
        elif child.tag == q("text:a"):
            yield from _segments(child, styles)
        elif isinstance(child.tag, str) and etree.QName(child).namespace == NS["text"]:
            # Expose cached field text for read-only targets; mutation rejects these elements.
            yield from _segments(child, styles)
        if child.tail:
            yield child.tail, styles, False


def paragraph_text(paragraph):
    # Keep explicit ODF whitespace separate until ordinary XML whitespace is collapsed.
    pieces = []
    for text, _, explicit in _segments(paragraph):
        if explicit:
            pieces.append((True, text))
        elif pieces and not pieces[-1][0]:
            pieces[-1] = (False, pieces[-1][1] + text)
        else:
            pieces.append((False, text))
    result = []
    for index, (explicit, text) in enumerate(pieces):
        if not explicit:
            text = re.sub(r"[ \t\r\n]+", " ", text)
            if index == 0:
                text = text.lstrip(" ")
            if index == len(pieces) - 1:
                text = text.rstrip(" ")
        result.append(text)
    return "".join(result)


def paragraph_reason(paragraph):
    if paragraph.tag not in {q("text:p"), q("text:h")}:
        return "Expected an ordinary paragraph or heading"
    allowed = {q("text:span"), q("text:s"), q("text:tab"), q("text:line-break")}
    for node in paragraph.iterdescendants():
        if node.tag not in allowed:
            return f"Paragraph contains {compact(node.tag)}"
        if q("xml:id") in node.attrib:
            return "Inline identifiers cannot be discarded during replacement"
        if node.tag in {q("text:s"), q("text:tab"), q("text:line-break")} and len(node):
            return "Whitespace element has unsupported children"
    return None


def _content_view(node):
    return {
        "tag": compact(node.tag), "attributes": attributes(node), "text": node.text or "",
        "children": [_content_view(child) for child in node], "tail": node.tail or "",
    }


def paragraph_view(paragraph):
    reason = paragraph_reason(paragraph)
    return {
        "text": paragraph_text(paragraph), "style": paragraph.get(q("text:style-name")),
        "attributes": attributes(paragraph), "editable": reason is None,
        **({"reason": reason} if reason else {}),
        "raw_text": paragraph.text or "", "content": [_content_view(child) for child in paragraph],
    }


def _write_text(parent, text):
    last = None
    for chunk in re.split(r"( +|\t|\n)", text):
        if not chunk:
            continue
        if chunk.startswith(" "):
            node = etree.SubElement(parent, q("text:s"))
            if len(chunk) > 1:
                node.set(q("text:c"), str(len(chunk)))
            last = node
        elif chunk in {"\t", "\n"}:
            last = etree.SubElement(parent, q("text:tab") if chunk == "\t" else q("text:line-break"))
        elif last is None:
            parent.text = (parent.text or "") + chunk
        else:
            last.tail = (last.tail or "") + chunk


def replace_paragraph(paragraph, text):
    validate_text(text)
    reason = paragraph_reason(paragraph)
    if reason:
        raise FileEditError("COMPLEX_TARGET", reason)
    styles = next((styles for value, styles, explicit in _segments(paragraph) if explicit or value.strip()), ())
    paragraph.text = None
    for child in list(paragraph):
        paragraph.remove(child)
    parent = paragraph
    for style in styles:
        parent = etree.SubElement(parent, q("text:span"), attrib=style)
    _write_text(parent, text.replace("\r\n", "\n").replace("\r", "\n"))
