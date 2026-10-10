"""ODP object commands edit text/geometry while preserving the remaining package."""

import io
import json
from copy import deepcopy
from pathlib import Path
from zipfile import ZipFile

import pytest
from lxml import etree

from sfe_files import odp_profile as odp
from sfe_files.core import FileEditError, sha256
from sfe_files.odf_text import NS, q

FIXTURE = Path(__file__).parents[1] / "examples" / "sfe" / "briefing.odp"


def mutate(callback):
    output = io.BytesIO()
    with ZipFile(FIXTURE) as source, ZipFile(output, "w") as target:
        root = etree.fromstring(source.read("content.xml"))
        callback(root.find("office:body/office:presentation/draw:page", NS))
        for info in source.infolist():
            target.writestr(info, etree.tostring(root) if info.filename == "content.xml" else source.read(info.filename))
    return output.getvalue()


def status(page):
    return page.find("draw:frame[@draw:name='status']", NS)


def apply(source, *steps):
    document = odp.parse(source)
    for step in steps:
        document = odp.edit(document, step)
    return odp.serialize(document)


def test_fixture_inspection_excludes_notes_and_noop_is_exact():
    source = FIXTURE.read_bytes()
    document = odp.parse(source)
    assert odp.serialize(document) == source
    summary = odp.summary(document)
    assert summary["slide_count"] == 1
    assert summary["slides"][0]["objects"] == ["title", "status", "indicator", "logo"]
    assert odp.inspect(document, "/slides/1/objects/status")["value"]["text"] == "Approval pending"
    assert odp.inspect(document, "/slides/1/objects/logo")["value"]["editable_text"] is False


def test_example_plan_text_move_resize_and_preserved_payloads():
    source = FIXTURE.read_bytes()
    plan = json.loads(FIXTURE.with_suffix(".plan.json").read_bytes())
    assert plan["base"]["sha256"] == sha256(source)
    result = apply(source, *plan["steps"])
    document = odp.parse(result)
    view = odp.inspect(document, "/slides/1/objects/status")["value"]
    assert view["text"] == "Approved for staging"
    assert view["geometry_cm"] == {"x": 2.5, "y": 5.0, "width": 21.0, "height": 2.5}
    with ZipFile(io.BytesIO(source)) as old, ZipFile(io.BytesIO(result)) as new:
        assert [name for name in old.namelist() if old.read(name) != new.read(name)] == ["content.xml"]
        before = etree.fromstring(old.read("content.xml"))
        after = etree.fromstring(new.read("content.xml"))
        for path in [".//presentation:notes", ".//draw:frame[@draw:name='logo']"]:
            assert etree.tostring(before.find(path, NS)) == etree.tostring(after.find(path, NS))
    assert view["paragraphs"][0]["style"] == "Body"


def test_escaped_names_and_measure_conversion():
    def change(page):
        node = status(page)
        node.set(q("draw:name"), "status/QA~1")
        node.set(q("svg:x"), "25mm")
        node.set(q("svg:y"), "1in")
        node.set(q("svg:width"), "72pt")
        node.set(q("svg:height"), "6pc")

    document = odp.parse(mutate(change))
    view = odp.inspect(document, "/slides/1/objects/status~1QA~01")["value"]
    assert view["geometry_cm"] == {"x": 2.5, "y": 2.54, "width": 2.54, "height": 2.54}


def test_duplicate_names_require_disambiguation():
    document = odp.parse(mutate(lambda page: page.append(deepcopy(status(page)))))
    with pytest.raises(FileEditError) as exc:
        odp.edit(document, {"op": "set-text", "slide": 1, "object": "status", "text": "x"})
    assert exc.value.code == "TARGET_NOT_UNIQUE"


