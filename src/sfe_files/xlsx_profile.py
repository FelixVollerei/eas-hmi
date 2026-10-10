"""Targeted SpreadsheetML cell edits that preserve unrelated OOXML parts."""

from __future__ import annotations

import math
import posixpath
import re
from dataclasses import dataclass
from typing import Any

from lxml import etree

from .ooxml import Package
from .plan_ops import FileEditError, _fields, _pointer_parts

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
S = "{" + NS + "}"
WORKBOOK = "xl/workbook.xml"
WORKBOOK_RELS = "xl/_rels/workbook.xml.rels"
OPS = {"set-cell", "set-formula"}
CONTRACT = {
    "addressing": {
        "kind": "Worksheet name and A1 cell reference",
        "inspect": "/sheets/Budget/A1:C8",
        "escaping": "Escape sheet-name / as ~1 and ~ as ~0 in inspection pointers.",
    },
    "operations": {
        "set-cell": {
            "example": {"op": "set-cell", "sheet": "Budget", "cell": "B5", "value": 1800},
            "description": "Set a string, finite number, boolean or null; null clears content, retaining style.",
        },
        "set-formula": {
            "example": {"op": "set-formula", "sheet": "Budget", "cell": "C7", "formula": "=SUM(C5:C6)"},
            "description": "Write a normal Excel formula beginning with =; no formula evaluation is performed.",
        },
    },
    "limitations": [
        "Transitional .xlsx worksheets only; macros, signed/encrypted packages and Strict OOXML are unsupported.",
        "No row/column insertion, sheet creation, style editing or formula evaluation.",
        "Dates expose stored ISO text or numeric serials; style IDs and workbook date system are inspectable.",
        "Shared/array/data-table formulas, table headers/totals/calculated columns, metadata and protected sheets reject edits.",
        "Merged cells can be changed only through their top-left anchor.",
        "Edits clear formula caches, delete the calculation chain and request full automatic recalculation on open.",
        "Charts, pivots and other linked-object caches are preserved; refresh them in a supporting spreadsheet engine.",
        "Untouched ZIP member payloads remain byte-identical. New strings use inline strings; shared strings stay intact.",
        "Replacing rich text with set-cell writes plain text while retaining the cell style.",
    ],
}


@dataclass
class Sheet:
    name: str
    part: str
    root: Any
    cells: dict[str, Any]


@dataclass
class XlsxDocument:
    package: Package
    workbook: Any
    sheets: dict[str, Sheet]
    strings: list[str]


def _fail(message: str, code: str = "UNSUPPORTED_XLSX"):
    raise FileEditError(code, message)


def _address(value: str) -> tuple[int, int]:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z]{1,3}[1-9][0-9]{0,6}", value):
        _fail("Use an uppercase A1 reference without $ or a sheet prefix", "INVALID_CELL")
    split = re.fullmatch(r"([A-Z]+)([0-9]+)", value)
    column = 0
    for char in split[1]:
        column = column * 26 + ord(char) - 64
    row = int(split[2])
    if column > 16384 or row > 1048576:
        _fail("Cell is outside Excel's worksheet bounds", "INVALID_CELL")
    return row, column


def _range(value: str) -> tuple[int, int, int, int]:
    parts = value.split(":") if isinstance(value, str) else []
    if len(parts) not in (1, 2):
        _fail("Expected a cell or rectangular A1 range", "INVALID_RANGE")
    start, end = _address(parts[0]), _address(parts[-1])
    if start[0] > end[0] or start[1] > end[1]:
        _fail("Range endpoints must be top-left and bottom-right", "INVALID_RANGE")
    return *start, *end


def _inside(address: str, bounds: tuple[int, int, int, int]) -> bool:
    row, col = _address(address)
    return bounds[0] <= row <= bounds[2] and bounds[1] <= col <= bounds[3]


def _decode(text: str) -> str:
    decoded = re.sub(r"_x([0-9a-fA-F]{4})_", lambda m: chr(int(m[1], 16)), text)
    try:
        return decoded.encode("utf-16-le", "surrogatepass").decode("utf-16-le")
    except UnicodeError as exc:
        raise FileEditError("INVALID_XLSX", "Invalid escaped Unicode string") from exc


def _encode(text: str) -> str:
    try:
        if len(text.encode("utf-16-le")) // 2 > 32767:
            _fail("Cell text exceeds 32767 UTF-16 units", "INVALID_VALUE")
    except UnicodeError as exc:
        raise FileEditError("INVALID_VALUE", "Cell text contains invalid Unicode") from exc
    text = re.sub(r"_x[0-9a-fA-F]{4}_", lambda match: "_x005F_" + match[0][1:], text)
    return "".join(
        f"_x{ord(char):04X}_" if ord(char) < 32 and char not in "\t\n\r" or char in "\ufffe\uffff" else char
        for char in text
    )


