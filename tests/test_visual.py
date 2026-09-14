"""Release gates exercise real SVG rendering, hostile patches and persisted history."""

import asyncio
import json
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest
from PIL import Image, ImageDraw

from eas_visual import VisualProject
from eas_visual.errors import VisualError
from eas_visual.pixels import pixel_hash


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    root = tmp_path_factory.mktemp("visual-template")
    image = Image.new("RGB", (240, 180), "#eee3cf")
    draw = ImageDraw.Draw(image)
    draw.rectangle((25, 35, 64, 89), fill="#498fa2")
    draw.rectangle((30, 40, 59, 52), fill="white")
    source = root / "input.png"
    image.save(source)
    VisualProject.create(source, root / "scene.eas")
    project = VisualProject(root / "scene.eas")
    project.promote("bed", bbox=[25, 35, 65, 90])
    return project.store.root


@pytest.fixture
def project(template, tmp_path):
    shutil.copytree(template, tmp_path / "scene.eas")
    return VisualProject(tmp_path / "scene.eas")


def head(project):
    manifest, records = project.store.load()
    return records[manifest["current_revision"]]


def baseline_bytes(project):
    return {p.name: p.read_bytes() for p in (project.store.root / "baseline").iterdir()}


def move(project):
    return project.move("bed", 75, 0, donor_bbox=[180, 130, 210, 155])


def test_baseline_move_and_exact_extraction(project):
    baseline = baseline_bytes(project)
    before = np.asarray(Image.open(project.inspect()["output_png"]).convert("RGB"))
    result = move(project)
    after = np.asarray(Image.open(result["output_png"]).convert("RGB"))
    assert np.array_equal(before[35:90, 25:65], after[35:90, 100:140])
    assert np.all(after[35:90, 25:65] == before[130, 180])
    assert result["validation"]["untouched_changed_pixels"] == 0
    assert baseline_bytes(project) == baseline
    assert head(project)["object_appearance_mae"] == 0


def test_undo_redo_checkout_reset_branch_reopen(project):
    baseline = baseline_bytes(project)
    first = move(project)
    second = project.edit("bed", "blue")
    assert project.navigate("undo")["output_hash"] == first["output_hash"]
    assert VisualProject(project.store.root).navigate("redo")["output_hash"] == second["output_hash"]
    assert project.navigate("checkout", first["revision"])["output_hash"] == first["output_hash"]
    branch = project.move("bed", -20, 0, donor_bbox=[180, 130, 210, 155])
    assert second["revision"] in branch["revisions"]
    assert branch["redo"] == []
    assert project.navigate("reset")["output_hash"] == first["baseline_hash"]
    assert project.inspect()["objects"] == {}
    assert project.navigate("checkout", second["revision"])["output_hash"] == second["output_hash"]
    manifest = (project.store.root / "manifest.json").read_bytes()
    replayed = VisualProject(project.store.root).replay()
    assert len(replayed["revisions"]) == 5
    assert all(r["matches"] for r in replayed["revisions"])
    assert (project.store.root / "manifest.json").read_bytes() == manifest
    assert baseline_bytes(project) == baseline


def test_provider_pollution_is_contained_with_blending(project, tmp_path):
    request = project.prepare("bed", "replace locally", margin=2)
    context = Image.open(request["context_file"]).convert("RGB")
    candidate = Image.fromarray(255 - np.asarray(context))  # changes every channel everywhere
    path = tmp_path / "hostile.png"
    candidate.save(path)
    result = project.edit(
        "bed",
        provider="external-patch",
        request_id=request["request_id"],
        patch_path=path,
        metadata={"provider": "test-hostile", "model": None, "seed": None},
    )
    assert (
        head(project)["provider"]["context_outside_mask_before_composite"]["untouched_changed_pixel_ratio"]
        == 1
    )
    assert result["validation"]["untouched_region_mae"] == 0
    assert result["validation"]["untouched_changed_pixels"] == 0
    assert result["validation"]["untouched_max_absolute_difference"] == 0


