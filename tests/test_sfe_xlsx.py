from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from lxml import etree

from sfe_files import xlsx_profile as xlsx
from sfe_files.core import FileEditError

S = xlsx.S
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"


def _fixture(extra: str = "", rows: str | None = None) -> bytes:
    if rows is None:
        rows = '''<row r="1"><c r="A1" t="s"><v>0</v></c>
          <c r="B1" t="b"><v>1</v></c><c r="C1" t="inlineStr"><is><r><t>rich</t></r>
          <r><t> text</t></r></is></c><c r="D1" t="str"><v>text</v></c>
          <c r="E1" t="e"><v>#N/A</v></c><c r="F1" t="d"><v>2026-09-26</v></c></row>
          <row r="2" spans="1:3"><c r="A2" s="1"><v>10</v></c>
          <c r="B2"><f>A2*2</f><v>20</v></c><c r="C2"><v>7</v></c></row>'''
    types = (
        f'<Types xmlns="{CT}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/calcChain.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.calcChain+xml"/>'
        '</Types>'
    )
    parts = {
        "[Content_Types].xml": types,
        "_rels/.rels": f'<Relationships xmlns="{PKG_REL}"><Relationship Id="rId1" Type="{xlsx.REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": f'<workbook xmlns="{xlsx.NS}" xmlns:r="{xlsx.REL}"><sheets><sheet name="Budget" sheetId="1" r:id="s1"/><sheet name="Other" sheetId="2" r:id="s2"/></sheets><calcPr calcMode="manual"/></workbook>',
        "xl/_rels/workbook.xml.rels": f'<Relationships xmlns="{PKG_REL}"><Relationship Id="s1" Type="{xlsx.REL}/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="s2" Type="{xlsx.REL}/worksheet" Target="worksheets/sheet2.xml"/><Relationship Id="ss" Type="{xlsx.REL}/sharedStrings" Target="sharedStrings.xml"/><Relationship Id="cc" Type="{xlsx.REL}/calcChain" Target="calcChain.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": f'<worksheet xmlns="{xlsx.NS}"><dimension ref="A1:F2"/><sheetData>{rows}</sheetData>{extra}</worksheet>',
        "xl/worksheets/sheet2.xml": f'<worksheet xmlns="{xlsx.NS}"><sheetData><row r="1"><c r="A1"><f>Budget!A2</f><v>10</v></c></row></sheetData></worksheet>',
        "xl/sharedStrings.xml": f'<sst xmlns="{xlsx.NS}" count="1" uniqueCount="1"><si><t>Shared label</t></si></sst>',
        "xl/styles.xml": f'<styleSheet xmlns="{xlsx.NS}"/><!-- preserved bytes -->',
        "xl/calcChain.xml": f'<calcChain xmlns="{xlsx.NS}"><c r="B2" i="1"/></calcChain>',
        "xl/media/image1.png": b"opaque image payload\0\1\2",
    }
    output = io.BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return output.getvalue()


def _values(data):
    return xlsx.projection(xlsx.parse(data))["sheets"]


def test_inspection_supports_stored_types_and_exposes_formula_cache():
    document = xlsx.parse(_fixture())
    values = xlsx.inspect(document, "/sheets/Budget/A1:F2")["value"]
    assert values["A1"]["value"] == "Shared label"
    assert values["B1"]["value"] is True
    assert values["C1"]["value"] == "rich text"
    assert values["D1"]["value"] == "text"
    assert values["E1"]["value"] == "#N/A"
    assert values["F1"]["value"] == "2026-09-26"
    assert values["B2"]["formula"] == "=A2*2"
    assert values["B2"]["cached_value"] == 20
    assert values["B2"]["value"] is None
    assert xlsx.summary(document)["date_system"] == "1900"
    assert xlsx.serialize(document) == _fixture()


