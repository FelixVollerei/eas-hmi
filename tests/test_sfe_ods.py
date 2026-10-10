from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

import pytest
from lxml import etree

from sfe_files import ods_profile as ods
from sfe_files.core import FileEditError

MIME = "application/vnd.oasis.opendocument.spreadsheet"
MANIFEST = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
STYLE = "urn:oasis:names:tc:opendocument:xmlns:style:1.0"
NUMBER = "urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0"
FO = "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"


def _fixture(rows=None, *, columns=3, other_sheet=True):
    if rows is None:
        rows = '''<table:table-row>
          <table:table-cell office:value-type="string"><text:p>Item</text:p></table:table-cell>
          <table:table-cell office:value-type="string"><text:p>Unit cost</text:p></table:table-cell>
          <table:table-cell office:value-type="string"><text:p>Amount</text:p></table:table-cell>
        </table:table-row><table:table-row>
          <table:table-cell office:value-type="string"><text:p>Engineering</text:p></table:table-cell>
          <table:table-cell table:style-name="ce1" office:value-type="float" office:value="100"><text:p>100</text:p></table:table-cell>
          <table:table-cell table:style-name="ce1" table:formula="of:=[.B2]*2" office:value-type="float" office:value="200"><text:p>200</text:p></table:table-cell>
        </table:table-row><table:table-row table:number-rows-repeated="2">
          <table:table-cell office:value-type="string"><text:p>Spare parts</text:p></table:table-cell>
          <table:table-cell table:number-columns-repeated="2" table:style-name="ce1" office:value-type="float" office:value="50"><text:p>50</text:p></table:table-cell>
        </table:table-row><table:table-row>
          <table:table-cell office:value-type="string"><text:p>Total</text:p></table:table-cell>
          <table:table-cell/>
          <table:table-cell table:style-name="ce1" table:formula="of:=SUM([.C2:.C4])" office:value-type="float" office:value="300"><text:p>300</text:p></table:table-cell>
        </table:table-row>'''
    namespaces = (f'xmlns:office="{ods.OFFICE}" xmlns:table="{ods.TABLE}" xmlns:text="{ods.TEXT}" '
                  f'xmlns:of="{ods.FORMULA}" xmlns:style="{STYLE}" xmlns:number="{NUMBER}" xmlns:fo="{FO}"')
    other = ('<table:table table:name="Other"><table:table-row><table:table-cell table:formula="of:=[Costs.B2]" '
             'office:value-type="float" office:value="100"><text:p>100</text:p></table:table-cell>'
             '</table:table-row></table:table>') if other_sheet else ""
    content = (
        f'<office:document-content {namespaces} office:version="1.3"><office:automatic-styles>'
        '<number:number-style style:name="n1"><number:number number:decimal-places="0"/></number:number-style>'
        '<style:style style:name="ce1" style:family="table-cell" style:data-style-name="n1">'
        '<style:table-cell-properties fo:background-color="#F5F7FA"/></style:style>'
        '</office:automatic-styles><office:body><office:spreadsheet><table:table table:name="Costs">'
        f'<table:table-column table:number-columns-repeated="{columns}"/>{rows}</table:table>{other}'
        '</office:spreadsheet></office:body></office:document-content>'
    )
    manifest = (
        f'<manifest:manifest xmlns:manifest="{MANIFEST}" manifest:version="1.3">'
        f'<manifest:file-entry manifest:full-path="/" manifest:media-type="{MIME}"/>'
        '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
        '<manifest:file-entry manifest:full-path="styles.xml" manifest:media-type="text/xml"/>'
        '<manifest:file-entry manifest:full-path="meta.xml" manifest:media-type="text/xml"/>'
        '</manifest:manifest>'
    )
    styles = (f'<office:document-styles {namespaces} office:version="1.3"><office:styles>'
              '<style:default-style style:family="table-cell"><style:text-properties fo:font-size="10pt"/>'
              '</style:default-style></office:styles></office:document-styles>')
    meta = (f'<office:document-meta xmlns:office="{ods.OFFICE}" '
            'xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0" office:version="1.3">'
            '<office:meta><meta:generator>SFE illustrative fixture</meta:generator></office:meta>'
            '</office:document-meta>')
    result = io.BytesIO()
    with ZipFile(result, "w") as archive:
        archive.writestr("mimetype", MIME, compress_type=ZIP_STORED)
        for name, value in {"META-INF/manifest.xml": manifest, "content.xml": content,
                            "styles.xml": styles, "meta.xml": meta}.items():
            archive.writestr(name, value.encode("utf-8"), compress_type=ZIP_DEFLATED)
    return result.getvalue()


