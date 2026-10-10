"""Native ODF spreadsheet cell editing without expanding repeated grid runs."""

from __future__ import annotations

import math
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from lxml import etree

from .plan_ops import FileEditError, _fields, _pointer_parts
from .zip_document import ZipDocument, open_odf

PACKAGE = True
OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
FORMULA = "urn:oasis:names:tc:opendocument:xmlns:of:1.2"
CALCEXT = "urn:org:documentfoundation:names:experimental:calc:xmlns:calcext:1.0"
O, T, X = "{" + OFFICE + "}", "{" + TABLE + "}", "{" + TEXT + "}"
ROW_REPEAT, COL_REPEAT = T + "number-rows-repeated", T + "number-columns-repeated"
VALUE_ATTRS = {O + name for name in (
    "value-type", "value", "string-value", "boolean-value", "date-value", "time-value", "currency",
)} | {"{" + CALCEXT + "}value-type"}
OPS = {"set-cell", "set-formula"}
CONTRACT = {
    "addressing": {"inspect": "/sheets/Costs/A1:C8", "edit": "sheet name plus uppercase A1 cell",
                   "escaping": "Escape / as ~1 and ~ as ~0 in sheet-name pointer segments."},
    "operations": {
        "set-cell": {"example": {"op": "set-cell", "sheet": "Costs", "cell": "B3", "value": 1800},
                     "description": "Set a JSON scalar or null (clear content), retaining the cell style."},
        "set-formula": {"example": {"op": "set-formula", "sheet": "Costs", "cell": "C5",
                                    "formula": "of:=SUM([.B2:.B4])"},
                        "description": "Write OpenFormula using the of:= prefix; Excel = syntax is not accepted."},
    },
    "limitations": [
        "ODF 1.2/1.3 .ods packages; no encryption or signatures. Edits use declared rows and columns only.",
        "Ordinary repeated scalar/blank rows and cells are split around the edited cell without expanding the sheet.",
        "Repeated runs with formulas, identifiers or complex objects cannot be split; references are not rebased.",
        "Merged anchors are editable; covered cells, other merged members and matrix formula ranges are read-only.",
        "No formula execution: all formula caches and matrix outputs are cleared; requires_recalculation is exposed.",
        "Dates/times expose stored ISO values. New scalar values support string, number, boolean and null only.",
        "Styles, annotations and untouched package payloads are preserved; replacing text removes its run formatting.",
        "Charts/pivots and linked-object caches are preserved and may need refresh in an ODF spreadsheet engine.",
        "projection uses compact repeated row/cell runs. Range inspection is limited to 1000 cells.",
    ],
}


@dataclass
class OdsDocument:
    package: ZipDocument
    root: Any
    sheets: dict[str, Any]


def _fail(message, code="UNSUPPORTED_ODS"):
    raise FileEditError(code, message)


def _count(node, attribute):
    value = node.get(attribute, "1")
    if not re.fullmatch(r"[1-9][0-9]*", value):
        _fail("ODF repeat/span counts must be positive integers", "INVALID_ODS")
    return int(value)


def _address(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z]+[1-9][0-9]*", value):
        _fail("Use an uppercase A1 address without $ or a sheet prefix", "INVALID_CELL")
    match = re.fullmatch(r"([A-Z]+)([0-9]+)", value)
    column = 0
    for char in match[1]:
        column = column * 26 + ord(char) - 64
    return int(match[2]), column


def _name(row, column):
    letters = ""
    while column:
        column, digit = divmod(column - 1, 26)
        letters = chr(65 + digit) + letters
    return letters + str(row)


def _elements(parent, kind):
    wrappers = {
        "row": {T + x for x in ("table-row-group", "table-header-rows", "table-rows")},
        "column": {T + x for x in ("table-column-group", "table-header-columns", "table-columns")},
    }
    for child in parent:
        if child.tag == T + "table-" + kind:
            yield child
        elif child.tag in wrappers[kind]:
            yield from _elements(child, kind)