def test_cell_update_preserves_style_other_parts_and_invalidates_formula_caches():
    original = _fixture()
    document = xlsx.parse(original)
    xlsx.edit(document, {"op": "set-cell", "sheet": "Budget", "cell": "A2", "value": 22})
    result = xlsx.serialize(document)
    values = _values(result)
    assert values["Budget"]["A2"]["value"] == 22
    assert values["Budget"]["A2"]["style_id"] == "1"
    assert values["Budget"]["B2"]["cached_value"] is None
    assert values["Other"]["A1"]["cached_value"] is None
    with ZipFile(io.BytesIO(original)) as old, ZipFile(io.BytesIO(result)) as new:
        for name in ("xl/sharedStrings.xml", "xl/styles.xml", "xl/media/image1.png", "_rels/.rels"):
            assert new.read(name) == old.read(name)
        assert "xl/calcChain.xml" not in new.namelist()
        assert b"calcChain" not in new.read("xl/_rels/workbook.xml.rels")
        assert b"calcChain" not in new.read("[Content_Types].xml")
        calc = etree.fromstring(new.read("xl/workbook.xml")).find(S + "calcPr")
        assert calc.get("fullCalcOnLoad") == "1"
        assert calc.get("forceFullCalc") == "1"
        assert calc.get("calcMode") == "auto"


@pytest.mark.parametrize("value", [None, True, False, 0, -1, 3.125, "", "中文", "=SUM(A1:A2)", "_x000A_\n\0"])
def test_scalar_values_round_trip_and_do_not_execute_formula_text(value):
    document = xlsx.parse(_fixture())
    xlsx.edit(document, {"op": "set-cell", "sheet": "Budget", "cell": "A2", "value": value})
    cell = _values(xlsx.serialize(document))["Budget"]["A2"]
    assert cell["value"] == value
    assert cell["style_id"] == "1"
    assert "formula" not in cell


def test_new_cell_placement_and_formula_change():
    document = xlsx.parse(_fixture())
    for step in [
        {"op": "set-cell", "sheet": "Budget", "cell": "B3", "value": 9},
        {"op": "set-cell", "sheet": "Budget", "cell": "AA2", "value": 4},
        {"op": "set-formula", "sheet": "Budget", "cell": "B2", "formula": "=SUM(A2:C2)"},
    ]:
        xlsx.edit(document, step)
    result = xlsx.serialize(document)
    values = _values(result)["Budget"]
    assert values["B3"]["value"] == 9
    assert values["AA2"]["value"] == 4
    assert values["B2"]["formula"] == "=SUM(A2:C2)"
    assert values["B2"]["cached_value"] is None
    with ZipFile(io.BytesIO(result)) as archive:
        root = etree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        assert root.find(S + "dimension") is None
        assert root.find(S + "sheetData")[1][-1].get("r") == "AA2"


@pytest.mark.parametrize("cell", ["a1", "A0", "A01", "$A$1", "XFE1", "A1048577", "Sheet1!A1", "A1:B2", None])
def test_invalid_addresses_rejected(cell):
    document = xlsx.parse(_fixture())
    with pytest.raises(FileEditError):
        xlsx.edit(document, {"op": "set-cell", "sheet": "Budget", "cell": cell, "value": 3})


@pytest.mark.parametrize("formula", ["", "=", "SUM(A1)", "=A1\0", 4])
def test_invalid_formula_rejected(formula):
    with pytest.raises(FileEditError):
        xlsx.edit(xlsx.parse(_fixture()), {"op": "set-formula", "sheet": "Budget", "cell": "A2", "formula": formula})


@pytest.mark.parametrize("formula_type", ["shared", "array", "dataTable"])
def test_grouped_formula_ranges_cannot_be_overwritten_at_anchor_or_follower(formula_type):
    data = _fixture(rows=f'<row r="2"><c r="B2"><f t="{formula_type}" ref="B2:C3">A1</f><v>1</v></c></row>')
    for address in ("B2", "C3"):
        document = xlsx.parse(data)
        with pytest.raises(FileEditError, match="formula"):
            xlsx.edit(document, {"op": "set-cell", "sheet": "Budget", "cell": address, "value": 2})
        assert xlsx.serialize(document) == data


def test_merged_anchor_supported_and_other_cells_rejected():
    data = _fixture(extra='<mergeCells count="1"><mergeCell ref="A5:B5"/></mergeCells>')
    document = xlsx.parse(data)
    with pytest.raises(FileEditError) as error:
        xlsx.edit(document, {"op": "set-cell", "sheet": "Budget", "cell": "B5", "value": "bad"})
    assert error.value.code == "MERGED_CELL"
    xlsx.edit(document, {"op": "set-cell", "sheet": "Budget", "cell": "A5", "value": "good"})
    assert _values(xlsx.serialize(document))["Budget"]["A5"]["value"] == "good"


