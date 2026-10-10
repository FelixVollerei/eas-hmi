"""Edit ordinary Word paragraphs and table cells without driving Word's UI."""

from __future__ import annotations

import re
from copy import deepcopy

from lxml import etree

from .ooxml import Package
from .plan_ops import FileEditError, _fields, json_get

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}
OPS = {"set-paragraph", "set-table-cell", "append-paragraph"}
CONTRACT = {
    "addressing": (
        "1-based main-body paragraphs /paragraphs/1 and tables /tables/1. Only direct body paragraphs "
        "and tables receive numbers; table paragraphs do not affect paragraph numbering. "
        "Table row and column numbers are also 1-based."
    ),
    "operations": {
        "set-paragraph": {
            "example": {"op": "set-paragraph", "paragraph": 2, "text": "Review complete."},
            "description": "Replace an ordinary paragraph, retaining paragraph properties and first-run style.",
        },
        "set-table-cell": {
            "example": {"op": "set-table-cell", "table": 1, "row": 2, "column": 2, "text": "Complete"},
            "description": "Replace a plain cell in an unmerged table; retain cell and first-paragraph properties.",
        },
        "append-paragraph": {
            "example": {"op": "append-paragraph", "text": "Next review is scheduled for Monday."},
            "description": "Append a body paragraph before final section properties using the default paragraph style.",
        },
    },
    "limitations": [
        "Transitional OOXML .docx only. The exposed editing surface is main-body text and ordinary tables.",
        (
            "Replacement uses the first run's formatting for all new text; mixed run styles are collapsed. "
            "A table-cell replacement collapses its ordinary paragraphs into the first paragraph."
        ),
        (
            "Targets containing fields, drawings, hyperlinks, tracked changes, content controls, bookmarks "
            "or other complex content are rejected. Tables containing merged cells and target cells with "
            "nested tables are rejected. Complex content elsewhere is preserved."
        ),
        (
            "Headers, footers, images and other unmodified ZIP member payloads are preserved, but are not "
            "editable through these commands. Semantic preview does not establish Word pagination or visual layout."
        ),
    ],
}


def _tag(name):
    return f"{{{W}}}{name}"


def _name(node):
    return etree.QName(node).localname if isinstance(node.tag, str) else "XML comment or processing instruction"


def parse(data: bytes) -> Package:
    document = Package.open(data, "docx")
    root = document.xml(document.main_part)
    if root.tag != _tag("document") or root.find("w:body", NS) is None:
        raise FileEditError("INVALID_DOCX", "Expected a WordprocessingML document with a body")
    return document


def _body(document):
    return document.xml(document.main_part).find("w:body", NS)


def _paragraph_text(paragraph):
    out = []
    for node in paragraph.iter():
        if node.tag == _tag("t"):
            out.append(node.text or "")
        elif node.tag == _tag("tab"):
            out.append("\t")
        elif node.tag in {_tag("br"), _tag("cr")}:
            out.append("\n")
    return "".join(out)


def _complex_reason(paragraph):
    if paragraph.tag != _tag("p"):
        return "Expected an ordinary paragraph"
    for child in paragraph:
        if child.tag not in {_tag("pPr"), _tag("r")}:
            return f"Paragraph contains {_name(child)}"
        if child.tag == _tag("r"):
            for item in child:
                if item.tag not in {_tag("rPr"), _tag("t"), _tag("tab"), _tag("br"), _tag("cr")}:
                    return f"Run contains {_name(item)}"
                if item.tag == _tag("br") and item.get(_tag("type"), "textWrapping") != "textWrapping":
                    return "Paragraph contains a page or column break"
    for node in paragraph.iter():
        if node.tag in {_tag("pPrChange"), _tag("rPrChange"), _tag("sectPr")}:
            return f"Paragraph contains {_name(node)}"
    return None


def _paragraph_view(paragraph):
    style = paragraph.find("w:pPr/w:pStyle", NS)
    reason = _complex_reason(paragraph)
    value = {
        "text": _paragraph_text(paragraph),
        "style": style.get(_tag("val")) if style is not None else None,
        "editable": reason is None,
        "paragraph_properties": _property_children(paragraph.find("w:pPr", NS)),
        "runs": [
            {"text": _paragraph_text(run), "properties": _property_children(run.find("w:rPr", NS))}
            for run in paragraph.findall("w:r", NS)
        ],
    }
    if reason:
        value["reason"] = reason
    return value


def _property_children(properties):
    """Expose OOXML formatting structure without repeated namespace declarations."""
    if properties is None:
        return []

    def view(node):
        item = {"name": _name(node)}
        if node.attrib:
            item["attributes"] = {etree.QName(key).localname: value for key, value in node.attrib.items()}
        if node.text and node.text.strip():
            item["text"] = node.text
        if len(node):
            item["children"] = [view(child) for child in node]
        return item

    return [view(node) for node in properties]


def _table_reason(table):
    for node in table.iter():
        if node.tag in {_tag("gridSpan"), _tag("vMerge"), _tag("hMerge")}:
            return "Table contains merged cells"
        if node.tag in {
            _tag("ins"),
            _tag("del"),
            _tag("moveFrom"),
            _tag("moveTo"),
            _tag("sdt"),
            _tag("tblPrChange"),
            _tag("trPrChange"),
            _tag("tcPrChange"),
        }:
            return "Table contains tracked changes or content controls"
    if any(child.tag not in {_tag("tblPr"), _tag("tblGrid"), _tag("tr")} for child in table):
        return "Table contains complex row content"
    return None


