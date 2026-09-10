import json
from pathlib import Path

import pytest
from lxml import etree
from PIL import Image, ImageChops
from typer.testing import CliRunner

from eas_hmi.cli import app
from eas_hmi.errors import EngineeringError
from eas_hmi.export.assets import InkscapeBackend
from eas_hmi.export.bundle import export_bundle
from eas_hmi.model import canonical_hash
from eas_hmi.operations.transaction import Store
from eas_hmi.query import Query
from eas_hmi.svg.common import safe_parse, tag
from eas_hmi.svg.importer import append_import, import_svg
from eas_hmi.svg.renderer import SUPPORTED_KINDS, render, validate_image_uri
from eas_hmi.svg.synchronizer import frame_of, identity_map, plan_sync


def pixels(data, width=800, height=500):
    return InkscapeBackend().png(data, width, height)[0]


def assert_same_pixels(before, after, tolerance=0):
    delta = ImageChops.difference(before, after)
    assert max(high for low, high in delta.getextrema()) <= tolerance


def test_all_kinds_deterministic_and_noop(component_project):
    p = component_project
    assert {n.kind for n in p.all_nodes()} == SUPPORTED_KINDS
    before = canonical_hash(p)
    svg = render(p, "components")
    assert svg == render(p, "components")
    assert plan_sync(p, svg).changes == []
    assert canonical_hash(p) == before


@pytest.mark.parametrize(
    "node_id", ["GROUP", "TITLE", "SHAPE", "IMAGE", "OPAQUE", "CARD_A", "VALUE", "STATUS", "LINK"]
)
def test_all_kinds_tagged_geometry_and_visibility(component_project, node_id):
    p = component_project
    root = safe_parse(render(p, "components"))
    g = identity_map(root)[node_id]
    frame = frame_of(g)
    frame.set("x", str(float(frame.get("x")) + 7))
    frame.set("width", str(float(frame.get("width")) * 1.2))
    g.set("visibility", "hidden")
    candidate = plan_sync(p, etree.tostring(root)).candidate
    old, new = Query(p).node(node_id), Query(candidate).node(node_id)
    assert new.x == pytest.approx(old.x + 7)
    assert new.width == pytest.approx(old.width * 1.2)
    assert not new.visible
    assert {n.id for n in p.all_nodes() if n.model_dump() != Query(candidate).node(n.id).model_dump()} == {
        node_id
    }


@pytest.mark.parametrize("node_id", ["CARD_A", "VALUE", "STATUS"])
def test_component_caption_sync_preserves_other_artwork(component_project, node_id):
    root = safe_parse(render(component_project, "components"))
    caption = identity_map(root)[node_id].find('.//*[@data-eas-part="caption"]')
    caption.text = "Edited caption"
    result = plan_sync(component_project, etree.tostring(root))
    assert Query(result.candidate).node(node_id).text == "Edited caption"
    assert len(result.changes) == 1
    caption.set("x", "19")
    with pytest.raises(EngineeringError, match="UNSUPPORTED HUMAN EDIT"):
        plan_sync(component_project, etree.tostring(root))


@pytest.mark.parametrize(
    "uri", [None, "https://example.com/pic.png", "data:image/png;base64,@@@", "data:image/png;base64,YWJj"]
)
def test_invalid_image_asset_clear_error(component_project, uri):
    Query(component_project).node("IMAGE").image_uri = uri
    with pytest.raises(EngineeringError, match="image|Image|PNG|identify|base64"):
        render(component_project, "components")


def test_image_mime_mismatch(component_project):
    uri = Query(component_project).node("IMAGE").image_uri.replace("image/png", "image/jpeg")
    with pytest.raises(EngineeringError, match="MIME"):
        validate_image_uri(uri)


def test_semantic_import_all_kinds_pixels_and_references(component_project):
    original = render(component_project, "components")
    result = import_svg(original)
    imported = result.project
    assert {n.id for n in imported.all_nodes()} == {n.id for n in component_project.all_nodes()}
    assert {n.kind for n in imported.all_nodes()} == SUPPORTED_KINDS
    assert Query(imported).node("LINK").target_ref == "CARD_B"
    assert Query(imported).node("TITLE").parent_id == "GROUP"
    assert Query(imported).node("CARD_A").bindings[0].point_ref == "P01_RUN"
    assert all(p.metadata["unresolved_import"] for p in imported.points)
    assert all(not p.tag and p.datatype == "unknown" for p in imported.points)
    assert result.warnings
    assert_same_pixels(pixels(original), pixels(render(imported, "components")))


def test_semantic_import_actual_inkscape_save(component_project, tmp_path):
    source = tmp_path / "semantic.svg"
    source.write_bytes(render(component_project, "components"))
    backend = InkscapeBackend()
    backend.run([str(source), "--actions=select-clear;document-save;document-close"])
    saved = source.read_bytes()
    imported = import_svg(saved).project
    assert len(imported.all_nodes()) == 11
    assert_same_pixels(pixels(saved), pixels(render(imported, "components")))


@pytest.mark.parametrize("change", ["artwork", "kind", "binding", "duplicate", "image", "skew", "asset"])
def test_semantic_import_rejects_unaccounted_source(component_project, change):
    root = safe_parse(render(component_project, "components"))
    g = identity_map(root)["CARD_A"]
    if change == "artwork":
        etree.SubElement(root, tag("circle"), cx="20", cy="20", r="10")
    elif change == "kind":
        g.set("data-eas-kind", "unknown")
    elif change == "binding":
        g.set("data-eas-bindings", '["invalid"]')
    elif change == "duplicate":
        g.set("id", identity_map(root)["CARD_B"].get("id"))
    elif change == "image":
        image = identity_map(root)["IMAGE"].find('.//*[@data-eas-part="body"]')
        image.set("href", "https://example.com/x.png")
    elif change == "skew":
        g.set("transform", "matrix(1 0 .5 1 0 0)")
    else:
        root.set("data-eas-artifact", "asset")
    with pytest.raises(EngineeringError):
        import_svg(etree.tostring(root))


