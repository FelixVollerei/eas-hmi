"""Word text edits preserve unrelated OPC payloads and reject lossy targets."""

import io
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from lxml import etree
from typer.testing import CliRunner

from sfe_files import docx_profile as docx
from sfe_files.cli import app
from sfe_files.core import FileEditError, sha256

FIXTURE = Path(__file__).parents[1] / "examples" / "sfe" / "meeting.docx"
W = docx.W
NS = {"w": W}
PART = "word/document.xml"


def pack(source, updates):
    output = io.BytesIO()
    with ZipFile(io.BytesIO(source)) as old, ZipFile(output, "w") as new:
        for item in old.infolist():
            new.writestr(item, updates.get(item.filename, old.read(item.filename)))
        for name, data in updates.items():
            if name not in old.namelist():
                new.writestr(name, data)
    return output.getvalue()


def mutate_main(callback):
    source = FIXTURE.read_bytes()
    with ZipFile(io.BytesIO(source)) as archive:
        root = etree.fromstring(archive.read(PART))
    callback(root)
    return pack(source, {PART: etree.tostring(root)})


def body(root):
    return root.find("w:body", NS)


def apply(source, *steps):
    document = docx.parse(source)
    for step in steps:
        document = docx.edit(document, step)
    return docx.serialize(document)


def test_inspect_and_noop_preserve_exact_bytes():
    source = FIXTURE.read_bytes()
    document = docx.parse(source)
    assert docx.serialize(document) == source
    assert docx.summary(document)["paragraph_count"] == 5
    assert docx.summary(document)["table_count"] == 1
    assert docx.inspect(document, "/paragraphs/1")["value"]["text"] == "Gateway commissioning review"
    assert docx.inspect(document, "/tables/1")["value"]["rows"][1]["cells"][2]["text"] == "Pending"
    json.dumps(docx.projection(document), allow_nan=False)


def test_example_plan_hash_and_all_commands():
    source = FIXTURE.read_bytes()
    plan = json.loads(FIXTURE.with_name("meeting.plan.json").read_bytes())
    assert plan["base"]["sha256"] == sha256(source)
    result = apply(source, *plan["steps"])
    document = docx.parse(result)
    view = docx.projection(document)
    assert view["paragraphs"]["2"]["text"].startswith("Approved for staging.")
    assert view["tables"]["1"]["rows"][1]["cells"][2]["text"] == "Complete"
    assert view["paragraphs"]["6"]["text"] == "Follow-up: verify alarms after the staging deployment."
    assert body(document.xml(PART))[-1].tag == f"{{{W}}}sectPr"
    with ZipFile(io.BytesIO(source)) as old, ZipFile(io.BytesIO(result)) as new:
        assert old.namelist() == new.namelist()
        assert [name for name in old.namelist() if old.read(name) != new.read(name)] == [PART]


def test_replacement_retains_paragraph_and_first_run_style():
    def modify(root):
        paragraph = body(root).findall("w:p", NS)[1]
        ppr = etree.SubElement(paragraph, f"{{{W}}}pPr")
        etree.SubElement(ppr, f"{{{W}}}keepNext")
        paragraph.insert(0, ppr)
        first_run = paragraph.find("w:r", NS)
        rpr = etree.Element(f"{{{W}}}rPr")
        etree.SubElement(rpr, f"{{{W}}}b")
        first_run.insert(0, rpr)
        second = etree.SubElement(paragraph, f"{{{W}}}r")
        second_rpr = etree.SubElement(second, f"{{{W}}}rPr")
        etree.SubElement(second_rpr, f"{{{W}}}i")
        etree.SubElement(second, f"{{{W}}}t").text = " Extra italic text."

    source = mutate_main(modify)
    result = apply(source, {"op": "set-paragraph", "paragraph": 2, "text": "New\ttext\nwith whitespace "})
    document = docx.parse(result)
    paragraph = body(document.xml(PART)).findall("w:p", NS)[1]
    assert paragraph.find("w:pPr/w:keepNext", NS) is not None
    assert paragraph.find("w:r/w:rPr/w:b", NS) is not None
    assert paragraph.find("w:r/w:rPr/w:i", NS) is None
    assert len(paragraph.findall("w:r", NS)) == 1
    assert docx.inspect(document, "/paragraphs/2")["value"]["text"] == "New\ttext\nwith whitespace "