def _text(node) -> str:
    return _decode("".join(node.xpath("./s:t/text() | ./s:r/s:t/text()", namespaces={"s": NS})))


def _cell_view(doc: XlsxDocument, cell) -> dict:
    kind = cell.get("t", "n")
    if kind not in {"n", "s", "str", "b", "e", "d", "inlineStr"}:
        _fail(f"Unsupported cell type: {kind}")
    raw = cell.findtext(S + "v")
    if kind == "inlineStr":
        inline = cell.find(S + "is")
        value = _text(inline) if inline is not None else ""
    elif raw is None:
        value = None
    elif kind == "s":
        if not re.fullmatch(r"0|[1-9][0-9]*", raw) or int(raw) >= len(doc.strings):
            _fail("Invalid shared-string reference", "INVALID_XLSX")
        value = doc.strings[int(raw)]
    elif kind in {"str", "e", "d"}:
        value = _decode(raw)
    elif kind == "b":
        if raw not in {"0", "1"}:
            _fail("Invalid boolean cell value", "INVALID_XLSX")
        value = raw == "1"
    elif kind == "n":
        try:
            value = int(raw) if re.fullmatch(r"[+-]?[0-9]+", raw) else float(raw)
            if not math.isfinite(float(value)):
                raise ValueError("Non-finite number")
        except (ValueError, OverflowError) as exc:
            raise FileEditError("INVALID_XLSX", "Invalid numeric cell value") from exc
    else:
        _fail(f"Unsupported cell type: {kind}")
    out = {"type": kind, "value": value, "style_id": cell.get("s")}
    formula = cell.find(S + "f")
    if formula is not None:
        out.update(
            formula="=" + formula.text if formula.text else None,
            formula_type=formula.get("t", "normal"),
            formula_range=formula.get("ref"),
            cached_value=value,
            value=None,
        )
    return out


def parse(data: bytes) -> XlsxDocument:
    pkg = Package.open(data, "xlsx")
    main = pkg.main_part
    rels_part = _rels_part(main)
    workbook = pkg.xml(main)
    if workbook.tag != S + "workbook":
        _fail("Only Transitional SpreadsheetML workbooks are supported")
    related = pkg.related(main)
    strings = []
    if pkg.has(rels_part):
        for rel in pkg.xml(rels_part):
            if rel.get("Type", "").endswith("/sharedStrings"):
                part = related.get(rel.get("Id"))
                if not part:
                    _fail("Missing internal shared-string relationship", "INVALID_XLSX")
                strings = [_text(node) for node in pkg.xml(part).findall(S + "si")]
    sheets = {}
    for node in workbook.findall(S + "sheets/" + S + "sheet"):
        name = node.get("name")
        part = related.get(node.get("{" + REL + "}id"))
        if not name or name in sheets or not part:
            _fail("Worksheet names and relationships must be unique and valid", "INVALID_XLSX")
        root = pkg.xml(part)
        if root.tag != S + "worksheet":
            _fail("Only normal worksheets are supported")
        blocks = root.findall(S + "sheetData")
        if len(blocks) != 1:
            _fail("Expected one worksheet sheetData element", "INVALID_XLSX")
        cells = {}
        rows = set()
        for row in blocks[0]:
            if row.tag != S + "row" or not re.fullmatch(r"[1-9][0-9]*", row.get("r", "")):
                _fail("Rows must have explicit valid addresses", "INVALID_XLSX")
            row_number = int(row.get("r"))
            if row_number in rows or row_number > 1048576:
                _fail("Duplicate or out-of-bounds row", "INVALID_XLSX")
            rows.add(row_number)
            for cell in row:
                address = cell.get("r")
                if cell.tag != S + "c" or not address or address in cells:
                    _fail("Cells must have unique explicit addresses", "INVALID_XLSX")
                if _address(address)[0] != row_number:
                    _fail("Cell and row addresses disagree", "INVALID_XLSX")
                if len(cell.findall(S + "f")) > 1 or len(cell.findall(S + "v")) > 1:
                    _fail("Duplicate formula or value element", "INVALID_XLSX")
                cells[address] = cell
        sheets[name] = Sheet(name, part, root, cells)
    if not sheets:
        _fail("Workbook has no worksheets", "INVALID_XLSX")
    doc = XlsxDocument(pkg, workbook, sheets, strings)
    for sheet in sheets.values():
        for cell in sheet.cells.values():
            _cell_view(doc, cell)
    return doc


def summary(doc: XlsxDocument) -> dict:
    props = doc.workbook.find(S + "workbookPr")
    return {
        "sheets": [
            {"name": sheet.name, "cell_count": len(sheet.cells), "part": sheet.part}
            for sheet in doc.sheets.values()
        ],
        "date_system": "1904" if props is not None and props.get("date1904") in {"1", "true"} else "1900",
        "formula_evaluation": "not performed; cached values are historical",
    }