def _cell_reason(cell):
    if any(child.tag not in {_tag("tcPr"), _tag("p")} for child in cell):
        return "Cell contains nested or complex content"
    paragraphs = cell.findall("w:p", NS)
    if not paragraphs:
        return "Cell has no ordinary paragraph"
    for paragraph in paragraphs:
        reason = _complex_reason(paragraph)
        if reason:
            return reason
    return None


def projection(document: Package) -> dict:
    body = _body(document)
    paragraphs = {
        str(index): _paragraph_view(paragraph)
        for index, paragraph in enumerate(body.findall("w:p", NS), 1)
    }
    tables = {}
    for index, table in enumerate(body.findall("w:tbl", NS), 1):
        table_reason = _table_reason(table)
        rows = []
        for row in table.findall("w:tr", NS):
            cells = []
            for cell in row.findall("w:tc", NS):
                reason = table_reason or _cell_reason(cell)
                value = {
                    "text": "\n".join(_paragraph_text(p) for p in cell.findall("w:p", NS)),
                    "editable": reason is None,
                    "paragraphs": [_paragraph_view(p) for p in cell.findall("w:p", NS)],
                }
                if reason:
                    value["reason"] = reason
                cells.append(value)
            rows.append({"cells": cells})
        tables[str(index)] = {"rows": rows}
    unsupported = [
        _name(child)
        for child in body
        if child.tag not in {_tag("p"), _tag("tbl"), _tag("sectPr")}
    ]
    return {"paragraphs": paragraphs, "tables": tables, "unsupported_body_elements": unsupported}


def summary(document: Package) -> dict:
    view = projection(document)
    return {
        "paragraph_count": len(view["paragraphs"]),
        "table_count": len(view["tables"]),
        "paragraphs": [
            {"paragraph": int(index), "text": item["text"][:200], "editable": item["editable"]}
            for index, item in list(view["paragraphs"].items())[:20]
        ],
        "unsupported_body_elements": view["unsupported_body_elements"],
        "truncated": len(view["paragraphs"]) > 20,
    }


def inspect(document: Package, pointer: str = "") -> dict:
    if not isinstance(pointer, str):
        raise FileEditError("INVALID_POINTER", "pointer must be a string")
    return {"pointer": pointer, "value": json_get(projection(document), pointer)}


def _ordinal(value, label):
    if type(value) is not int or value < 1:
        raise FileEditError("INVALID_STEP", f"{label} must be a 1-based positive integer")
    return value - 1


def _select(items, value, label):
    index = _ordinal(value, label)
    if index >= len(items):
        raise FileEditError("TARGET_NOT_FOUND", f"{label} {value} does not exist")
    return items[index]


def _replace_text(paragraph, text):
    reason = _complex_reason(paragraph)
    if reason:
        raise FileEditError("COMPLEX_TARGET", reason)
    old_runs = paragraph.findall("w:r", NS)
    run = deepcopy(old_runs[0]) if old_runs else etree.Element(_tag("r"))
    for child in list(run):
        if child.tag != _tag("rPr"):
            run.remove(child)
    for child in old_runs:
        paragraph.remove(child)
    for chunk in re.split(r"(\n|\t)", text.replace("\r\n", "\n").replace("\r", "\n")):
        if chunk == "\n":
            etree.SubElement(run, _tag("br"))
        elif chunk == "\t":
            etree.SubElement(run, _tag("tab"))
        elif chunk:
            node = etree.SubElement(run, _tag("t"))
            node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            node.text = chunk
    paragraph.append(run)


def edit(document: Package, step: dict) -> Package:
    if not isinstance(step, dict) or not isinstance(step.get("op"), str) or step["op"] not in OPS:
        raise FileEditError("INVALID_STEP", "Unsupported DOCX operation")
    op = step["op"]
    fields = {
        "set-paragraph": {"op", "paragraph", "text"},
        "set-table-cell": {"op", "table", "row", "column", "text"},
        "append-paragraph": {"op", "text"},
    }[op]
    _fields(step, fields, fields)
    text = step["text"]
    if not isinstance(text, str) or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]", text):
        raise FileEditError("INVALID_STEP", "text must be a string containing valid XML characters")
    body = _body(document)
    if op == "set-paragraph":
        paragraph = _select(body.findall("w:p", NS), step["paragraph"], "paragraph")
        _replace_text(paragraph, text)
    elif op == "set-table-cell":
        table = _select(body.findall("w:tbl", NS), step["table"], "table")
        row = _select(table.findall("w:tr", NS), step["row"], "row")
        cell = _select(row.findall("w:tc", NS), step["column"], "column")
        reason = _table_reason(table) or _cell_reason(cell)
        if reason:
            raise FileEditError("COMPLEX_TARGET", reason)
        paragraphs = cell.findall("w:p", NS)
        _replace_text(paragraphs[0], text)
        for paragraph in paragraphs[1:]:
            cell.remove(paragraph)
    else:
        paragraph = etree.Element(_tag("p"))
        _replace_text(paragraph, text)
        section = body.find("w:sectPr", NS)
        if section is None:
            body.append(paragraph)
        else:
            body.insert(body.index(section), paragraph)
    document.mark_changed(document.main_part)
    return document


def serialize(document: Package) -> bytes:
    return document.serialize()
