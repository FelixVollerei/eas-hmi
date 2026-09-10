from unittest.mock import patch

import pytest
from lxml import etree
from PIL import Image, ImageChops

from eas_hmi.errors import EngineeringError
from eas_hmi.export.assets import InkscapeBackend, export_asset
from eas_hmi.model import Node, Page, Project, canonical_hash
from eas_hmi.svg.common import SVG, safe_parse, xml_id
from eas_hmi.svg.opaque import render_payload, restore_payload
from eas_hmi.svg.renderer import render
from eas_hmi.svg.synchronizer import plan_sync


@pytest.fixture(scope="module")
def backend():
    # This is a real integration requirement, not a mocked or skipped export test.
    return InkscapeBackend()


def test_opaque_identity_and_references(small_project, opaque_source):
    before = canonical_hash(small_project)
    root = etree.fromstring(render(small_project, "probe"))
    ids = root.xpath("//@id")
    assert len(ids) == len(set(ids))
    for use in root.xpath('//*[local-name()="use"]'):
        href = use.get("{http://www.w3.org/1999/xlink}href")
        assert href[1:] in ids
    for original in ("linearGradient", "clipPath", "mask", "path", "style"):
        assert len(root.xpath(f'//*[local-name()="{original}"]')) == 2
    assert all(n.payload == opaque_source for n in small_project.all_nodes() if n.kind == "opaque_svg")
    assert canonical_hash(small_project) == before


def test_real_opaque_render_matches_source_and_duplicate(backend, small_project, opaque_source):
    page, warning = backend.png(render(small_project, "probe"), 800, 480)
    original, source_warning = backend.png(opaque_source.encode(), 120, 100)
    first = page.crop((30, 190, 150, 290))
    second = page.crop((210, 190, 330, 290))
    assert ImageChops.difference(first, second).getbbox(alpha_only=False) is None
    background = Image.new("RGBA", original.size, "#172131")
    composited = Image.alpha_composite(background, original)
    # Independent alpha compositing quantizes channels; maximum observed error is 2/255.
    assert max(hi for lo, hi in ImageChops.difference(first, composited).getextrema()) <= 2


@pytest.mark.parametrize("suffix", [".png", ".bmp", ".svg"])
def test_real_node_export_dimensions_crop_and_background(backend, small_project, tmp_path, suffix):
    target = tmp_path / ("icon" + suffix)
    before = canonical_hash(small_project)
    result = export_asset(
        small_project,
        "probe",
        target,
        node_id="OPAQUE_A",
        width=240,
        height=180,
        background="#f1e2d3",
        backend=backend,
    )
    assert result["width"] == 240 and result["height"] == 180
    if suffix == ".svg":
        root = safe_parse(target.read_bytes())
        assert root.get("width") == "240" and root.get("height") == "180"
        assert len(root.xpath("//*[@data-eas-id]")) == 1
        raster, _ = backend.png(target.read_bytes(), 240, 180)
        assert raster.getextrema()[3][0] == 0
    else:
        image = Image.open(target)
        image.load()
        assert image.size == (240, 180)
        assert image.format == suffix[1:].upper()
        if suffix == ".png":
            assert image.mode == "RGBA"
            assert image.getextrema()[3] == (0, 255)
        else:
            assert image.mode == "RGB"
            assert image.getpixel((0, 0)) == (241, 226, 211)
    assert canonical_hash(small_project) == before


def test_actual_drawing_bbox_respects_art_instead_of_engineering_frame(backend, tmp_path):
    payload = f'<svg xmlns="{SVG}" width="100" height="100"><rect x="20" y="30" width="40" height="20" fill="red"/></svg>'
    n = Node(
        id="ART",
        kind="opaque_svg",
        page_id="p",
        x=100,
        y=50,
        width=100,
        height=100,
        content_width=100,
        content_height=100,
        payload=payload,
    )
    project = Project(id="P", name="Crop probe", pages=[Page(id="p", name="p", nodes=[n])])
    result = export_asset(project, "p", tmp_path / "crop.png", node_id="ART", backend=backend)
    assert tuple(map(float, result["viewBox"].split())) == pytest.approx((120, 80, 40, 20))
    image = Image.open(tmp_path / "crop.png")
    assert image.size == (40, 20)
    assert image.getpixel((20, 10)) == (255, 0, 0, 255)


def test_page_export(backend, small_project, tmp_path):
    result = export_asset(
        small_project, "probe", tmp_path / "page.png", width=400, height=240, backend=backend
    )
    image = Image.open(result["path"])
    assert image.size == (400, 240)
    assert image.getpixel((0, 0)) == (23, 33, 49, 255)