def _get(data, cell, sheet="Costs"):
    return ods.inspect(ods.parse(data), f"/sheets/{sheet}/{cell}")["value"][cell]


def _root(data):
    with ZipFile(io.BytesIO(data)) as archive:
        return etree.fromstring(archive.read("content.xml"))


def test_reads_formula_cache_and_compact_repeated_rows_without_expanding():
    data = _fixture()
    doc = ods.parse(data)
    info = ods.summary(doc)
    assert info["sheets"][0] == {"name": "Costs", "rows": 5, "columns": 3,
                                 "stored_row_runs": 4, "formula_cells": 2}
    assert _get(data, "B3")["value"] == _get(data, "B4")["value"] == 50
    formula = _get(data, "C5")
    assert formula["formula"] == "of:=SUM([.C2:.C4])" and formula["cached_value"] == 300
    assert formula["value"] is None
    assert ods.serialize(doc) == data


def test_edit_splits_only_needed_runs_preserves_styles_and_payloads():
    data = _fixture()
    doc = ods.parse(data)
    ods.edit(doc, {"op": "set-cell", "sheet": "Costs", "cell": "B3", "value": 75})
    result = ods.serialize(doc)
    assert _get(result, "B3")["value"] == 75
    assert _get(result, "B3")["style"] == "ce1"
    assert all(_get(result, cell)["value"] == 50 for cell in ["C3", "B4", "C4"])
    root = _root(result)
    table = root.find(ods.O + "body/" + ods.O + "spreadsheet/" + ods.T + "table")
    rows = table.findall(ods.T + "table-row")
    assert len(rows) == 5
    assert len(rows[2]) == 3 and rows[2][1].get(ods.O + "value") == "75"
    assert rows[3][1].get(ods.COL_REPEAT) == "2"
    with ZipFile(io.BytesIO(data)) as old, ZipFile(io.BytesIO(result)) as new:
        assert new.infolist()[0].filename == "mimetype" and new.infolist()[0].compress_type == ZIP_STORED
        assert old.namelist() == new.namelist()
        for name in old.namelist():
            if name != "content.xml":
                assert old.read(name) == new.read(name)
    assert _get(result, "C2")["cached_value"] is None
    assert _get(result, "A1", "Other")["cached_value"] is None
    assert ods.summary(ods.parse(result))["requires_recalculation"] is True


def test_million_repeated_rows_and_columns_stay_compact():
    rows = ('<table:table-row table:number-rows-repeated="1048576">'
            '<table:table-cell table:number-columns-repeated="16384" office:value-type="float" office:value="0"/>'
            '</table:table-row>')
    doc = ods.parse(_fixture(rows, columns=16384, other_sheet=False))
    ods.edit(doc, {"op": "set-cell", "sheet": "Costs", "cell": "XFD1048575", "value": 9})
    result = ods.serialize(doc)
    assert _get(result, "XFD1048575")["value"] == 9
    assert _get(result, "XFC1048575")["value"] == 0
    assert _get(result, "XFD1048576")["value"] == 0
    assert len(ods.projection(doc)["sheets"]["Costs"]["rows"]) == 3
    assert len(result) < 5000


@pytest.mark.parametrize("value", [None, "", "中文  内容\t下一列\n第二行", True, False, -3.5, 0, "of:=NOT_A_FORMULA"])
def test_scalar_values_and_literal_formula_text(value):
    doc = ods.parse(_fixture())
    ods.edit(doc, {"op": "set-cell", "sheet": "Costs", "cell": "B2", "value": value})
    result = ods.serialize(doc)
    assert _get(result, "B2")["value"] == value
    assert "formula" not in _get(result, "B2")
    assert _get(result, "B2")["style"] == "ce1"