@pytest.mark.parametrize("tag", ["text:a", "text:bookmark", "text:date", "text:change-start"])
def test_complex_text_is_rejected_without_change(tag):
    def change(page):
        paragraph = status(page).find("draw:text-box/text:p", NS)
        etree.SubElement(paragraph, q(tag)).text = "Protected"

    source = mutate(change)
    document = odp.parse(source)
    assert not odp.inspect(document, "/slides/1/objects/status")["value"]["editable_text"]
    with pytest.raises(FileEditError) as exc:
        odp.edit(document, {"op": "set-text", "slide": 1, "object": "status", "text": "x"})
    assert exc.value.code == "COMPLEX_TARGET"
    assert odp.serialize(document) == source


def test_multiple_paragraphs_are_rejected():
    def change(page):
        etree.SubElement(status(page).find("draw:text-box", NS), q("text:p")).text = "Second paragraph"

    with pytest.raises(FileEditError) as exc:
        apply(mutate(change), {"op": "set-text", "slide": 1, "object": "status", "text": "x"})
    assert exc.value.code == "COMPLEX_TARGET"


@pytest.mark.parametrize("attribute,value", [
    ("draw:transform", "rotate(0.5)"), ("style:rel-width", "50%"),
    ("svg:width", "50%"), ("svg:height", "0cm"),
])
def test_unsupported_geometry_is_rejected(attribute, value):
    source = mutate(lambda page: status(page).set(q(attribute), value))
    document = odp.parse(source)
    with pytest.raises(FileEditError) as exc:
        odp.edit(document, {"op": "move", "slide": 1, "object": "status", "x": 1, "y": 2})
    assert exc.value.code == "COMPLEX_TARGET"
    assert odp.serialize(document) == source


def test_group_geometry_is_rejected_but_named_text_can_be_edited():
    def change(page):
        node = status(page)
        page.remove(node)
        group = etree.SubElement(page, q("draw:g"), {q("draw:name"): "group"})
        group.append(node)

    source = mutate(change)
    with pytest.raises(FileEditError) as exc:
        apply(source, {"op": "resize", "slide": 1, "object": "status", "width": 5, "height": 2})
    assert exc.value.code == "COMPLEX_TARGET"
    result = apply(source, {"op": "set-text", "slide": 1, "object": "status", "text": "Updated"})
    assert odp.inspect(odp.parse(result), "/slides/1/objects/status")["value"]["text"] == "Updated"


@pytest.mark.parametrize("step", [
    {"op": "move", "slide": True, "object": "status", "x": 1, "y": 2},
    {"op": "move", "slide": 1, "object": "status", "x": float("nan"), "y": 2},
    {"op": "move", "slide": 1, "object": "status", "x": True, "y": 2},
    {"op": "resize", "slide": 1, "object": "status", "width": 2, "height": 0},
    {"op": "set-text", "slide": 1, "object": "status", "text": "x", "extra": 1},
])
def test_invalid_steps_fail_before_mutating(step):
    source = FIXTURE.read_bytes()
    document = odp.parse(source)
    with pytest.raises(FileEditError) as exc:
        odp.edit(document, step)
    assert exc.value.code == "INVALID_STEP"
    assert odp.serialize(document) == source


def test_reading_slide_number_uses_ascii_positive_indices():
    document = odp.parse(FIXTURE.read_bytes())
    for pointer in ["/slides/0", "/slides/01", "/slides/٠"]:
        with pytest.raises(FileEditError) as exc:
            odp.inspect(document, pointer)
        assert exc.value.code == "INVALID_POINTER"


def test_text_whitespace_and_mixed_style_change_are_visible_in_projection():
    def change(page):
        paragraph = status(page).find("draw:text-box/text:p", NS)
        paragraph.text = "Approval "
        etree.SubElement(paragraph, q("text:span"), {q("text:style-name"): "Emphasis"}).text = "pending"

    source = mutate(change)
    before = odp.projection(odp.parse(source))
    result = apply(source, {"op": "set-text", "slide": 1, "object": "status", "text": "Approval pending"})
    after = odp.projection(odp.parse(result))
    assert before != after
    assert before["slides"][0]["objects"][1]["text"] == after["slides"][0]["objects"][1]["text"]