def test_backend_failure_does_not_damage_existing_output(small_project, tmp_path):
    dest = tmp_path / "page.png"
    dest.write_bytes(b"existing output")
    backend = InkscapeBackend()
    with patch.object(backend, "png", side_effect=EngineeringError("RASTERIZER_FAILED", "probe failure")):
        with pytest.raises(EngineeringError, match="probe failure"):
            export_asset(small_project, "probe", dest, backend=backend)
    assert dest.read_bytes() == b"existing output"
    assert list(tmp_path.glob("*.tmp")) == []


def test_missing_backend():
    with pytest.raises(EngineeringError) as exc:
        InkscapeBackend("Z:/definitely-not-present/eas-inkscape.exe")
    assert exc.value.code == "RASTERIZER_UNAVAILABLE"


@pytest.mark.parametrize(
    "addition",
    [
        "<script>alert(1)</script>",
        '<image href="https://example.org/image.png"/>',
        '<rect onclick="x()"/>',
        '<style>@import "https://example.org/style.css";</style>',
        "<style>rect{fill:url(https://example.org/paint.svg#x)}</style>",
    ],
)
def test_unsafe_payload_explicitly_rejected(addition):
    source = f'<svg xmlns="{SVG}" width="100" height="100">{addition}</svg>'
    with pytest.raises(EngineeringError):
        render_payload(source, "N")


def test_dtd_rejected():
    with pytest.raises(EngineeringError):
        safe_parse(f'<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///not-read">]><svg xmlns="{SVG}">&x;</svg>')


def test_opaque_duplicate_id_and_unscoped_css_rejected():
    with pytest.raises(EngineeringError, match="Duplicate"):
        render_payload(f'<svg xmlns="{SVG}"><g id="x"/><g id="x"/></svg>', "N")
    wrapper = render_payload(
        f'<svg xmlns="{SVG}"><style>rect{{fill:red}}</style><rect width="10" height="10"/></svg>', "N"
    )
    wrapper.xpath('.//*[local-name()="style"]')[0].text = "rect {fill:blue}"
    with pytest.raises(EngineeringError, match="scope"):
        restore_payload(wrapper, "N")


def test_real_edited_matrix_equals_rerendered_image(backend, small_project):
    root = etree.fromstring(render(small_project, "probe"))
    box = root.xpath('//*[@data-eas-id="BOX_A"]')[0]
    box.set("transform", "translate(90 15) rotate(25 30 30) scale(1.25)")
    edited = etree.tostring(root)
    candidate = plan_sync(small_project, edited).candidate
    first, _ = backend.png(edited, 800, 480)
    second, _ = backend.png(render(candidate, "probe"), 800, 480)
    # Geometry-equivalent views can differ by <=1 at antialiased boundary pixels.
    extrema = ImageChops.difference(first, second).getextrema()
    assert max(hi for lo, hi in extrema) <= 1


def test_actual_inkscape_save_is_no_op(backend, small_project, tmp_path):
    source, saved = tmp_path / "semantic.svg", tmp_path / "inkscape-saved.svg"
    source.write_bytes(render(small_project, "probe"))
    before = source.read_bytes()
    backend.run([source, "--export-type=svg", f"--export-filename={saved}"])
    assert saved.is_file()
    plan = plan_sync(small_project, saved.read_bytes())
    assert plan.changes == []
    assert source.read_bytes() == before


@pytest.mark.parametrize(
    "action", ["transform-translate:40,10", "transform-rotate:30", "transform-scale:1.25"]
)
def test_actual_inkscape_transforms_round_trip(backend, small_project, tmp_path, action):
    source, edited = tmp_path / "input.svg", tmp_path / "edited.svg"
    source.write_bytes(render(small_project, "probe"))
    actions = f"select-by-id:{xml_id('BOX_A')};{action};export-filename:{edited};export-do"
    backend.run([source, "--export-type=svg", f"--actions={actions}"])
    plan = plan_sync(small_project, edited.read_bytes())
    changed_ids = {change["object_id"] for change in plan.changes}
    assert changed_ids == {"BOX_A"}
    box = next(n for n in plan.candidate.all_nodes() if n.id == "BOX_A")
    if action.startswith("transform-translate"):
        assert (box.x, box.y) == pytest.approx((70, 40))
    else:
        assert (box.width, box.height, box.rotation) != (100, 60, 0)
    first, _ = backend.png(edited.read_bytes(), 800, 480)
    second, _ = backend.png(render(plan.candidate, "probe"), 800, 480)
    assert max(hi for lo, hi in ImageChops.difference(first, second).getextrema()) <= 2


def test_nested_rotated_group_asset_bounds(backend, small_project, tmp_path):
    group = next(n for n in small_project.all_nodes() if n.id == "ASSEMBLY")
    group.rotation = 90
    result = export_asset(small_project, "probe", tmp_path / "group.png", node_id="ASSEMBLY", backend=backend)
    assert tuple(map(float, result["viewBox"].split())) == pytest.approx((215, 45, 30, 50))
    image = Image.open(result["path"])
    assert image.getpixel((15, 25)) == (230, 155, 52, 255)
