import subprocess
from unittest.mock import patch

import pytest
from lxml import etree

from eas_hmi.errors import EngineeringError
from eas_hmi.export.assets import InkscapeBackend, export_asset
from eas_hmi.geometry import parse_transform
from eas_hmi.model import canonical_hash
from eas_hmi.operations.transaction import Store
from eas_hmi.svg.common import SVG, safe_parse
from eas_hmi.svg.renderer import render
from eas_hmi.svg.synchronizer import frame_of, plan_sync


def target(root, id):
    return root.xpath("//*[@data-eas-id=$id]", id=id)[0]


@pytest.mark.parametrize("attribute,value", [("x", "7"), ("y", "11"), ("width", "140"), ("height", "45")])
def test_primitive_rect_geometry_projection(small_project, attribute, value):
    root = etree.fromstring(render(small_project, "probe"))
    frame_of(target(root, "BOX_A"))[0].set(attribute, value)
    candidate = plan_sync(small_project, etree.tostring(root)).candidate
    n = next(n for n in candidate.all_nodes() if n.id == "BOX_A")
    assert getattr(n, attribute) == float(value) + (30 if attribute in ("x", "y") else 0)


def test_text_position_projection(small_project):
    root = etree.fromstring(render(small_project, "probe"))
    text = frame_of(target(root, "LABEL"))[0]
    text.set("x", "12")
    text.set("y", "30")
    candidate = plan_sync(small_project, etree.tostring(root)).candidate
    n = next(n for n in candidate.all_nodes() if n.id == "LABEL")
    assert (n.x, n.y) == (42, 115)


@pytest.mark.parametrize(
    "mutation",
    [
        "rich-text",
        "multiline",
        "overlap-text",
        "missing-frame",
        "body-style",
        "locked",
        "equipment",
        "reordered",
        "new-child",
        "frame-transform",
        "duplicate-xml-id",
    ],
)
def test_unsupported_content_is_not_silently_lost(small_project, mutation):
    if mutation == "locked":
        small_project.pages[0].nodes[0].locked = True
    root = etree.fromstring(render(small_project, "probe"))
    box, label = target(root, "BOX_A"), target(root, "LABEL")
    text = frame_of(label)[0]
    if mutation == "rich-text":
        text.text = None
        etree.SubElement(text, f"{{{SVG}}}tspan", style="font-weight:bold").text = "new"
    elif mutation == "multiline":
        text.text = "two\nlines"
    elif mutation == "overlap-text":
        etree.SubElement(text, f"{{{SVG}}}tspan", x="0").text = "overlaps existing text"
    elif mutation == "missing-frame":
        box.remove(frame_of(box))
    elif mutation == "body-style":
        frame_of(box)[0].set("fill", "red")
    elif mutation == "locked":
        box.set("transform", "translate(10)")
    elif mutation == "equipment":
        box.set("data-eas-equipment", "UNKNOWN")
    elif mutation == "reordered":
        root.append(box)
    elif mutation == "new-child":
        etree.SubElement(frame_of(box), f"{{{SVG}}}circle", r="3")
    elif mutation == "frame-transform":
        frame_of(box).set("transform", "translate(3)")
    else:
        frame_of(label)[0].set("id", frame_of(box)[0].get("id"))
    before = canonical_hash(small_project)
    with pytest.raises(EngineeringError):
        plan_sync(small_project, etree.tostring(root))
    assert canonical_hash(small_project) == before


def test_failed_sync_does_not_write_storage(small_project, tmp_path):
    store = Store(tmp_path / "state")
    store.initialize(small_project)
    old_model = store.load()
    old_head = store.head.read_bytes()
    old_history = (store.root / "history/operations.jsonl").read_bytes()
    old_events = (store.root / "history/events.jsonl").read_bytes()
    root = etree.fromstring(render(old_model, "probe"))
    frame_of(target(root, "LABEL"))[0].text = "valid edit before rejected edit"
    target(root, "BOX_A").set("transform", "skewX(20)")
    with pytest.raises(EngineeringError):
        store.transaction(
            "rejected sync probe", lambda p: plan_sync(p, etree.tostring(root)).apply(p), actor="human"
        )
    assert store.head.read_bytes() == old_head
    assert (store.root / "history/operations.jsonl").read_bytes() == old_history
    assert (store.root / "history/events.jsonl").read_bytes() == old_events
    assert canonical_hash(store.load()) == canonical_hash(old_model)