def test_openformula_prefix_namespace_is_bound_even_when_input_had_none():
    data = _fixture('<table:table-row><table:table-cell/></table:table-row>', other_sheet=False)
    doc = ods.parse(data)
    # Remove unused source namespace through a fresh XML tree to exercise formula-cell binding.
    raw = doc.package.read("content.xml").replace(f' xmlns:of="{ods.FORMULA}"'.encode(), b"")
    doc.package.write("content.xml", raw)
    doc = ods.parse(doc.package.serialize())
    ods.edit(doc, {"op": "set-formula", "sheet": "Costs", "cell": "A1", "formula": "of:=SUM([.B1:.C1])"})
    result = ods.serialize(doc)
    cell = _root(result).find(".//" + ods.T + "table-cell")
    assert cell.nsmap["of"] == ods.FORMULA
    assert cell.get(ods.T + "formula") == "of:=SUM([.B1:.C1])"
    assert _get(result, "A1")["requires_recalculation"] is True


def test_merged_anchor_can_change_but_covered_cells_cannot():
    rows = ('<table:table-row><table:table-cell table:number-columns-spanned="2" table:number-rows-spanned="2" '
            'office:value-type="string"><text:p>Merged</text:p></table:table-cell><table:covered-table-cell/>'
            '</table:table-row><table:table-row><table:covered-table-cell table:number-columns-repeated="2"/>'
            '</table:table-row>')
    doc = ods.parse(_fixture(rows, other_sheet=False))
    for cell in ["B1", "A2", "B2"]:
        with pytest.raises(FileEditError):
            ods.edit(doc, {"op": "set-cell", "sheet": "Costs", "cell": cell, "value": "bad"})
    ods.edit(doc, {"op": "set-cell", "sheet": "Costs", "cell": "A1", "value": "Changed"})
    assert _get(ods.serialize(doc), "A1")["value"] == "Changed"


def test_matrix_targets_rejected_and_output_caches_cleared_after_input_change():
    rows = ('<table:table-row><table:table-cell table:number-matrix-columns-spanned="2" '
            'table:number-matrix-rows-spanned="2" table:formula="of:=[.C1]*{1;2|3;4}" '
            'office:value-type="float" office:value="2"><text:p>2</text:p></table:table-cell>'
            '<table:table-cell office:value-type="float" office:value="4"/>'
            '<table:table-cell office:value-type="float" office:value="2"/></table:table-row>'
            '<table:table-row><table:table-cell table:number-columns-repeated="2" '
            'office:value-type="float" office:value="6"/></table:table-row>')
    doc = ods.parse(_fixture(rows, other_sheet=False))
    for cell in ["A1", "B2"]:
        with pytest.raises(FileEditError, match="Matrix"):
            ods.edit(doc, {"op": "set-cell", "sheet": "Costs", "cell": cell, "value": 8})
    ods.edit(doc, {"op": "set-cell", "sheet": "Costs", "cell": "C1", "value": 3})
    result = ods.serialize(doc)
    assert _get(result, "A1")["cached_value"] is None
    assert _get(result, "B1")["value"] is None and _get(result, "B2")["value"] is None


def test_repeat_with_formulas_cannot_be_split_and_other_edits_keep_reference_text():
    rows = ('<table:table-row><table:table-cell office:value-type="float" office:value="2"/></table:table-row>'
            '<table:table-row table:number-rows-repeated="2"><table:table-cell table:formula="of:=[.A1]*2" '
            'office:value-type="float" office:value="4"/></table:table-row>')
    doc = ods.parse(_fixture(rows, other_sheet=False))
    with pytest.raises(FileEditError, match="rebasing"):
        ods.edit(doc, {"op": "set-cell", "sheet": "Costs", "cell": "A2", "value": 8})
    ods.edit(doc, {"op": "set-cell", "sheet": "Costs", "cell": "A1", "value": 3})
    result = ods.serialize(doc)
    assert _get(result, "A2")["formula"] == "of:=[.A1]*2"
    assert _get(result, "A3")["cached_value"] is None