def _rows(sheet):
    start = 1
    for row in _elements(sheet, "row"):
        repeat = _count(row, ROW_REPEAT)
        yield start, repeat, row
        start += repeat


def _cells(row):
    start = 1
    for cell in row:
        if cell.tag not in {T + "table-cell", T + "covered-table-cell"}:
            _fail("Unsupported structure within an ODF table row")
        repeat = _count(cell, COL_REPEAT)
        yield start, repeat, cell
        start += repeat


def _width(sheet):
    explicit = sum(_count(col, COL_REPEAT) for col in _elements(sheet, "column"))
    return max([explicit, *[sum(repeat for _, repeat, _ in _cells(row)) for _, _, row in _rows(sheet)]])


def _text(node):
    output = node.text or ""
    for child in node:
        if child.tag == X + "s":
            output += " " * _count(child, X + "c")
        elif child.tag == X + "tab":
            output += "\t"
        elif child.tag == X + "line-break":
            output += "\n"
        else:
            output += _text(child)
        output += child.tail or ""
    return output


def _display(cell):
    return "\n".join(_text(node) for node in cell if node.tag in {X + "p", X + "h"})


def _xml_name(name):
    if not name.startswith("{"):
        return name
    namespace, local = name[1:].split("}", 1)
    prefixes = {TEXT: "text", TABLE: "table", OFFICE: "office",
                "http://www.w3.org/1999/xlink": "xlink", "http://www.w3.org/XML/1998/namespace": "xml"}
    return prefixes.get(namespace, "{" + namespace + "}") + ":" + local


def _text_projection(node):
    """Include run formatting in semantic diffs, even when visible text is unchanged."""
    return {
        "tag": _xml_name(node.tag),
        "attributes": {_xml_name(key): value for key, value in sorted(node.attrib.items())},
        "text": node.text or "", "tail": node.tail or "",
        "children": [_text_projection(child) for child in node if isinstance(child.tag, str)],
    }


def _view(cell):
    kind = cell.get(O + "value-type")
    if kind in {"float", "percentage", "currency"}:
        raw = cell.get(O + "value")
        try:
            value = float(raw)
            if not math.isfinite(value):
                raise ValueError("Non-finite value")
        except (TypeError, ValueError, OverflowError) as exc:
            raise FileEditError("INVALID_ODS", "Numeric ODS cell needs a finite office:value") from exc
    elif kind == "boolean":
        raw = cell.get(O + "boolean-value")
        if raw not in {"true", "false", "1", "0"}:
            _fail("Invalid stored boolean", "INVALID_ODS")
        value = raw in {"true", "1"}
    elif kind == "string":
        value = cell.get(O + "string-value", _display(cell))
    elif kind in {"date", "time"}:
        value = cell.get(O + kind + "-value")
    elif kind is None:
        value = _display(cell) or None
    else:
        _fail(f"Unsupported ODS value type: {kind}")
    result = {"type": kind, "value": value, "style": cell.get(T + "style-name"),
              "covered": cell.tag == T + "covered-table-cell",
              "paragraphs": [_text_projection(node) for node in cell if node.tag in {X + "p", X + "h"}]}
    formula = cell.get(T + "formula")
    if formula is not None:
        result.update(formula=formula, cached_value=value, value=None, requires_recalculation=kind is None)
    return result


def _regions(sheet):
    for row_start, row_repeat, row in _rows(sheet):
        for col_start, col_repeat, cell in _cells(row):
            matrix = any(T + name in cell.attrib for name in (
                "number-matrix-rows-spanned", "number-matrix-columns-spanned",
            ))
            prefix = "number-matrix-" if matrix else "number-"
            height = _count(cell, T + prefix + "rows-spanned")
            width = _count(cell, T + prefix + "columns-spanned")
            if matrix or height > 1 or width > 1:
                if row_repeat > 1 or col_repeat > 1:
                    _fail("Repeated merged/matrix ranges are unsupported")
                yield row_start, col_start, row_start + height - 1, col_start + width - 1, matrix


