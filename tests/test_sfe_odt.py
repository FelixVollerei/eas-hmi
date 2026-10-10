"""ODT text/table changes preserve formatting references and unrelated ZIP parts."""

import io
import json
from copy import deepcopy
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile

import pytest
from lxml import etree
from typer.testing import CliRunner

from sfe_files import odt_profile as odt
from sfe_files.cli import app
from sfe_files.core import FileEditError, sha256
from sfe_files.odf_text import NS, paragraph_text, q

FIXTURE = Path(__file__).parents[1] / "examples" / "sfe" / "notes.odt"


def mutate(callback):
    output = io.BytesIO()
    with ZipFile(FIXTURE) as source, ZipFile(output, "w") as target:
        root = etree.fromstring(source.read("content.xml"))
        callback(root.find("office:body/office:text", NS))
        for info in source.infolist():
            target.writestr(info, etree.tostring(root) if info.filename == "content.xml" else source.read(info.filename))
    return output.getvalue()


def apply(source, *steps):
    document = odt.parse(source)
    for step in steps:
        document = odt.edit(document, step)
    return odt.serialize(document)


def test_fixture_inspection_and_noop_identity():
    source = FIXTURE.read_bytes()
    document = odt.parse(source)
    assert odt.serialize(document) == source
    assert odt.summary(document)["paragraph_count"] == 3
    assert odt.inspect(document, "/paragraphs/1")["value"]["text"] == "Commissioning notes"
    assert odt.inspect(document, "/tables/Actions")["value"]["rows"][1]["cells"][1]["text"] == "Pending"


def test_all_example_steps_and_untouched_payloads():
    source = FIXTURE.read_bytes()
    plan = json.loads(FIXTURE.with_suffix(".plan.json").read_bytes())
    assert plan["base"]["sha256"] == sha256(source)
    result = apply(source, *plan["steps"])
    document = odt.parse(result)
    assert odt.inspect(document, "/paragraphs/2")["value"]["text"] == "Approved for staging."
    assert odt.inspect(document, "/paragraphs/4")["value"]["text"] == "Verify alarms after deployment."
    assert odt.inspect(document, "/tables/Actions")["value"]["rows"][1]["cells"][1]["text"] == "Complete"
    with ZipFile(io.BytesIO(source)) as old, ZipFile(io.BytesIO(result)) as new:
        assert old.namelist() == new.namelist()
        assert new.infolist()[0].filename == "mimetype"
        assert new.infolist()[0].compress_type == ZIP_STORED
        assert [name for name in old.namelist() if old.read(name) != new.read(name)] == ["content.xml"]


def test_spaces_tabs_newlines_and_first_span_style_are_preserved():
    def change(body):
        paragraph = body.findall("text:p", NS)[0]
        paragraph.text = None
        etree.SubElement(paragraph, q("text:span"), {q("text:style-name"): "Emphasis"}).text = "Original"

    text = "  Leading  spaces\tand\nline end  "
    result = apply(mutate(change), {"op": "set-paragraph", "paragraph": 2, "text": text})
    document = odt.parse(result)
    view = odt.inspect(document, "/paragraphs/2")["value"]
    assert view["text"] == text
    assert view["style"] == "Body"
    assert view["content"][0]["attributes"]["text:style-name"] == "Emphasis"
    root = document.xml("content.xml")
    assert root.find(".//text:s[@text:c='2']", NS) is not None


def test_odf_whitespace_collapses_across_span_boundaries():
    paragraph = etree.fromstring(
        f'<text:p xmlns:text="{NS["text"]}"> A <text:span>  B </text:span>  C <text:s text:c="2"/> D </text:p>'
    )
    assert paragraph_text(paragraph) == "A B C    D"