def test_wrong_size_refused_then_explicit_normalize(project, tmp_path):
    request = project.prepare("bed", "blue")
    patch = tmp_path / "small.png"
    Image.new("RGB", (10, 10), "blue").save(patch)
    before = (project.store.root / "manifest.json").read_bytes()
    args = {
        "provider": "external-patch",
        "request_id": request["request_id"],
        "patch_path": patch,
        "metadata": {"provider": "test"},
    }
    with pytest.raises(VisualError, match="explicit normalize"):
        project.edit("bed", **args)
    assert (project.store.root / "manifest.json").read_bytes() == before
    result = project.edit("bed", **args, normalize=True)
    assert result["validation"]["untouched_changed_pixels"] == 0
    assert head(project)["provider"]["normalized"] is True
    assert result["quality_warnings"]


def test_stale_request_refuses_to_apply(project, tmp_path):
    request = project.prepare("bed", "blue")
    patch = tmp_path / "patch.png"
    shutil.copyfile(request["context_file"], patch)
    move(project)
    with pytest.raises(VisualError) as exc:
        project.edit(
            "bed",
            provider="external-patch",
            request_id=request["request_id"],
            patch_path=patch,
            metadata={"provider": "test"},
        )
    assert exc.value.code == "STALE_REQUEST"


@pytest.mark.parametrize("kind", ["history", "patch", "baseline", "manifest", "inactive-patch"])
def test_corruption_blocks_operations_without_manifest_change(project, kind):
    move(project)
    record = head(project)
    if kind == "inactive-patch":
        project.navigate("undo")
    manifest, _ = project.store.load()
    ref = {
        "history": manifest["revisions"][record["edit_id"]],
        "patch": record["patch_reference"][0],
        "inactive-patch": record["patch_reference"][0],
        "baseline": manifest["baseline"]["png"],
    }.get(kind)
    path = project.store.path(ref["path"]) if ref else project.store.root / "manifest.json"
    path.write_bytes(path.read_bytes() + b"corruption")
    before = (project.store.root / "manifest.json").read_bytes()
    with pytest.raises(VisualError):
        project.navigate("reset")
    assert (project.store.root / "manifest.json").read_bytes() == before


@pytest.mark.parametrize(
    "operation,code",
    [
        (lambda p: p.move("missing", 1, 1), "OBJECT_NOT_FOUND"),
        (lambda p: p.move("bed", -100, 0), "TARGET_OUTSIDE"),
        (lambda p: p.move("bed", 10, 0), "REPAIR_REQUIRED"),
        (lambda p: p.promote("large", bbox=[0, 0, 240, 180]), "INVALID_SUPPORT"),
        (lambda p: p.prepare("bed", "edit", context_bbox=[0, 0, 240, 180]), "WHOLE_IMAGE_PROVIDER"),
        (lambda p: p.prepare("bed", "edit", context_bbox=[30, 40, 50, 60]), "CONTEXT_TOO_SMALL"),
        (lambda p: p.promote("overlap", bbox=[30, 40, 50, 60]), "OBJECT_CONFLICT"),
        (lambda p: p.move("bed", 1.5, 0, repair="none"), "INVALID_TRANSFORM"),
    ],
)
def test_fail_closed_selection_and_target(project, operation, code):
    before = (project.store.root / "manifest.json").read_bytes()
    with pytest.raises(VisualError) as exc:
        operation(project)
    assert exc.value.code == code
    assert (project.store.root / "manifest.json").read_bytes() == before


def test_bad_binary_mask_and_alpha_patch(project, tmp_path):
    mask = tmp_path / "mask.png"
    Image.new("L", (240, 180), 127).save(mask)
    with pytest.raises(VisualError) as exc:
        project.promote("bad", mask_path=mask)
    assert exc.value.code == "INVALID_MASK"
    request = project.prepare("bed", "edit")
    patch = tmp_path / "transparent.png"
    Image.new("RGBA", Image.open(request["context_file"]).size, (0, 0, 0, 0)).save(patch)
    with pytest.raises(VisualError) as exc:
        project.edit(
            "bed",
            provider="external-patch",
            request_id=request["request_id"],
            patch_path=patch,
            metadata={"provider": "test"},
        )
    assert exc.value.code == "ALPHA_UNSUPPORTED"