def parse(data: bytes) -> OdsDocument:
    pkg = open_odf(data, "ods")
    root = pkg.xml("content.xml")
    spreadsheet = root.find(O + "body/" + O + "spreadsheet")
    if spreadsheet is None:
        _fail("ODS content must contain office:spreadsheet", "INVALID_ODS")
    sheets = {}
    for sheet in spreadsheet.findall(T + "table"):
        name = sheet.get(T + "name")
        if not name or name in sheets:
            _fail("Sheet names must be nonempty and unique", "INVALID_ODS")
        sheets[name] = sheet
        for _, _, row in _rows(sheet):
            for _, _, cell in _cells(row):
                _view(cell)
        list(_regions(sheet))
    if not sheets:
        _fail("ODS contains no sheets", "INVALID_ODS")
    return OdsDocument(pkg, root, sheets)


def summary(doc):
    sheets = []
    requires = False
    for name, sheet in doc.sheets.items():
        rows = list(_rows(sheet))
        formulas = 0
        for _, repeat, row in rows:
            for _, crepeat, cell in _cells(row):
                if cell.get(T + "formula") is not None:
                    formulas += repeat * crepeat
                    requires |= cell.get(O + "value-type") is None
        sheets.append({"name": name, "rows": sum(repeat for _, repeat, _ in rows),
                       "columns": _width(sheet), "stored_row_runs": len(rows), "formula_cells": formulas})
    return {"sheets": sheets, "requires_recalculation": requires, "formula_evaluation": "not performed"}


def _sheet_projection(sheet):
    return [{"row": start, "repeat": repeat,
             "cells": [{"column": column, "repeat": count, **_view(cell)}
                       for column, count, cell in _cells(row)]} for start, repeat, row in _rows(sheet)]


def projection(doc):
    return {"sheets": {name: {"rows": _sheet_projection(sheet)} for name, sheet in doc.sheets.items()},
            "requires_recalculation": summary(doc)["requires_recalculation"]}


def _locate(sheet, row_number, column):
    for row_start, repeat, row in _rows(sheet):
        if row_start <= row_number < row_start + repeat:
            for col_start, count, cell in _cells(row):
                if col_start <= column < col_start + count:
                    return row_start, repeat, row, col_start, count, cell
            return row_start, repeat, row, None, None, None
    _fail("Target row is outside the declared grid", "TARGET_NOT_FOUND")


def inspect(doc, pointer=""):
    if not isinstance(pointer, str):
        _fail("pointer must be a string", "INVALID_POINTER")
    parts = _pointer_parts(pointer)
    if not parts:
        return {"pointer": pointer, "value": summary(doc)}
    if len(parts) not in {2, 3} or parts[0] != "sheets" or parts[1] not in doc.sheets:
        _fail("Use /sheets/<name> or /sheets/<name>/A1:C8", "INVALID_POINTER")
    sheet = doc.sheets[parts[1]]
    if len(parts) == 2:
        rows = _sheet_projection(sheet)
        return {"pointer": pointer, "rows": rows[:200], "truncated": len(rows) > 200,
                "representation": "repeated row/cell runs"}
    references = parts[2].split(":")
    if len(references) not in {1, 2}:
        _fail("Expected an A1 cell or rectangular range", "INVALID_RANGE")
    first, last = _address(references[0]), _address(references[-1])
    if first[0] > last[0] or first[1] > last[1]:
        _fail("Range must run from top-left to bottom-right", "INVALID_RANGE")
    if (last[0] - first[0] + 1) * (last[1] - first[1] + 1) > 1000:
        _fail("Range inspection is limited to 1000 cells; use a smaller range", "INSPECTION_TOO_LARGE")
    values = {}
    height, width = sum(repeat for _, repeat, _ in _rows(sheet)), _width(sheet)
    for row in range(first[0], last[0] + 1):
        for column in range(first[1], last[1] + 1):
            if row > height or column > width:
                _fail("Inspection range is outside the declared grid", "TARGET_NOT_FOUND")
            cell = _locate(sheet, row, column)[5]
            values[_name(row, column)] = _view(cell) if cell is not None else {"type": None, "value": None}
    return {"pointer": pointer, "value": values, "requires_recalculation": summary(doc)["requires_recalculation"]}


