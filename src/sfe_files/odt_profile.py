"""Native ZIP/XML editing for ordinary OpenDocument Text content."""

from __future__ import annotations

from lxml import etree

from .odf_text import (
    NS,
    attributes,
    compact,
    paragraph_reason,
    paragraph_text,
    paragraph_view,
    q,
    replace_paragraph,
    validate_text,
)
from .plan_ops import FileEditError, _fields, _pointer_parts, json_get
from .zip_document import open_odf

PACKAGE = True
OPS = {"set-paragraph", "append-paragraph", "set-table-cell"}
CONTRACT = {
    "addressing": (
        "Main office:text direct text:p/text:h paragraphs use /paragraphs/1 (1-based). "
        "Direct tables use their unique table:name at /tables/<escaped-name>; row and column are 1-based."
    ),
    "operations": {
        "set-paragraph": {
            "example": {"op": "set-paragraph", "paragraph": 2, "text": "Review approved."},
            "description": "Replace a plain paragraph, retaining its attributes and first text style chain.",
        },
        "append-paragraph": {
            "example": {"op": "append-paragraph", "text": "Next review is on Monday."},
            "description": "Append a paragraph with default formatting at the end of the main text body.",
        },
        "set-table-cell": {
            "example": {"op": "set-table-cell", "table": "Actions", "row": 2, "column": 2, "text": "Done"},
            "description": "Replace a plain string cell, retaining cell attributes and first paragraph formatting.",
        },
    },
    "limitations": [
        "Native ODF package editing. Lists, sections, headers and nested tables have no mutation addresses.",
        (
            "Links, bookmarks, fields, tracked changes and other complex inline targets are rejected. "
            "Mixed span formatting collapses to the first text style; semantic preview includes original spans."
        ),
        (
            "Table edits reject merged/repeated/protected tables and formula or non-string cells. "
            "Multiple ordinary cell paragraphs collapse into the first, as shown in semantic preview."
        ),
        "Styles, images, metadata and all unmodified ZIP member payloads are preserved. No layout rendering is performed.",
    ],
    "standard": "https://docs.oasis-open.org/office/OpenDocument/v1.3/os/part3-schema/OpenDocument-v1.3-os-part3-schema.html",
}


def parse(data: bytes):
    document = open_odf(data, "odt")
    if _body(document) is None:
        raise FileEditError("INVALID_ODT", "Expected office:body/office:text")
    return document


def _body(document):
    return document.xml("content.xml").find("office:body/office:text", NS)


def _paragraphs(document):
    return [node for node in _body(document) if node.tag in {q("text:p"), q("text:h")}]


def _table_reason(table):
    if table.get(q("table:protected")) in {"true", "1"}:
        return "Protected table is unsupported"
    for node in table.iter():
        if node.tag == q("table:covered-table-cell"):
            return "Merged table cells are unsupported"
        repeated = (
            node.tag == q("table:table-row") and node.get(q("table:number-rows-repeated"), "1") != "1"
        ) or (
            node.tag == q("table:table-cell") and node.get(q("table:number-columns-repeated"), "1") != "1"
        )
        if repeated or any(key in node.attrib and node.get(key) != "1" for key in (
            q("table:number-rows-spanned"), q("table:number-columns-spanned"),
        )):
            return "Merged or repeated table rows/cells are unsupported"
        if node.tag in {q("table:table-row-group"), q("table:table-source")}:
            return "Grouped or linked table structure is unsupported"
    return None


def _rows(table):
    result = []
    for node in table:
        if node.tag == q("table:table-row"):
            result.append(node)
        elif node.tag in {q("table:table-header-rows"), q("table:table-rows")}:
            result.extend(node.findall("table:table-row", NS))
    return result


def _cell_reason(cell):
    if cell.tag != q("table:table-cell"):
        return "Expected ordinary table cell"
    if cell.get(q("table:protected")) in {"true", "1"} or q("table:formula") in cell.attrib:
        return "Protected or formula cells are unsupported"
    if cell.get(q("office:value-type")) not in {None, "string"}:
        return "Only string cells are editable"
    if any(node.tag not in {q("text:p"), q("text:h")} for node in cell):
        return "Cell contains nested or complex content"
    if not len(cell):
        return "Cell has no ordinary paragraph"
    return next((reason for node in cell if (reason := paragraph_reason(node))), None)