def projection(doc: XlsxDocument) -> dict:
    return {
        "sheets": {
            sheet.name: {address: _cell_view(doc, cell) for address, cell in sheet.cells.items()}
            for sheet in doc.sheets.values()
        }
    }


def inspect(doc: XlsxDocument, pointer: str = "") -> dict:
    if not isinstance(pointer, str):
        _fail("pointer must be a string", "INVALID_POINTER")
    parts = _pointer_parts(pointer)
    if not parts:
        return {"pointer": pointer, "value": summary(doc)}
    if len(parts) not in {2, 3} or parts[0] != "sheets" or parts[1] not in doc.sheets:
        _fail("Use /sheets/<sheet-name> or /sheets/<sheet-name>/A1:C8", "TARGET_NOT_FOUND")
    sheet = doc.sheets[parts[1]]
    bounds = _range(parts[2]) if len(parts) == 3 else None
    cells = [(addr, cell) for addr, cell in sheet.cells.items() if bounds is None or _inside(addr, bounds)]
    cells.sort(key=lambda item: _address(item[0]))
    return {
        "pointer": pointer,
        "value": {address: _cell_view(doc, cell) for address, cell in cells[:1000]},
        "cell_count": len(cells),
        "truncated": len(cells) > 1000,
        "blank_cells": "Unlisted addresses are empty; formulas expose cached_value separately.",
    }


def _check_target(doc: XlsxDocument, sheet: Sheet, address: str):
    if sheet.root.find(S + "sheetProtection") is not None:
        _fail("Editing protected worksheets is not supported")
    for merge in sheet.root.findall(S + "mergeCells/" + S + "mergeCell"):
        ref = merge.get("ref", "")
        if _inside(address, _range(ref)) and address != ref.split(":")[0]:
            _fail("Only a merged range's top-left cell is editable", "MERGED_CELL")
    for cell in sheet.cells.values():
        formula = cell.find(S + "f")
        if formula is not None and (formula.get("t", "normal") != "normal" or formula.get("ref")):
            ref = formula.get("ref", cell.get("r"))
            if _inside(address, _range(ref)):
                _fail("Shared, array and data-table formula cells cannot be edited independently")
    cell = sheet.cells.get(address)
    if cell is not None:
        if set(cell.attrib) - {"r", "s", "t"}:
            _fail("Cell metadata and extended cell attributes are not supported edit targets")
        if any(child.tag not in {S + "f", S + "v", S + "is"} for child in cell):
            _fail("Extended cell structures are not supported edit targets")
    related = doc.package.related(sheet.part)
    for node in sheet.root.findall(S + "tableParts/" + S + "tablePart"):
        part = related.get(node.get("{" + REL + "}id"))
        if not part:
            _fail("Invalid worksheet table relationship", "INVALID_XLSX")
        table = doc.package.xml(part)
        bounds = _range(table.get("ref", ""))
        if _inside(address, bounds):
            row, column = _address(address)
            try:
                headers = int(table.get("headerRowCount", "1"))
                totals = int(table.get("totalsRowCount", "0"))
            except ValueError as exc:
                raise FileEditError("INVALID_XLSX", "Invalid table header or totals row count") from exc
            if row < bounds[0] + headers or row > bounds[2] - totals:
                _fail("Table headers and totals require a table-aware operation")
            columns = table.findall(S + "tableColumns/" + S + "tableColumn")
            index = column - bounds[1]
            if len(columns) != bounds[3] - bounds[1] + 1:
                _fail("Table column count disagrees with its range", "INVALID_XLSX")
            if columns[index].find(S + "calculatedColumnFormula") is not None:
                _fail("Table calculated columns cannot be edited independently")


def _ensure_cell(sheet: Sheet, address: str):
    if address in sheet.cells:
        return sheet.cells[address]
    row_number, column = _address(address)
    data = sheet.root.find(S + "sheetData")
    rows = [row for row in data if int(row.get("r")) == row_number]
    if rows:
        row = rows[0]
    else:
        row = etree.Element(S + "row", r=str(row_number))
        index = next((i for i, other in enumerate(data) if int(other.get("r")) > row_number), len(data))
        data.insert(index, row)
    cell = etree.Element(S + "c", r=address)
    index = next((i for i, other in enumerate(row) if _address(other.get("r"))[1] > column), len(row))
    row.insert(index, cell)
    # spans is an optional optimization; remove it rather than leave a stale column span.
    row.attrib.pop("spans", None)
    sheet.cells[address] = cell
    dimension = sheet.root.find(S + "dimension")
    if dimension is not None:
        # An omitted dimension is valid and avoids shrinking formatted/merged ranges.
        sheet.root.remove(dimension)
    return cell