def _splittable(node):
    for child in node.iter():
        if "{http://www.w3.org/XML/1998/namespace}id" in child.attrib or T + "formula" in child.attrib:
            _fail("Repeated runs with IDs or formulas cannot be split without rebasing references")
        if child.tag not in {T + "table-row", T + "table-cell", T + "covered-table-cell",
                             X + "p", X + "h", X + "span", X + "s", X + "tab", X + "line-break"}:
            _fail("Repeated runs with complex content cannot be split")


def _split(node, attribute, offset, count=1):
    total = _count(node, attribute)
    if offset == 0 and count == total:
        return node
    _splittable(node)
    parent, position = node.getparent(), node.getparent().index(node)
    pieces, selected = [], None
    for amount, middle in ((offset, False), (count, True), (total - offset - count, False)):
        if not amount:
            continue
        clone = deepcopy(node)
        if amount == 1:
            clone.attrib.pop(attribute, None)
        else:
            clone.set(attribute, str(amount))
        pieces.append(clone)
        if middle:
            selected = clone
    parent.remove(node)
    for i, piece in enumerate(pieces):
        parent.insert(position + i, piece)
    return selected


def _clear(cell):
    for attribute in VALUE_ATTRS:
        cell.attrib.pop(attribute, None)
    for child in list(cell):
        if child.tag in {X + "p", X + "h"}:
            cell.remove(child)


def _invalidate(doc):
    for sheet in doc.sheets.values():
        regions = [region for region in _regions(sheet) if region[4]]
        for top, left, bottom, right, _ in regions:
            for row_start, repeat, row in list(_rows(sheet)):
                start, end = max(top, row_start), min(bottom, row_start + repeat - 1)
                if start > end:
                    continue
                row = _split(row, ROW_REPEAT, start - row_start, end - start + 1)
                for col_start, count, cell in list(_cells(row)):
                    cstart, cend = max(left, col_start), min(right, col_start + count - 1)
                    if cstart <= cend:
                        _clear(_split(cell, COL_REPEAT, cstart - col_start, cend - cstart + 1))
        for _, _, row in _rows(sheet):
            for _, _, cell in _cells(row):
                if cell.get(T + "formula") is not None:
                    _clear(cell)


def _check_target(sheet, row_number, column, row, repeat, cell, count):
    if sheet.get(T + "protected") in {"true", "1"} or sheet.find(T + "table-source") is not None:
        _fail("Protected or externally sourced tables are not editable")
    for top, left, bottom, right, matrix in _regions(sheet):
        if top <= row_number <= bottom and left <= column <= right:
            if matrix:
                _fail("Matrix formula ranges cannot be edited one cell at a time")
            if (row_number, column) != (top, left):
                _fail("Only a merged range's anchor is editable", "MERGED_CELL")
    if repeat > 1:
        _splittable(row)
    if cell is not None:
        if cell.tag == T + "covered-table-cell":
            _fail("Covered cells are read-only", "MERGED_CELL")
        if cell.get(T + "protected") in {"true", "1"}:
            _fail("Protected cells are not editable")
        if any(child.tag not in {X + "p", X + "h", O + "annotation", T + "detective"} for child in cell):
            _fail("Cell contains a linked range, nested table or complex object")
        for paragraph in cell:
            if paragraph.tag in {X + "p", X + "h"} and any(
                child.tag not in {X + "span", X + "a", X + "s", X + "tab", X + "line-break"}
                for child in paragraph.iterdescendants() if isinstance(child.tag, str)
            ):
                _fail("Cell text contains fields, bookmarks or embedded objects")
        if count > 1:
            _splittable(cell)