def test_declared_columns_allow_blank_tail_cell_but_no_new_rows_or_columns():
    doc = ods.parse(_fixture('<table:table-row><table:table-cell/></table:table-row>', columns=5, other_sheet=False))
    ods.edit(doc, {"op": "set-cell", "sheet": "Costs", "cell": "E1", "value": "new"})
    assert _get(ods.serialize(doc), "E1")["value"] == "new"
    for cell in ["F1", "A2"]:
        with pytest.raises(FileEditError) as error:
            ods.edit(doc, {"op": "set-cell", "sheet": "Costs", "cell": cell, "value": 9})
        assert error.value.code == "TARGET_NOT_FOUND"


@pytest.mark.parametrize("formula", ["=SUM(B2:B4)", "of:=", 4, "of:=A\0"])
def test_invalid_formula_rejected(formula):
    data = _fixture()
    doc = ods.parse(data)
    with pytest.raises(FileEditError):
        ods.edit(doc, {"op": "set-formula", "sheet": "Costs", "cell": "C5", "formula": formula})
    assert ods.serialize(doc) == data


@pytest.mark.parametrize("value", [[], {}, float("inf"), "a\0b"])
def test_invalid_scalar_rejected(value):
    with pytest.raises(FileEditError):
        ods.edit(ods.parse(_fixture()), {"op": "set-cell", "sheet": "Costs", "cell": "B2", "value": value})


def test_stored_dates_times_and_special_text_whitespace_are_readable():
    rows = ('<table:table-row><table:table-cell office:value-type="date" office:date-value="2026-09-26"/>'
            '<table:table-cell office:value-type="time" office:time-value="PT1H30M"/>'
            '<table:table-cell office:value-type="string"><text:p>a<text:s text:c="3"/>b<text:tab/>'
            'c<text:line-break/>d</text:p></table:table-cell></table:table-row>')
    data = _fixture(rows, other_sheet=False)
    assert _get(data, "A1")["value"] == "2026-09-26"
    assert _get(data, "B1")["value"] == "PT1H30M"
    assert _get(data, "C1")["value"] == "a   b\tc\nd"


def test_same_text_replacement_exposes_removed_run_formatting_in_projection():
    import difflib

    rows = ('<table:table-row><table:table-cell office:value-type="string">'
            '<text:p><text:span text:style-name="Bold">Same</text:span> text</text:p>'
            '</table:table-cell></table:table-row>')
    doc = ods.parse(_fixture(rows, other_sheet=False))
    before = json.dumps(ods.projection(doc), indent=2, ensure_ascii=False)
    ods.edit(doc, {"op": "set-cell", "sheet": "Costs", "cell": "A1", "value": "Same text"})
    after = json.dumps(ods.projection(doc), indent=2, ensure_ascii=False)
    assert _get(ods.serialize(doc), "A1")["value"] == "Same text"
    diff = "\n".join(difflib.unified_diff(before.splitlines(), after.splitlines()))
    assert '"text:style-name": "Bold"' in diff
    assert '"tag": "text:span"' in diff


def test_example_plan_hash_and_results():
    directory = Path(__file__).resolve().parents[1] / "examples" / "sfe"
    data = (directory / "costs.ods").read_bytes()
    plan = json.loads((directory / "costs.plan.json").read_text(encoding="utf-8"))
    assert plan["base"]["sha256"] == hashlib.sha256(data).hexdigest()
    doc = ods.parse(data)
    for step in plan["steps"]:
        ods.edit(doc, step)
    result = ods.serialize(doc)
    assert _get(result, "B3")["value"] == 75
    assert _get(result, "B4")["value"] == 50
    assert _get(result, "A4")["value"] == "现场调试"
    assert _get(result, "C5")["formula"] == "of:=SUM([.B2:.B4])"