def test_renderer_outside_change_aborts(project, monkeypatch):
    compose = project.compose

    def faulty(*args):
        svg, actual, warning = compose(*args)
        actual.putpixel((0, 0), (0, 0, 0))
        return svg, actual, warning

    monkeypatch.setattr(project, "compose", faulty)
    before = (project.store.root / "manifest.json").read_bytes()
    with pytest.raises(VisualError) as exc:
        move(project)
    assert exc.value.code == "OUTSIDE_CHANGE"
    assert (project.store.root / "manifest.json").read_bytes() == before


def test_failure_before_manifest_commit_preserves_previous(project, monkeypatch):
    before = (project.store.root / "manifest.json").read_bytes()

    def fail(_):
        raise OSError("simulated disk failure before commit")

    monkeypatch.setattr(project.store, "write_manifest", fail)
    with pytest.raises(OSError):
        move(project)
    assert (project.store.root / "manifest.json").read_bytes() == before
    assert VisualProject(project.store.root).inspect()["revision"].startswith("0001-")


def test_generative_repair_and_explicit_none(project, tmp_path):
    request = project.prepare("bed", "remove bed, restore floor", purpose="repair", margin=1)
    patch = tmp_path / "floor.png"
    Image.new("RGB", Image.open(request["context_file"]).size, "#eee3cf").save(patch)
    result = project.move(
        "bed",
        75,
        0,
        repair="generative",
        request_id=request["request_id"],
        patch_path=patch,
        metadata={"provider": "test-floor"},
    )
    assert result["validation"]["untouched_changed_pixels"] == 0
    assert any("estimate" in warning for warning in result["quality_warnings"])
    result = project.move("bed", 10, 0, repair="none")
    assert any("retains source" in warning for warning in result["quality_warnings"])


def test_protected_export(project, tmp_path):
    baseline = baseline_bytes(project)
    with pytest.raises(VisualError):
        project.export(project.store.root / "baseline" / "new.png")
    target = tmp_path / "export.png"
    project.export(target)
    assert pixel_hash(Image.open(target)) == project.inspect()["output_hash"]
    with pytest.raises(VisualError):
        project.export(target)
    assert baseline_bytes(project) == baseline


def test_cli_json_real_process(project):
    for suffix in ([], ["--json"]):
        run = subprocess.run(
            [sys.executable, "-m", "eas_visual.cli", "inspect", str(project.store.root), *suffix],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert run.returncode == 0, run.stderr
        assert json.loads(run.stdout)["result"]["revision"] == project.inspect()["revision"]
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "eas_visual.cli",
            "move",
            str(project.store.root),
            "--object",
            "bad-id",
            "--dx",
            "1",
            "--dy",
            "2",
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert run.returncode == 2
    assert json.loads(run.stdout)["error"]["code"] == "OBJECT_NOT_FOUND"


def test_mcp_stdio_real_session(project, tmp_path):
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def scenario():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "eas_visual.mcp_server"],
            env={**os.environ, "EAS_VISUAL_ROOTS": str(tmp_path)},
        )
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
            await session.initialize()
            listing = await session.list_tools()
            assert len(listing.tools) == 6
            result = await session.call_tool("inspect_visual", {"project": str(project.store.root)})
            assert not result.isError
            parsed = json.loads(result.content[0].text)
            assert parsed["ok"] and parsed["result"]["revision"] == project.inspect()["revision"]
            denied = await session.call_tool("inspect_visual", {"project": str(tmp_path.parent)})
            assert json.loads(denied.content[0].text)["error"]["code"] == "PATH_NOT_ALLOWED"

    asyncio.run(scenario())


def test_cli_unicode_label_is_utf8_json_on_legacy_console(project):
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "eas_visual.cli",
            "promote",
            str(project.store.root),
            "--label",
            "椅子🪑",
            "--bbox",
            "[150,20,160,30]",
        ],
        env={**os.environ, "PYTHONIOENCODING": "ascii"},
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert run.returncode == 0, run.stderr
    response = json.loads(run.stdout.decode("utf-8"))
    assert any(o["semantic_label"] == "椅子🪑" for o in response["result"]["objects"].values())