@pytest.mark.parametrize(
    "fragment",
    [
        '<w:hyperlink><w:r><w:t>Link</w:t></w:r></w:hyperlink>',
        '<w:fldSimple w:instr="DATE"><w:r><w:t>Date</w:t></w:r></w:fldSimple>',
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r>',
        '<w:r><w:drawing/></w:r>',
        '<w:ins><w:r><w:t>Inserted</w:t></w:r></w:ins>',
        '<w:sdt><w:sdtContent><w:r><w:t>Controlled</w:t></w:r></w:sdtContent></w:sdt>',
        '<w:bookmarkStart w:id="0" w:name="target"/>',
        '<w:r><w:br w:type="page"/></w:r>',
    ],
)
def test_complex_paragraph_is_readable_but_edit_rejected(fragment):
    def modify(root):
        body(root).findall("w:p", NS)[1].append(etree.fromstring(f'<wrapper xmlns:w="{W}">{fragment}</wrapper>')[0])

    source = mutate_main(modify)
    document = docx.parse(source)
    assert docx.inspect(document, "/paragraphs/2")["value"]["editable"] is False
    with pytest.raises(FileEditError, match="contains") as exc:
        docx.edit(document, {"op": "set-paragraph", "paragraph": 2, "text": "Replacement"})
    assert exc.value.code == "COMPLEX_TARGET"
    assert docx.serialize(document) == source


def test_unmodified_media_and_complex_paragraph_are_preserved():
    def modify(root):
        paragraph = body(root).findall("w:p", NS)[4]
        etree.SubElement(paragraph.find("w:r", NS), f"{{{W}}}drawing")

    source = pack(mutate_main(modify), {"word/media/preserved.bin": b"Opaque image bytes\x00\xff"})
    result = apply(source, {"op": "set-paragraph", "paragraph": 2, "text": "Approved"})
    with ZipFile(io.BytesIO(source)) as old, ZipFile(io.BytesIO(result)) as new:
        for name in old.namelist():
            if name != PART:
                assert new.read(name) == old.read(name)
        original_xml = etree.fromstring(old.read(PART))
        result_xml = etree.fromstring(new.read(PART))
        assert etree.tostring(body(original_xml).findall("w:p", NS)[4]) == etree.tostring(
            body(result_xml).findall("w:p", NS)[4]
        )


def test_plain_multiline_cell_replacement_retains_properties():
    def modify(root):
        cell = body(root).find("w:tbl/w:tr/w:tc", NS)
        paragraph = etree.SubElement(cell, f"{{{W}}}p")
        etree.SubElement(etree.SubElement(paragraph, f"{{{W}}}r"), f"{{{W}}}t").text = "Second paragraph"

    source = mutate_main(modify)
    before = docx.parse(source).xml(PART).find("w:body/w:tbl/w:tr/w:tc/w:tcPr", NS)
    result = apply(source, {"op": "set-table-cell", "table": 1, "row": 1, "column": 1, "text": "Task\nitem"})
    document = docx.parse(result)
    cell = document.xml(PART).find("w:body/w:tbl/w:tr/w:tc", NS)
    assert etree.tostring(cell.find("w:tcPr", NS)) == etree.tostring(before)
    assert len(cell.findall("w:p", NS)) == 1
    assert docx.inspect(document, "/tables/1")["value"]["rows"][0]["cells"][0]["text"] == "Task\nitem"


@pytest.mark.parametrize("element", ["gridSpan", "vMerge", "hMerge"])
def test_merged_table_rejected(element):
    def modify(root):
        tcpr = body(root).find("w:tbl/w:tr/w:tc/w:tcPr", NS)
        etree.SubElement(tcpr, f"{{{W}}}{element}").set(f"{{{W}}}val", "2")

    document = docx.parse(mutate_main(modify))
    with pytest.raises(FileEditError) as exc:
        docx.edit(document, {"op": "set-table-cell", "table": 1, "row": 2, "column": 2, "text": "x"})
    assert exc.value.code == "COMPLEX_TARGET"