@pytest.mark.parametrize(
    "source,expected_count",
    [
        (
            '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="120"><g id="A"><rect x="10" y="10" width="40" height="30" fill="red"/></g><path d="M70 10h60v30h-60z" fill="blue"/></svg>',
            2,
        ),
        (
            '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="120" opacity=".7"><rect x="10" y="10" width="90" height="70" fill="red"/><rect x="40" y="40" width="90" height="70" fill="blue"/></svg>',
            1,
        ),
        (
            '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="120"><style>.art {fill:#17b890}</style><g class="art"><path d="M10 10h60v70h-60z"/></g></svg>',
            1,
        ),
        (
            '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="200" height="120"><rect id="box" x="10" y="10" width="60" height="30" fill="red"/><use xlink:href="#box" x="70"/></svg>',
            1,
        ),
    ],
)
def test_ordinary_import_preserves_pixels_and_stable_ids(source, expected_count):
    result = import_svg(source)
    p = result.project
    assert len(p.all_nodes()) == expected_count
    assert all(n.kind == "opaque_svg" for n in p.all_nodes())
    assert canonical_hash(p) == canonical_hash(import_svg(source).project)
    assert_same_pixels(
        pixels(source.encode(), 200, 120), pixels(render(p, p.pages[0].id), 200, 120), tolerance=2
    )


def test_opaque_fixture_import_preserves_gradients_clip_masks(opaque_source):
    p = import_svg(opaque_source).project
    assert_same_pixels(
        pixels(opaque_source.encode(), 240, 200), pixels(render(p, p.pages[0].id), 240, 200), tolerance=2
    )


def test_defs_referencing_visible_artwork_stays_composite():
    source = b"""<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="200" height="120">
    <defs><clipPath id="clip"><use xlink:href="#box"/></clipPath></defs>
    <rect id="box" x="10" y="10" width="40" height="60" fill="red"/>
    <rect x="0" y="0" width="100" height="100" fill="blue" clip-path="url(#clip)"/>
    </svg>"""
    result = import_svg(source)
    assert len(result.project.all_nodes()) == 1
    assert_same_pixels(
        pixels(source, 200, 120), pixels(render(result.project, result.project.pages[0].id), 200, 120)
    )


def test_import_cli_source_unchanged_append_undo_and_reject(tmp_path, component_project):
    source = tmp_path / "source.svg"
    source.write_bytes(render(component_project, "components"))
    original = source.read_bytes()
    path = tmp_path / "project"
    runner = CliRunner()

    def call(*args, code=0):
        result = runner.invoke(app, ["--project", str(path), *map(str, args)])
        assert result.exit_code == code, (result.stdout, result.exception)
        return json.loads(result.stdout)

    assert call("import-svg", source)["committed"]
    state = Store(path)
    baseline = canonical_hash(state.load())
    before = state.head.read_bytes()
    assert call("import-svg", source, code=2)["code"] == "DUPLICATE_ID"
    assert state.head.read_bytes() == before
    other = tmp_path / "other.svg"
    other.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="50" height="50"><circle cx="25" cy="25" r="20"/></svg>'
    )
    assert call("import-svg", other)["revision"] == 1
    assert len(state.load().pages) == 2
    call("undo")
    assert canonical_hash(state.load()) == baseline
    assert source.read_bytes() == original
    bad = tmp_path / "bad.svg"
    bad.write_text('<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>')
    before = state.head.read_bytes()
    call("import-svg", bad, code=2)
    assert state.head.read_bytes() == before


def test_append_import_reuses_enriched_registries(component_project):
    imported = import_svg(render(component_project, "components")).project
    p = component_project.model_copy(deep=True)
    p.pages = [p.pages[1]]
    append_import(p, imported)
    assert len(p.points) == 5 and p.points[0].tag == "SYN.P01.RUN"
    assert p.templates[0].required_bindings == ["run", "fault"]


def test_asset_cli_selectors_formats_sizes_and_history(component_project, tmp_path):
    path = tmp_path / "project"
    state = Store(path)
    state.initialize(component_project)
    original = state.head.read_bytes()
    result = CliRunner().invoke(
        app,
        [
            "--project",
            str(path),
            "export-assets",
            "--template",
            "PUMP",
            "--width",
            "96",
            "--height",
            "64",
            "--json",
        ],
    )
    assert result.exit_code == 0, (result.stdout, result.exception)
    report = json.loads(result.stdout)
    assert len(report["assets"]) == 6
    assert {a["node_id"] for a in report["assets"]} == {"CARD_A", "CARD_B"}
    assert Path(report["directory"], "manifest.json").exists()
    for asset in report["assets"]:
        assert Path(asset["path"]).is_file()
        if asset["format"] != "svg":
            with Image.open(asset["path"]) as image:
                assert image.size == (96, 64)
                assert image.mode == ("RGBA" if asset["format"] == "png" else "RGB")
    assert state.head.read_bytes() == original
    all_pages = export_bundle(component_project, tmp_path / "pages", formats=("svg",))
    assert {a["page"] for a in all_pages["assets"]} == {"components", "spare"}
    node = export_bundle(
        component_project, tmp_path / "image", node_id="IMAGE", formats=("png",), width=32, height=32
    )
    with Image.open(node["assets"][0]["path"]) as image:
        assert image.getextrema()[3][1] < 255