def _table_view(table):
    reason = _table_reason(table)
    return {
        "name": table.get(q("table:name")), "attributes": attributes(table),
        "rows": [{"cells": [
            {
                "text": "\n".join(paragraph_text(p) for p in cell if p.tag in {q("text:p"), q("text:h")}),
                "attributes": attributes(cell), "editable": not (reason or _cell_reason(cell)),
                "reason": reason or _cell_reason(cell),
                "paragraphs": [paragraph_view(p) for p in cell if p.tag in {q("text:p"), q("text:h")}],
            }
            for cell in row if cell.tag in {q("table:table-cell"), q("table:covered-table-cell")}
        ]} for row in _rows(table)],
    }


def projection(document):
    body = _body(document)
    tables = body.findall("table:table", NS)
    # A list retains duplicate names without silently hiding either table.
    return {
        "paragraphs": {str(i): paragraph_view(node) for i, node in enumerate(_paragraphs(document), 1)},
        "tables": [_table_view(table) for table in tables],
        "other_body_elements": [compact(node.tag) for node in body if node.tag not in {
            q("text:p"), q("text:h"), q("table:table"),
        }],
    }


def summary(document):
    view = projection(document)
    return {
        "paragraph_count": len(view["paragraphs"]), "table_count": len(view["tables"]),
        "paragraphs": [{"paragraph": int(key), "text": item["text"][:200], "editable": item["editable"]}
                       for key, item in list(view["paragraphs"].items())[:20]],
        "tables": [{"name": item["name"], "row_count": len(item["rows"])} for item in view["tables"][:20]],
        "truncated": len(view["paragraphs"]) > 20 or len(view["tables"]) > 20,
    }


def _table(document, name):
    if not isinstance(name, str) or not name:
        raise FileEditError("INVALID_STEP", "table must be a nonempty table:name")
    matches = [table for table in _body(document).findall("table:table", NS) if table.get(q("table:name")) == name]
    if len(matches) != 1:
        raise FileEditError("TARGET_NOT_UNIQUE", f"Expected one table named {name}; found {len(matches)}")
    return matches[0]


def inspect(document, pointer=""):
    if not isinstance(pointer, str):
        raise FileEditError("INVALID_POINTER", "pointer must be a string")
    if not pointer:
        return {"pointer": pointer, "value": summary(document)}
    parts = _pointer_parts(pointer)
    if len(parts) == 2 and parts[0] == "tables":
        value = _table_view(_table(document, parts[1]))
    elif len(parts) == 2 and parts[0] == "paragraphs":
        value = json_get(projection(document), pointer)
    else:
        raise FileEditError("INVALID_POINTER", "Use /paragraphs/<number> or /tables/<name>")
    return {"pointer": pointer, "value": value}


def _select(items, number, label):
    if type(number) is not int or number < 1:
        raise FileEditError("INVALID_STEP", f"{label} must be a 1-based positive integer")
    if number > len(items):
        raise FileEditError("TARGET_NOT_FOUND", f"{label} {number} does not exist")
    return items[number - 1]


def edit(document, step):
    if not isinstance(step, dict) or not isinstance(step.get("op"), str) or step["op"] not in OPS:
        raise FileEditError("INVALID_STEP", "Unsupported ODT operation")
    op = step["op"]
    fields = {"op", "text"} | ({"paragraph"} if op == "set-paragraph" else
                              {"table", "row", "column"} if op == "set-table-cell" else set())
    _fields(step, fields, fields)
    validate_text(step["text"])
    if op == "set-paragraph":
        replace_paragraph(_select(_paragraphs(document), step["paragraph"], "paragraph"), step["text"])
    elif op == "append-paragraph":
        paragraph = etree.Element(q("text:p"))
        replace_paragraph(paragraph, step["text"])
        _body(document).append(paragraph)
    else:
        table = _table(document, step["table"])
        row = _select(_rows(table), step["row"], "row")
        cell = _select([node for node in row if node.tag in {q("table:table-cell"), q("table:covered-table-cell")}],
                       step["column"], "column")
        reason = _table_reason(table) or _cell_reason(cell)
        if reason:
            raise FileEditError("COMPLEX_TARGET", reason)
        paragraphs = list(cell)
        replace_paragraph(paragraphs[0], step["text"])
        for paragraph in paragraphs[1:]:
            cell.remove(paragraph)
        cell.set(q("office:value-type"), "string")
        if q("office:string-value") in cell.attrib:
            cell.set(q("office:string-value"), step["text"])
    document.mark_changed("content.xml")
    return document


def serialize(document):
    return document.serialize()