def test_array_follower_caches_invalidated_when_other_input_changes():
    rows = '<row r="1"><c r="A1"><v>2</v></c><c r="B1"><f t="array" ref="B1:C1">A1*{1,2}</f><v>2</v></c><c r="C1"><v>4</v></c></row>'
    document = xlsx.parse(_fixture(rows=rows))
    xlsx.edit(document, {"op": "set-cell", "sheet": "Budget", "cell": "A1", "value": 3})
    assert _values(xlsx.serialize(document))["Budget"]["C1"]["value"] is None


def test_table_body_input_is_editable_but_headers_totals_and_calculated_columns_are_not():
    extra = f'<tableParts count="1"><tablePart xmlns:r="{xlsx.REL}" r:id="table1"/></tableParts>'
    doc = xlsx.parse(_fixture(extra=extra))
    doc.package.write("xl/worksheets/_rels/sheet1.xml.rels", (
        f'<Relationships xmlns="{PKG_REL}"><Relationship Id="table1" Type="{xlsx.REL}/table" '
        'Target="../tables/table1.xml"/></Relationships>'
    ).encode())
    doc.package.write("xl/tables/table1.xml", (
        f'<table xmlns="{xlsx.NS}" id="1" name="Table1" displayName="Table1" ref="A1:C4" '
        'totalsRowCount="1"><tableColumns count="3"><tableColumn id="1" name="Input"/>'
        '<tableColumn id="2" name="Calculation"><calculatedColumnFormula>A2*2</calculatedColumnFormula>'
        '</tableColumn><tableColumn id="3" name="Other"/></tableColumns></table>'
    ).encode())
    types = doc.package.xml("[Content_Types].xml")
    etree.SubElement(types, "{" + CT + "}Override", PartName="/xl/tables/table1.xml",
                     ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.table+xml")
    doc.package.mark_changed("[Content_Types].xml")
    for address in ["A1", "C4", "B2"]:
        with pytest.raises(FileEditError, match="Table"):
            xlsx.edit(doc, {"op": "set-cell", "sheet": "Budget", "cell": address, "value": 8})
    xlsx.edit(doc, {"op": "set-cell", "sheet": "Budget", "cell": "A2", "value": 8})
    assert _values(xlsx.serialize(doc))["Budget"]["A2"]["value"] == 8


@pytest.mark.parametrize("data", [
    _fixture(extra='<sheetProtection sheet="1"/>'),
    _fixture(rows='<row r="2"><c r="A2" cm="1"><v>1</v></c></row>'),
])
def test_protected_or_metadata_targets_rejected(data):
    document = xlsx.parse(data)
    with pytest.raises(FileEditError):
        xlsx.edit(document, {"op": "set-cell", "sheet": "Budget", "cell": "A2", "value": 1})
    assert xlsx.serialize(document) == data


@pytest.mark.parametrize("value", [[], {}, float("inf"), float("nan"), "a" * 32768],
                         ids=["list", "mapping", "infinity", "nan", "long-text"])
def test_unsupported_values_do_not_mutate_document(value):
    data = _fixture()
    document = xlsx.parse(data)
    with pytest.raises(FileEditError):
        xlsx.edit(document, {"op": "set-cell", "sheet": "Budget", "cell": "A2", "value": value})
    assert xlsx.serialize(document) == data


def test_example_plan_matches_fixture_and_applies():
    directory = Path(__file__).resolve().parents[1] / "examples" / "sfe"
    data = (directory / "budget.xlsx").read_bytes()
    plan = json.loads((directory / "budget.plan.json").read_text(encoding="utf-8"))
    assert plan["base"]["sha256"] == hashlib.sha256(data).hexdigest()
    doc = xlsx.parse(data)
    for step in plan["steps"]:
        xlsx.edit(doc, step)
    edited = _values(xlsx.serialize(doc))["Budget"]
    assert edited["B5"]["value"] == 1800
    assert edited["C7"]["formula"] == "=SUM(C5:C6)"
    assert edited["A5"]["value"] == "HMI controls"