@pytest.mark.parametrize(
    "body",
    [
        "<style>rect{fill:url(https://example.org/x)}</style>",
        '<style>@import "evil.css";</style>',
        '<rect style="filter:url(file:///not-read)"/>',
        '<use href="#missing"/>',
        '<rect fill="url(#missing)"/>',
        '<image href="data:image/svg+xml;base64,AAAA"/>',
    ],
)
def test_external_and_dangling_references_never_reach_backend(body):
    with pytest.raises(EngineeringError):
        safe_parse(f'<svg xmlns="{SVG}">{body}</svg>')


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"width": 0, "height": 20}, "INVALID_ASSET_SIZE"),
        ({"width": 40}, "INVALID_ASSET_SIZE"),
        ({"width": 100.5, "height": 20}, "INVALID_ASSET_SIZE"),
        ({"node_id": "MISSING"}, "OBJECT_NOT_FOUND"),
    ],
)
def test_invalid_export_arguments_do_not_write(small_project, tmp_path, kwargs, code):
    destination = tmp_path / "asset.svg"
    with pytest.raises(EngineeringError) as error:
        export_asset(small_project, "probe", destination, **kwargs)
    assert error.value.code == code
    assert not destination.exists()


def test_raster_timeout_and_failure_are_structured():
    backend = InkscapeBackend()
    with patch("eas_hmi.export.assets.subprocess.run", side_effect=subprocess.TimeoutExpired("inkscape", 1)):
        with pytest.raises(EngineeringError) as err:
            backend.run(["--version"])
        assert err.value.code == "RASTERIZER_FAILED"
    with patch(
        "eas_hmi.export.assets.subprocess.run",
        return_value=subprocess.CompletedProcess([], 2, "", "bad export"),
    ):
        with pytest.raises(EngineeringError, match="bad export"):
            backend.run(["--version"])


@pytest.mark.parametrize("text", ["translate(nope)", "rotate(10 5)", "scale(inf)", "matrix(1 2)"])
def test_malformed_transform(text):
    with pytest.raises(EngineeringError):
        parse_transform(text)


def test_zero_size_edit_rejected_but_no_op_preserved(small_project):
    root = etree.fromstring(render(small_project, "probe"))
    frame_of(target(root, "ZERO")).set("width", "40")
    with pytest.raises(EngineeringError, match="zero-size"):
        plan_sync(small_project, etree.tostring(root))
    assert plan_sync(small_project, render(small_project, "probe")).changes == []


def test_stroked_primitive_resize_rejected_instead_of_changing_stroke_weight(small_project):
    small_project.pages[0].nodes[0].style.update({"stroke": "red", "stroke-width": "2"})
    root = etree.fromstring(render(small_project, "probe"))
    frame_of(target(root, "BOX_A"))[0].set("width", "150")
    with pytest.raises(EngineeringError, match="stroke"):
        plan_sync(small_project, etree.tostring(root))


def test_art_edit_can_reference_editor_generated_id(small_project):
    root = etree.fromstring(render(small_project, "probe"))
    content = target(root, "OPAQUE_A")
    circle = content.xpath('.//*[local-name()="mask"]/*[local-name()="circle"]')[0]
    mask = circle.getparent()
    etree.SubElement(mask, f"{{{SVG}}}use", href="#" + circle.get("id"), transform="translate(10)")
    plan = plan_sync(small_project, etree.tostring(root))
    assert {c["object_id"] for c in plan.changes} == {"OPAQUE_A"}
    assert plan_sync(plan.candidate, render(plan.candidate, "probe")).changes == []