def _paragraph(cell, text):
    p = etree.SubElement(cell, X + "p")
    for part in re.split(r"( +|\t|\n)", text):
        if not part:
            continue
        if part.startswith(" "):
            node = etree.SubElement(p, X + "s")
            if len(part) > 1:
                node.set(X + "c", str(len(part)))
        elif part == "\t":
            etree.SubElement(p, X + "tab")
        elif part == "\n":
            etree.SubElement(p, X + "line-break")
        elif len(p):
            p[-1].tail = (p[-1].tail or "") + part
        else:
            p.text = (p.text or "") + part


def edit(doc, step):
    if not isinstance(step, dict) or not isinstance(step.get("op"), str) or step["op"] not in OPS:
        _fail("Unsupported ODS operation", "INVALID_STEP")
    field = "formula" if step["op"] == "set-formula" else "value"
    fields = {"op", "sheet", "cell", field}
    _fields(step, fields, fields)
    if not isinstance(step["sheet"], str) or step["sheet"] not in doc.sheets:
        _fail("Worksheet not found", "TARGET_NOT_FOUND")
    row_number, column = _address(step["cell"])
    value = step[field]
    if field == "formula":
        if not isinstance(value, str) or not value.startswith("of:=") or not value[4:].strip():
            _fail("ODS formula must begin with of:= and use OpenFormula syntax", "INVALID_FORMULA")
    elif value is not None and type(value) not in {str, int, float, bool}:
        _fail("Cell value must be a JSON scalar or null", "INVALID_VALUE")
    if isinstance(value, str):
        if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]", value):
            _fail("Text contains invalid XML characters", "INVALID_VALUE")
        value = value.replace("\r\n", "\n").replace("\r", "\n")
    elif type(value) in {int, float}:
        try:
            if not math.isfinite(float(value)):
                raise ValueError()
        except (ValueError, OverflowError) as exc:
            raise FileEditError("INVALID_VALUE", "Numeric value must be finite") from exc
    sheet = doc.sheets[step["sheet"]]
    if column > _width(sheet):
        _fail("Target column is outside the declared grid", "TARGET_NOT_FOUND")
    start, repeat, row, col_start, count, cell = _locate(sheet, row_number, column)
    _check_target(sheet, row_number, column, row, repeat, cell, count)
    row = _split(row, ROW_REPEAT, row_number - start)
    # The row may have been cloned while splitting its repeat run.
    _, _, _, col_start, count, cell = _locate(sheet, row_number, column)
    if cell is None:
        width = sum(count for _, count, _ in _cells(row))
        if column > width + 1:
            blank = etree.SubElement(row, T + "table-cell")
            blank.set(COL_REPEAT, str(column - width - 1))
        cell = etree.SubElement(row, T + "table-cell")
    else:
        cell = _split(cell, COL_REPEAT, column - col_start)
    _clear(cell)
    cell.attrib.pop(T + "formula", None)
    if field == "formula":
        if cell.nsmap.get("of") != FORMULA:
            new = etree.Element(cell.tag, nsmap={**cell.nsmap, "of": FORMULA})
            new.attrib.update(cell.attrib)
            new.text, new.tail = cell.text, cell.tail
            for child in list(cell):
                new.append(child)
            cell.getparent().replace(cell, new)
            cell = new
        cell.set(T + "formula", value)
    elif value is not None:
        if type(value) is bool:
            cell.set(O + "value-type", "boolean")
            cell.set(O + "boolean-value", "true" if value else "false")
            _paragraph(cell, "TRUE" if value else "FALSE")
        elif isinstance(value, str):
            cell.set(O + "value-type", "string")
            cell.set(O + "string-value", value)
            _paragraph(cell, value)
        else:
            cell.set(O + "value-type", "float")
            cell.set(O + "value", str(value))
            _paragraph(cell, str(value))
    _invalidate(doc)
    doc.package.mark_changed("content.xml")
    return doc


def serialize(doc):
    output = doc.package.serialize()
    parse(output)
    return output