def _invalidate(doc: XlsxDocument):
    pkg = doc.package
    main = pkg.main_part
    rels_part = _rels_part(main)
    for sheet in doc.sheets.values():
        ranges = [
            _range(formula.get("ref"))
            for cell in sheet.cells.values()
            if (formula := cell.find(S + "f")) is not None and formula.get("ref")
        ]
        for addr, cell in sheet.cells.items():
            if cell.find(S + "f") is not None or any(_inside(addr, bounds) for bounds in ranges):
                values = cell.findall(S + "v")
                for value in values:
                    cell.remove(value)
                if values or "t" in cell.attrib:
                    cell.attrib.pop("t", None)
                    pkg.mark_changed(sheet.part)
    calc = doc.workbook.find(S + "calcPr")
    if calc is None:
        calc = etree.Element(S + "calcPr")
        following = {S + tag for tag in (
            "oleSize", "customWorkbookViews", "pivotCaches", "smartTagPr", "smartTagTypes",
            "webPublishing", "fileRecoveryPr", "webPublishObjects", "extLst",
        )}
        index = next((i for i, child in enumerate(doc.workbook) if child.tag in following), len(doc.workbook))
        doc.workbook.insert(index, calc)
    calc.set("calcMode", "auto")
    calc.set("fullCalcOnLoad", "1")
    calc.set("forceFullCalc", "1")
    pkg.mark_changed(main)
    removed = set()
    if pkg.has(rels_part):
        rels = pkg.xml(rels_part)
        related = pkg.related(main)
        for rel in list(rels):
            if rel.get("Type", "").endswith("/calcChain"):
                part = related.get(rel.get("Id"))
                if part:
                    pkg.delete(part)
                    removed.add("/" + part)
                rels.remove(rel)
                pkg.mark_changed(rels_part)
    if removed:
        content = pkg.xml("[Content_Types].xml")
        for node in list(content):
            if node.get("PartName") in removed:
                content.remove(node)
                pkg.mark_changed("[Content_Types].xml")


def edit(doc: XlsxDocument, step: dict) -> XlsxDocument:
    if not isinstance(step, dict) or not isinstance(step.get("op"), str) or step["op"] not in OPS:
        _fail("Unsupported XLSX operation", "INVALID_STEP")
    content = "value" if step["op"] == "set-cell" else "formula"
    fields = {"op", "sheet", "cell", content}
    _fields(step, fields, fields)
    if not isinstance(step["sheet"], str) or step["sheet"] not in doc.sheets:
        _fail("Worksheet not found", "TARGET_NOT_FOUND")
    address = step["cell"]
    _address(address)
    value = step[content]
    if content == "formula":
        if not isinstance(value, str) or not value.startswith("=") or not value[1:].strip():
            _fail("formula must be a nonempty Excel formula beginning with =", "INVALID_FORMULA")
        if len(value) > 8192 or any(ord(char) < 32 or char in "\ufffe\uffff" for char in value):
            _fail("Formula contains unsupported characters or exceeds 8192 characters", "INVALID_FORMULA")
        try:
            value.encode("utf-8")
        except UnicodeError as exc:
            raise FileEditError("INVALID_FORMULA", "Formula contains invalid Unicode") from exc
    elif isinstance(value, str):
        value = _encode(value)
    elif value is not None and type(value) not in {int, float, bool}:
        _fail("Cell value must be a JSON scalar or null", "INVALID_VALUE")
    elif type(value) in {int, float}:
        try:
            finite = math.isfinite(float(value))
        except OverflowError:
            finite = False
        if not finite:
            _fail("Cell number must be finite and within the Excel numeric range", "INVALID_VALUE")
    sheet = doc.sheets[step["sheet"]]
    _check_target(doc, sheet, address)
    cell = _ensure_cell(sheet, address)
    for child in list(cell):
        cell.remove(child)
    cell.attrib.pop("t", None)
    if content == "formula":
        etree.SubElement(cell, S + "f").text = value[1:]
    elif isinstance(value, str):
        cell.set("t", "inlineStr")
        node = etree.SubElement(etree.SubElement(cell, S + "is"), S + "t")
        node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        node.text = value
    elif value is not None:
        cell.set("t", "b" if type(value) is bool else "n")
        etree.SubElement(cell, S + "v").text = ("1" if value else "0") if type(value) is bool else str(value)
    doc.package.mark_changed(sheet.part)
    _invalidate(doc)
    return doc


def serialize(doc: XlsxDocument) -> bytes:
    result = doc.package.serialize()
    parse(result)
    return result


def _rels_part(part: str) -> str:
    return posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels")