@pytest.mark.parametrize("tag", ["text:a", "text:bookmark", "text:date", "text:change-start", "draw:frame"])
def test_complex_paragraph_is_readable_but_edits_rejected(tag):
    def change(body):
        etree.SubElement(body.findall("text:p", NS)[0], q(tag)).text = "Protected content"

    source = mutate(change)
    document = odt.parse(source)
    assert not odt.inspect(document, "/paragraphs/2")["value"]["editable"]
    with pytest.raises(FileEditError) as exc:
        odt.edit(document, {"op": "set-paragraph", "paragraph": 2, "text": "Replacement"})
    assert exc.value.code == "COMPLEX_TARGET"
    assert odt.serialize(document) == source


@pytest.mark.parametrize("attribute,value", [
    ("table:number-columns-repeated", "2"), ("table:number-columns-spanned", "2"),
    ("table:number-rows-spanned", "2"), ("table:formula", "of:=1+1"),
    ("office:value-type", "float"), ("table:protected", "true"),
])
def test_complex_cell_rejected(attribute, value):
    def change(body):
        body.find("table:table/table:table-row/table:table-cell", NS).set(q(attribute), value)

    source = mutate(change)
    document = odt.parse(source)
    with pytest.raises(FileEditError) as exc:
        odt.edit(document, {"op": "set-table-cell", "table": "Actions", "row": 1, "column": 1, "text": "x"})
    assert exc.value.code == "COMPLEX_TARGET"
    assert odt.serialize(document) == source


def test_duplicate_table_names_are_not_silently_overwritten():
    source = mutate(lambda body: body.append(deepcopy(body.find("table:table", NS))))
    document = odt.parse(source)
    assert odt.summary(document)["table_count"] == 2
    with pytest.raises(FileEditError) as exc:
        odt.inspect(document, "/tables/Actions")
    assert exc.value.code == "TARGET_NOT_UNIQUE"


def test_escaped_table_name_and_plain_cell_paragraph_merge():
    def change(body):
        table = body.find("table:table", NS)
        table.set(q("table:name"), "Actions/QA~1")
        cell = table.find("table:table-row/table:table-cell", NS)
        etree.SubElement(cell, q("text:p"), {q("text:style-name"): "Title"}).text = "Second"

    source = mutate(change)
    result = apply(source, {"op": "set-table-cell", "table": "Actions/QA~1", "row": 1, "column": 1, "text": "One"})
    cell = odt.inspect(odt.parse(result), "/tables/Actions~1QA~01")["value"]["rows"][0]["cells"][0]
    assert cell["text"] == "One"
    assert len(cell["paragraphs"]) == 1
    assert cell["attributes"]["table:style-name"] == "Cell"


@pytest.mark.parametrize("step", [
    {"op": "set-paragraph", "paragraph": 0, "text": "x"},
    {"op": "set-paragraph", "paragraph": True, "text": "x"},
    {"op": "append-paragraph", "text": "x\x00"},
    {"op": "append-paragraph", "text": "x", "extra": 1},
])
def test_invalid_steps_are_rejected(step):
    with pytest.raises(FileEditError) as exc:
        apply(FIXTURE.read_bytes(), step)
    assert exc.value.code == "INVALID_STEP"


def test_semantic_preview_exposes_style_collapse_with_identical_text(tmp_path):
    def change(body):
        paragraph = body.findall("text:p", NS)[0]
        paragraph.text = "Approval "
        etree.SubElement(paragraph, q("text:span"), {q("text:style-name"): "Emphasis"}).text = "is pending."

    source = mutate(change)
    file = tmp_path / "sample.odt"
    file.write_bytes(source)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"protocol": "sfe/0.1", "format": "odt", "base": {"sha256": sha256(source)},
                               "steps": [{"op": "set-paragraph", "paragraph": 2, "text": "Approval is pending."}]}))
    result = CliRunner().invoke(app, ["preview", str(file), str(plan), "--limit", "1000"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["diff_kind"] == "semantic"
    assert any(line.startswith("-") and "Emphasis" in line for line in payload["diff"].splitlines())
    assert payload["package_parts"]["modified"] == ["content.xml"]