@pytest.mark.parametrize(
    "step,code",
    [
        ({"op": "set-paragraph", "paragraph": 0, "text": "x"}, "INVALID_STEP"),
        ({"op": "set-paragraph", "paragraph": True, "text": "x"}, "INVALID_STEP"),
        ({"op": "set-paragraph", "paragraph": 99, "text": "x"}, "TARGET_NOT_FOUND"),
        ({"op": "append-paragraph", "text": 123}, "INVALID_STEP"),
        ({"op": "append-paragraph", "text": "bad\x00text"}, "INVALID_STEP"),
        ({"op": "append-paragraph", "text": "x", "extra": 1}, "INVALID_STEP"),
        ({"op": "set-table-cell", "table": 1, "row": 9, "column": 1, "text": "x"}, "TARGET_NOT_FOUND"),
    ],
)
def test_step_validation(step, code):
    source = FIXTURE.read_bytes()
    document = docx.parse(source)
    with pytest.raises(FileEditError) as exc:
        docx.edit(document, step)
    assert exc.value.code == code
    assert docx.serialize(document) == source


def test_complex_cell_rejected_before_modification():
    def modify(root):
        cell = body(root).find("w:tbl/w:tr/w:tc", NS)
        etree.SubElement(cell, f"{{{W}}}tbl")

    source = mutate_main(modify)
    document = docx.parse(source)
    with pytest.raises(FileEditError) as exc:
        docx.edit(document, {"op": "set-table-cell", "table": 1, "row": 1, "column": 1, "text": "x"})
    assert exc.value.code == "COMPLEX_TARGET"
    assert docx.serialize(document) == source


def preview_plan(tmp_path, source, step):
    file = tmp_path / "source.docx"
    file.write_bytes(source)
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {"protocol": "sfe/0.1", "format": "docx", "base": {"sha256": sha256(source)}, "steps": [step]}
        ),
        encoding="utf-8",
    )
    result = CliRunner().invoke(app, ["preview", str(file), str(plan), "--limit", "1000"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_preview_reports_mixed_run_format_loss_even_when_text_is_unchanged(tmp_path):
    def modify(root):
        paragraph = body(root).findall("w:p", NS)[1]
        first_text = paragraph.find("w:r/w:t", NS)
        original = first_text.text
        first_text.text = original[:20]
        run = etree.SubElement(paragraph, f"{{{W}}}r")
        rpr = etree.SubElement(run, f"{{{W}}}rPr")
        etree.SubElement(rpr, f"{{{W}}}i")
        etree.SubElement(run, f"{{{W}}}t").text = original[20:]

    source = mutate_main(modify)
    before = docx.inspect(docx.parse(source), "/paragraphs/2")["value"]
    assert before["runs"][1]["properties"] == [{"name": "i"}]
    step = {"op": "set-paragraph", "paragraph": 2, "text": before["text"]}
    result = preview_plan(tmp_path, source, step)
    after = docx.inspect(docx.parse(apply(source, step)), "/paragraphs/2")["value"]
    assert before["text"] == after["text"]
    assert after["runs"] == [{"text": before["text"], "properties": []}]
    assert result["diff_kind"] == "semantic"
    assert any(line.startswith("-") and '"name": "i"' in line for line in result["diff"].splitlines())
    assert result["package_parts"]["modified"] == [PART]
    assert "xmlns" not in result["diff"]
    assert result["committed"] is False


def test_preview_reports_cell_paragraph_merge_when_visible_text_is_unchanged(tmp_path):
    def modify(root):
        cell = body(root).find("w:tbl/w:tr/w:tc", NS)
        paragraph = etree.SubElement(cell, f"{{{W}}}p")
        ppr = etree.SubElement(paragraph, f"{{{W}}}pPr")
        etree.SubElement(ppr, f"{{{W}}}jc").set(f"{{{W}}}val", "center")
        etree.SubElement(etree.SubElement(paragraph, f"{{{W}}}r"), f"{{{W}}}t").text = "Details"

    source = mutate_main(modify)
    before = docx.inspect(docx.parse(source), "/tables/1")["value"]["rows"][0]["cells"][0]
    assert before["paragraphs"][1]["paragraph_properties"] == [
        {"name": "jc", "attributes": {"val": "center"}}
    ]
    step = {"op": "set-table-cell", "table": 1, "row": 1, "column": 1, "text": before["text"]}
    result = preview_plan(tmp_path, source, step)
    after = docx.inspect(docx.parse(apply(source, step)), "/tables/1")["value"]["rows"][0]["cells"][0]
    assert before["text"] == after["text"]
    assert len(before["paragraphs"]) == 2
    assert len(after["paragraphs"]) == 1
    assert any(line.startswith("-") and '"name": "jc"' in line for line in result["diff"].splitlines())
    assert "xmlns" not in result["diff"]
