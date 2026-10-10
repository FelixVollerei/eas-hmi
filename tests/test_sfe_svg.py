import json
from pathlib import Path

import pytest
from lxml import etree

from sfe_files import svg_profile as svg
from sfe_files.core import FileEditError, sha256

ROOT = Path(__file__).resolve().parents[1]


def test_svg_published_plan_targets_and_preserves_other_nodes():
    source = ROOT / "examples/sfe/schematic.svg"
    raw = source.read_bytes()
    document = svg.parse(raw)
    plan = json.loads((source.parent / "schematic.plan.json").read_text(encoding="utf-8"))
    assert plan["base"]["sha256"] == sha256(raw)
    tank_before = etree.tostring(document.xpath("//*[@id='tank']")[0])
    pipe_before = etree.tostring(document.xpath("//*[@id='pipe']")[0])
    for step in plan["steps"]:
        assert svg.edit(document, step) is document
    after = svg.parse(svg.serialize(document))
    assert (
        svg.inspect(after, "pump")["target"]["attributes"]["transform"] == "translate(24 12) translate(48 64)"
    )
    assert svg.inspect(after, "pump-body")["target"]["attributes"]["fill"] == "#16a34a"
    assert svg.inspect(after, "pump-label")["target"]["text"] == "Pump ready"
    assert etree.tostring(after.xpath("//*[@id='tank']")[0]) == tank_before
    assert etree.tostring(after.xpath("//*[@id='pipe']")[0]) == pipe_before
    assert source.read_bytes() == raw


def test_svg_inspect_summary_and_remove_attribute():
    document = svg.parse(
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 30"><rect id="r" fill="red"/></svg>'
    )
    assert svg.summary(document)["viewBox"] == "0 0 40 30"
    assert svg.inspect(document)["summary"]["elements"] == [{"id": "r", "tag": "rect"}]
    svg.edit(document, {"op": "remove-attribute", "id": "r", "name": "fill"})
    assert svg.inspect(document, "r")["target"]["attributes"] == {"id": "r"}
    with pytest.raises(FileEditError, match="attribute not found"):
        svg.edit(document, {"op": "remove-attribute", "id": "r", "name": "fill"})
    with pytest.raises(FileEditError, match="id not found"):
        svg.inspect(document, "missing")


def test_svg_duplicate_ids_and_mixed_text_are_rejected():
    with pytest.raises(FileEditError, match="not unique"):
        svg.parse(b'<svg><rect id="r"/><text id="r">Duplicate</text></svg>')
    document = svg.parse(b'<svg><text id="t">Before <tspan id="s">mixed</tspan> tail</text></svg>')
    original = svg.serialize(document)
    with pytest.raises(FileEditError, match="requires a leaf"):
        svg.edit(document, {"op": "set-text", "id": "t", "text": "erased"})
    assert svg.serialize(document) == original
    svg.edit(document, {"op": "set-text", "id": "s", "text": "safe & <literal>"})
    after = svg.parse(svg.serialize(document))
    assert svg.inspect(after, "s")["target"]["text"] == "safe & <literal>"
    assert after.xpath("//*[@id='s']")[0].tail == " tail"


def test_svg_translation_is_prepended_in_parent_coordinates():
    document = svg.parse(b'<svg><g id="group" transform="scale(2) rotate(90)"><rect id="r"/></g></svg>')
    svg.edit(document, {"op": "translate", "id": "group", "dx": 10, "dy": -3.5})
    transform = svg.inspect(document, "group")["target"]["attributes"]["transform"]
    assert transform == "translate(10 -3.5) scale(2) rotate(90)"
    assert "transform" not in svg.inspect(document, "r")["target"]["attributes"]
    svg.edit(document, {"op": "translate", "id": "r", "dx": 0, "dy": 1})
    assert svg.inspect(document, "r")["target"]["attributes"]["transform"] == "translate(0 1)"


@pytest.mark.parametrize("value", [True, "1", float("inf"), float("nan")])
def test_svg_rejects_invalid_translation_numbers(value):
    document = svg.parse(b'<svg><g id="g"/></svg>')
    before = svg.serialize(document)
    with pytest.raises(FileEditError, match="finite numbers"):
        svg.edit(document, {"op": "translate", "id": "g", "dx": 1, "dy": value})
    assert svg.serialize(document) == before


@pytest.mark.parametrize(
    "step",
    [
        {"op": "set-attribute", "id": "r", "name": "id", "value": "new"},
        {"op": "remove-attribute", "id": "r", "name": "id"},
        {"op": "set-attribute", "id": "r", "name": "fill", "value": 12},
        {"op": "set-attribute", "id": "r", "name": "fill", "value": "red", "extra": True},
        {"op": "set-text", "id": "r", "text": "not a text node"},
    ],
)
def test_svg_rejects_invalid_steps_without_changes(step):
    document = svg.parse(b'<svg><rect id="r"/></svg>')
    before = svg.serialize(document)
    with pytest.raises(FileEditError):
        svg.edit(document, step)
    assert svg.serialize(document) == before


@pytest.mark.parametrize(
    "raw",
    [
        b"<!DOCTYPE svg><svg/>",
        b'<!DOCTYPE svg [<!ENTITY x "expanded">]><svg><text>&x;</text></svg>',
        b'<!DOCTYPE svg SYSTEM "https://example.invalid/svg.dtd"><svg/>',
        b'<?xml version="1.0" encoding="ISO-8859-1"?><svg/>',
        "<svg/>".encode("utf-16"),
        b"<html/>",
    ],
)
def test_svg_rejects_dtd_entities_non_utf8_and_non_svg(raw):
    with pytest.raises(FileEditError):
        svg.parse(raw)


def test_svg_accepts_utf8_bom_and_bounds_summary():
    raw = (
        b'\xef\xbb\xbf<svg width="80" height="60">'
        + b"".join(f'<rect id="r{i}"/>'.encode() for i in range(205))
        + b"</svg>"
    )
    document = svg.parse(raw)
    summary = svg.summary(document)
    assert summary["width"] == "80" and summary["height"] == "60"
    assert summary["id_count"] == 205 and len(summary["elements"]) == 200
    assert summary["truncated"]
    assert svg.parse(svg.serialize(document)).getroot().tag == "svg"
