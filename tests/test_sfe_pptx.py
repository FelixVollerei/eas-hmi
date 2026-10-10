import io
import json
from copy import deepcopy
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from lxml import etree

from sfe_files import pptx_profile as pptx
from sfe_files.core import FileEditError, sha256

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples/sfe/project.pptx"
SLIDE = "ppt/slides/slide1.xml"


def parts(raw):
    with ZipFile(io.BytesIO(raw)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def packed(values):
    output = io.BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for name, data in values.items():
            archive.writestr(name, data)
    return output.getvalue()


def modified_slide(change):
    values = parts(SOURCE.read_bytes())
    root = etree.fromstring(values[SLIDE])
    shape = root.xpath(".//p:sp[p:nvSpPr/p:cNvPr[@id='2']]", namespaces=pptx.NS)[0]
    change(root, shape)
    values[SLIDE] = etree.tostring(root)
    return packed(values)


def test_pptx_sample_plan_edits_only_target_slide_and_retains_styles():
    raw = SOURCE.read_bytes()
    plan = json.loads(SOURCE.with_suffix(".plan.json").read_text(encoding="utf-8"))
    assert plan["base"]["sha256"] == sha256(raw)
    document = pptx.parse(raw)
    assert pptx.serialize(document) == raw
    original = document.package.xml(SLIDE)
    title_before = etree.tostring(original.xpath(".//p:sp[p:nvSpPr/p:cNvPr[@id='3']]", namespaces=pptx.NS)[0])
    paragraph_before = original.xpath(".//p:sp[p:nvSpPr/p:cNvPr[@id='2']]/p:txBody/a:p", namespaces=pptx.NS)[
        0
    ]
    paragraph_props = etree.tostring(paragraph_before.find("a:pPr", pptx.NS))
    run_props = etree.tostring(paragraph_before.find("a:r/a:rPr", pptx.NS))
    for step in plan["steps"]:
        pptx.edit(document, step)
    edited = pptx.serialize(document)
    after = pptx.parse(edited)
    body = pptx.inspect(after, "/slides/1/shapes/2")["value"]
    assert body["text"] == "Status: Ready for review"
    assert (body["x"], body["y"], body["width"], body["height"]) == (914400, 2286000, 8229600, 1371600)
    assert body["editable_text"] and body["editable_geometry"]
    after_root = after.package.xml(SLIDE)
    assert (
        etree.tostring(after_root.xpath(".//p:sp[p:nvSpPr/p:cNvPr[@id='3']]", namespaces=pptx.NS)[0])
        == title_before
    )
    paragraph_after = after_root.xpath(".//p:sp[p:nvSpPr/p:cNvPr[@id='2']]/p:txBody/a:p", namespaces=pptx.NS)[
        0
    ]
    assert etree.tostring(paragraph_after.find("a:pPr", pptx.NS)) == paragraph_props
    assert etree.tostring(paragraph_after.find("a:r/a:rPr", pptx.NS)) == run_props
    old_parts, new_parts = parts(raw), parts(edited)
    assert old_parts.keys() == new_parts.keys()
    assert [name for name in old_parts if old_parts[name] != new_parts[name]] == [SLIDE]
    assert any("slideLayouts" in name for name in old_parts)
    assert any("theme" in name for name in old_parts)
    assert SOURCE.read_bytes() == raw


def test_pptx_slide_list_relationship_order_and_shape_id_addressing():
    values = parts(SOURCE.read_bytes())
    presentation = etree.fromstring(values["ppt/presentation.xml"])
    order = presentation.find("p:sldIdLst", pptx.NS)
    first = order[0]
    second = etree.Element(f"{{{pptx.P}}}sldId", {"id": "300", f"{{{pptx.R}}}id": "rIdTest"})
    order.insert(0, second)
    rels = etree.fromstring(values["ppt/_rels/presentation.xml.rels"])
    etree.SubElement(
        rels,
        "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship",
        {"Id": "rIdTest", "Type": pptx.R + "/slide", "Target": "slides/slide99.xml"},
    )
    extra = etree.fromstring(values[SLIDE])
    extra.xpath(".//a:t", namespaces=pptx.NS)[0].text = "First in presentation order"
    values["ppt/slides/slide99.xml"] = etree.tostring(extra)
    values["ppt/presentation.xml"] = etree.tostring(presentation)
    values["ppt/_rels/presentation.xml.rels"] = etree.tostring(rels)
    document = pptx.parse(packed(values))
    assert document.slides == ["ppt/slides/slide99.xml", SLIDE]
    assert first is order[1]
    assert pptx.inspect(document, "/slides/1")["value"]["shapes"][0]["text"] == "First in presentation order"
    assert pptx.inspect(document, "/slides/2/shapes/2")["value"]["text"] == "Status: Draft"
    assert len(pptx.projection(document)["slides"]) == 2


def test_pptx_picture_geometry_preserves_media_payload():
    def add_picture(root, _shape):
        tree = root.find("p:cSld/p:spTree", pptx.NS)
        picture = etree.fromstring(f'''<p:pic xmlns:p="{pptx.P}" xmlns:a="{pptx.A}" xmlns:r="{pptx.R}">
          <p:nvPicPr><p:cNvPr id="20" name="Logo"/><p:cNvPicPr/><p:nvPr/></p:nvPicPr>
          <p:blipFill><a:blip r:embed="rIdImage"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>
          <p:spPr><a:xfrm rot="60000"><a:off x="10" y="20"/><a:ext cx="300" cy="400"/></a:xfrm></p:spPr>
        </p:pic>''')
        tree.append(picture)

    values = parts(modified_slide(add_picture))
    values["ppt/media/preserved.png"] = b"unchanged-media-payload"
    document = pptx.parse(packed(values))
    pptx.edit(document, {"op": "move", "slide": 1, "shape_id": 20, "x": -40, "y": 50})
    pptx.edit(document, {"op": "resize", "slide": 1, "shape_id": 20, "width": 600, "height": 800})
    shape = pptx.inspect(document, "/slides/1/shapes/20")["value"]
    assert shape["type"] == "pic" and shape["x"] == -40 and shape["width"] == 600
    after = parts(pptx.serialize(document))
    assert after["ppt/media/preserved.png"] == values["ppt/media/preserved.png"]
    assert etree.fromstring(after[SLIDE]).xpath(".//p:pic/p:spPr/a:xfrm/@rot", namespaces=pptx.NS) == [
        "60000"
    ]


def test_pptx_grouped_shapes_are_inspectable_but_not_editable():
    def group_shape(root, shape):
        tree = root.find("p:cSld/p:spTree", pptx.NS)
        group = etree.SubElement(tree, f"{{{pptx.P}}}grpSp")
        nonvisual = etree.SubElement(group, f"{{{pptx.P}}}nvGrpSpPr")
        etree.SubElement(nonvisual, f"{{{pptx.P}}}cNvPr", id="20", name="Group")
        etree.SubElement(group, f"{{{pptx.P}}}grpSpPr")
        group.append(shape)

    raw = modified_slide(group_shape)
    document = pptx.parse(raw)
    view = pptx.inspect(document, "/slides/1/shapes/2")["value"]
    assert view["grouped"] and not view["editable_geometry"] and not view["editable_text"]
    with pytest.raises(FileEditError, match="standalone"):
        pptx.edit(document, {"op": "move", "slide": 1, "shape_id": 2, "x": 0, "y": 0})
    assert pptx.serialize(document) == raw


@pytest.mark.parametrize("kind", ["field", "hyperlink", "mixed-style", "multiple-paragraphs"])
def test_pptx_rejects_complex_text_without_mutation(kind):
    def complex_text(_root, shape):
        body = shape.find("p:txBody", pptx.NS)
        paragraph = body.find("a:p", pptx.NS)
        if kind == "field":
            etree.SubElement(paragraph, f"{{{pptx.A}}}fld", id="field-id")
        elif kind == "hyperlink":
            etree.SubElement(paragraph.find("a:r/a:rPr", pptx.NS), f"{{{pptx.A}}}hlinkClick")
        elif kind == "mixed-style":
            run = deepcopy(paragraph.find("a:r", pptx.NS))
            run.find("a:rPr", pptx.NS).set("b", "1")
            paragraph.append(run)
        else:
            body.append(deepcopy(paragraph))

    raw = modified_slide(complex_text)
    document = pptx.parse(raw)
    assert not pptx.inspect(document, "/slides/1/shapes/2")["value"]["editable_text"]
    with pytest.raises(FileEditError):
        pptx.edit(document, {"op": "set-text", "slide": 1, "shape_id": 2, "text": "Replacement"})
    assert pptx.serialize(document) == raw


@pytest.mark.parametrize(
    "step",
    [
        {"op": "move", "slide": 1, "shape_id": 2, "x": True, "y": 1},
        {"op": "move", "slide": 1, "shape_id": 2, "x": 1.2, "y": 1},
        {"op": "move", "slide": 0, "shape_id": 2, "x": 1, "y": 1},
        {"op": "move", "slide": 1, "shape_id": True, "x": 1, "y": 1},
        {"op": "resize", "slide": 1, "shape_id": 2, "width": 0, "height": 10},
        {"op": "resize", "slide": 1, "shape_id": 2, "width": 10, "height": 10, "extra": 1},
        {"op": "set-text", "slide": 1, "shape_id": 2, "text": "First\nSecond"},
        {"op": "set-text", "slide": 1, "shape_id": 2, "text": "bad\x00text"},
    ],
)
def test_pptx_invalid_steps_leave_original_bytes(step):
    raw = SOURCE.read_bytes()
    document = pptx.parse(raw)
    with pytest.raises(FileEditError):
        pptx.edit(document, step)
    assert pptx.serialize(document) == raw


def test_pptx_duplicate_shape_ids_rejected():
    raw = modified_slide(lambda root, shape: shape.find("p:nvSpPr/p:cNvPr", pptx.NS).set("id", "3"))
    with pytest.raises(FileEditError, match="unique"):
        pptx.parse(raw)
