"""Behavior checks for the bounded Minecraft Java 26.3 EAFS profile."""

from __future__ import annotations

import gzip
import io
import json
import struct
import sys
import zlib
from pathlib import Path

import nbtlib
import pytest
from nbtlib import tag
from typer.testing import CliRunner

from sfe_files.cli import app
from sfe_files.minecraft_structure import compile_plan, load_scene, parse_scene, resolve, statistics
from sfe_files.minecraft_structure import inspect as inspect_scene
from sfe_files.minecraft_structure_cli import main as scene_main
from sfe_files.minecraft_structure_view import render_html
from sfe_files.plan_ops import FileEditError
from sfe_files.service import apply_data, contract_data, inspect_data, preview_data, status_data

DATA_VERSION = 5023


def _small_scene() -> dict:
    return {"format": "eafs-minecraft-structure/0.1", "origin": [0, 0], "size": [2, 2],
            "elements": [
                {"id": "site", "parent": None, "role": "site", "block": None,
                 "boxes": [], "overrides": []},
                {"id": "floor", "parent": "site", "role": "floor",
                 "block": "minecraft:stone_bricks",
                 "boxes": [[[0, -40, 0], [1, -40, 1]]], "overrides": []},
                {"id": "column", "parent": "site", "role": "column",
                 "block": "minecraft:red_terracotta",
                 "boxes": [[[0, -40, 0], [0, -40, 0]]], "overrides": ["floor"]},
            ]}


def test_semantic_scene_resolves_roles_and_compiles_to_eafs(tmp_path: Path):
    world = _world(tmp_path)
    source_hash = status_data(world)["sha256"]
    resolved = resolve(parse_scene(_small_scene()))
    stats = statistics(resolved)
    assert stats["final_blocks"] == 4
    assert stats["declared_overlaps"] == 1
    assert stats["by_role"] == {"column": 1, "floor": 3}
    assert inspect_scene(resolved, 0, -40, 0)["ancestors"] == ["site"]
    assert inspect_scene(resolved, 0, -40, 0)["element"] == "column"
    assert inspect_scene(resolved, 8, -40, 0)["element"] is None
    assert "点击平面查看方块归属" in render_html(resolved)
    plan = compile_plan(resolved, world)
    assert plan["base"]["sha256"] == source_hash
    assert len(plan["steps"][0]["runs"]) == 3
    payload = json.dumps(plan).encode()
    preview = preview_data(world, payload)
    assert preview["changed_blocks"] == 4
    output = tmp_path / "from-scene"
    applied = apply_data(world, payload, output)
    assert applied["after_sha256"] == preview["after_sha256"]
    assert status_data(world)["sha256"] == source_hash
    assert inspect_data(output, pointer="/blocks/minecraft:overworld/0/-40/0")["block"] == "minecraft:red_terracotta"


def test_semantic_scene_rejects_undeclared_and_unused_overrides():
    scene = _small_scene()
    scene["elements"][2]["overrides"] = []
    with pytest.raises(ValueError, match="Undeclared overlap"):
        resolve(parse_scene(scene))
    scene["elements"][2]["boxes"] = [[[1, -39, 0], [1, -39, 0]]]
    scene["elements"][2]["overrides"] = ["floor"]
    with pytest.raises(ValueError, match="unused overrides"):
        resolve(parse_scene(scene))


def test_semantic_scene_rejects_self_overlap_and_oversized_box():
    scene = _small_scene()
    scene["elements"] = scene["elements"][:2]
    scene["elements"][1]["boxes"].append([[0, -40, 0], [0, -40, 0]])
    with pytest.raises(ValueError, match="overlaps itself"):
        resolve(parse_scene(scene))
    scene["size"] = [300, 300]
    scene["elements"][1]["boxes"] = [[[0, 0, 0], [299, 5, 299]]]
    with pytest.raises(ValueError, match="exceeds 500000"):
        resolve(parse_scene(scene))


@pytest.mark.parametrize("mutation,expected", [
    (lambda scene: scene["elements"][2].update(parent="missing"), "parent"),
    (lambda scene: scene["elements"][2].update(block="minecraft:command_block"), "supported block"),
    (lambda scene: scene["elements"][2].update(boxes=[[[2, -40, 0], [2, -40, 0]]]), "exceeds scene"),
    (lambda scene: scene["elements"][2].update(role="unclassified"), "unsupported role"),
])
def test_semantic_scene_rejects_invalid_structure(mutation, expected):
    scene = _small_scene()
    mutation(scene)
    with pytest.raises(ValueError, match=expected):
        parse_scene(scene)


def test_courtyard_example_is_inspectable():
    scene = load_scene(Path("examples/minecraft/courtyard.scene.json"))
    resolved = resolve(scene)
    assert statistics(resolved)["final_blocks"] == 9287
    assert inspect_scene(resolved, -88, 67, -75)["element"] == "hall-front-columns"


def test_semantic_cli_reports_bad_scene_without_traceback(tmp_path: Path, monkeypatch, capsys):
    bad = tmp_path / "bad.scene.json"
    bad.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["eafs-minecraft-structure", str(bad), "stats"])
    with pytest.raises(SystemExit) as stopped:
        scene_main()
    assert stopped.value.code == 2
    assert json.loads(capsys.readouterr().out)["code"] == "INVALID_SCENE"


def _nbt_bytes(document: nbtlib.File) -> bytes:
    stream = io.BytesIO()
    document.write(stream)
    return stream.getvalue()


def _chunk_record(chunk_x: int, block_entity: bool = False,
                  *, plain_palette: bool = False, derived_cache: bool = False) -> bytes:
    palette = (tag.List[tag.String]([tag.String("minecraft:stone")]) if plain_palette
               else tag.List[tag.Compound]([tag.Compound({"": tag.String("minecraft:stone")})]))
    section = tag.Compound({"Y": tag.Byte(-3),
                            "block_states": tag.Compound({"palette": palette})})
    if derived_cache:
        section["SkyLight"] = tag.ByteArray([0] * 2048)
    chunk = nbtlib.File({
        "DataVersion": tag.Int(DATA_VERSION), "xPos": tag.Int(chunk_x), "zPos": tag.Int(0),
        "sections": tag.List[tag.Compound]([section]),
        "block_entities": tag.List[tag.Compound]([
            tag.Compound({"x": tag.Int(0), "y": tag.Int(-40), "z": tag.Int(0)})
        ] if block_entity else []),
    })
    if derived_cache:
        chunk["Heightmaps"] = tag.Compound({"WORLD_SURFACE": tag.LongArray([0] * 37)})
        chunk["isLightOn"] = tag.Byte(1)
    compressed = zlib.compress(_nbt_bytes(chunk))
    return struct.pack(">I", len(compressed) + 1) + b"\x02" + compressed


def _world(tmp_path: Path, *, version: str = "26.3", block_entity: bool = False,
           chunk_count: int = 2) -> Path:
    world = tmp_path / "source-world"
    world.mkdir(parents=True)
    level = nbtlib.File({"Data": tag.Compound({
        "DataVersion": tag.Int(DATA_VERSION),
        "Version": tag.Compound({"Name": tag.String(version), "Id": tag.Int(DATA_VERSION)}),
    })})
    (world / "level.dat").write_bytes(gzip.compress(_nbt_bytes(level), mtime=0))
    region = bytearray((2 + chunk_count) * 4096)
    for chunk_x in range(chunk_count):
        region[chunk_x * 4 : chunk_x * 4 + 4] = (2 + chunk_x).to_bytes(3, "big") + b"\x01"
        record = _chunk_record(chunk_x, block_entity and chunk_x == 0)
        start = (2 + chunk_x) * 4096
        region[start : start + len(record)] = record
    region_path = world / "dimensions/minecraft/overworld/region/r.0.0.mca"
    region_path.parent.mkdir(parents=True)
    region_path.write_bytes(region)
    shared = world / "data/minecraft/unchanged.dat"
    shared.parent.mkdir(parents=True)
    shared.write_bytes(b"keep these bytes")
    return world


def _plan(world: Path, steps: list[dict]) -> bytes:
    return json.dumps({"protocol": "sfe/0.1", "format": "minecraft-java-world",
                       "base": {"sha256": status_data(world)["sha256"]}, "steps": steps}).encode()


def _step(old: str = "minecraft:stone", new: str = "minecraft:diamond_block") -> dict:
    return {"op": "replace-block", "dimension": "minecraft:overworld",
            "x": 0, "y": -40, "z": 0, "from": old, "to": new}


def test_world_contract_preview_apply_and_source_preservation(tmp_path: Path):
    world = _world(tmp_path)
    pointer = "/blocks/minecraft:overworld/0/-40/0"
    assert contract_data()["formats"]["minecraft-java-world"]["kind"] == "directory"
    assert status_data(world)["summary"]["version"] == "26.3"
    assert inspect_data(world, pointer=pointer)["block"] == "minecraft:stone"
    plan = _plan(world, [_step()])
    preview = preview_data(world, plan)
    assert preview["changes"][0]["after"] == "minecraft:diamond_block"
    assert preview["committed"] is False
    assert inspect_data(world, pointer=pointer)["block"] == "minecraft:stone"
    output = tmp_path / "edited-world"
    result = apply_data(world, plan, output)
    assert result["committed"] is True
    assert result["after_sha256"] == preview["after_sha256"] == status_data(output)["sha256"]
    assert inspect_data(output, pointer=pointer)["block"] == "minecraft:diamond_block"
    assert (output / "level.dat").read_bytes() == (world / "level.dat").read_bytes()
    assert (output / "data/minecraft/unchanged.dat").read_bytes() == b"keep these bytes"
    original_region = (world / "dimensions/minecraft/overworld/region/r.0.0.mca").read_bytes()
    edited_region = (output / "dimensions/minecraft/overworld/region/r.0.0.mca").read_bytes()
    assert edited_region[12288:16384] == original_region[12288:16384]
    assert inspect_data(output, pointer="/blocks/minecraft:overworld/16/-40/0")["block"] == "minecraft:stone"
    assert inspect_data(world, pointer=pointer)["block"] == "minecraft:stone"


def test_world_stale_and_failed_multistep_do_not_publish(tmp_path: Path):
    world = _world(tmp_path)
    plan = _plan(world, [_step()])
    (world / "data/minecraft/unchanged.dat").write_bytes(b"changed")
    with pytest.raises(FileEditError, match="World bytes no longer match") as stale:
        apply_data(world, plan, tmp_path / "stale-output")
    assert stale.value.code == "BASE_CONFLICT"
    assert not (tmp_path / "stale-output").exists()
    plan = _plan(world, [_step(), _step()])
    with pytest.raises(FileEditError, match="Expected minecraft:stone") as failed:
        apply_data(world, plan, tmp_path / "failed-output")
    assert failed.value.code == "BLOCK_CONFLICT"
    assert not (tmp_path / "failed-output").exists()


def test_world_version_and_unsafe_block_rejected(tmp_path: Path):
    old_world = _world(tmp_path, version="26.2")
    with pytest.raises(FileEditError) as version:
        status_data(old_world)
    assert version.value.code == "UNSUPPORTED_VERSION"
    world = _world(tmp_path / "second")
    with pytest.raises(FileEditError) as missing:
        inspect_data(world, pointer="/blocks/minecraft:overworld/32/-40/0")
    assert missing.value.code == "CHUNK_NOT_FOUND"
    plan = _plan(world, [_step(new="minecraft:air")])
    with pytest.raises(FileEditError) as unsafe:
        preview_data(world, plan)
    assert unsafe.value.code == "UNSUPPORTED_BLOCK"
    entity_world = _world(tmp_path / "third", block_entity=True)
    with pytest.raises(FileEditError) as entity:
        preview_data(entity_world, _plan(entity_world, [_step()]))
    assert entity.value.code == "UNSUPPORTED_BLOCK"


def test_cli_accepts_world_directory(tmp_path: Path):
    world = _world(tmp_path)
    result = CliRunner().invoke(app, ["status", str(world)])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["format"] == "minecraft-java-world"
    plan_path = tmp_path / "edit.plan.json"
    plan_path.write_bytes(_plan(world, [_step()]))
    preview = CliRunner().invoke(app, ["preview", str(world), str(plan_path)])
    assert preview.exit_code == 0
    assert json.loads(preview.stdout)["diff_kind"] == "semantic"
    output = tmp_path / "cli-output"
    applied = CliRunner().invoke(app, ["apply", str(world), str(plan_path), "--output", str(output)])
    assert applied.exit_code == 0
    assert inspect_data(output, pointer="/blocks/minecraft:overworld/0/-40/0")["block"] == "minecraft:diamond_block"


def test_mcp_file_tools_accept_world_directory(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("EAFS_MCP_ROOTS", str(tmp_path))
    from sfe_files.mcp_server import file_apply, file_contract, file_inspect, file_preview, file_status

    world = _world(tmp_path)
    assert file_contract("minecraft-java-world").structuredContent["kind"] == "directory"
    assert file_status(str(world)).structuredContent["summary"]["version"] == "26.3"
    pointer = "/blocks/minecraft:overworld/0/-40/0"
    assert file_inspect(str(world), pointer=pointer).structuredContent["block"] == "minecraft:stone"
    plan = json.loads(_plan(world, [_step()]))
    preview = file_preview(str(world), plan).structuredContent
    assert preview["changes"][0]["after"] == "minecraft:diamond_block"
    output = tmp_path / "mcp-output"
    applied = file_apply(str(world), plan, str(output)).structuredContent
    assert applied["after_sha256"] == preview["after_sha256"]
    assert file_inspect(str(output), pointer=pointer).structuredContent["block"] == "minecraft:diamond_block"


def test_batch_build_replace_clear_and_paint_over_10000_blocks(tmp_path: Path):
    world = _world(tmp_path, chunk_count=4)
    source_hash = status_data(world)["sha256"]
    steps = [
        {"op": "fill-box", "dimension": "minecraft:overworld",
         "min": [0, -48, 0], "max": [63, -38, 15], "block": "minecraft:white_concrete"},
        {"op": "replace-box", "dimension": "minecraft:overworld",
         "min": [0, -48, 0], "max": [63, -48, 15],
         "from": "minecraft:white_concrete", "to": "minecraft:blue_concrete"},
        {"op": "clear-box", "dimension": "minecraft:overworld",
         "min": [0, -47, 0], "max": [63, -47, 15]},
        {"op": "paint-runs", "dimension": "minecraft:overworld",
         "palette": {"q": "minecraft:quartz_block", "g": "minecraft:glass"},
         "runs": [[0, -46, 0, 64, "q"], [0, -45, 0, 64, "g"]]},
    ]
    plan = _plan(world, steps)
    preview = preview_data(world, plan, limit=8)
    assert preview["steps"] == 4
    assert preview["changed_blocks"] > 10_000
    assert preview["truncated"] is True
    assert len(preview["changes"]) == 8
    output = tmp_path / "batch-output"
    result = apply_data(world, plan, output)
    assert result["after_sha256"] == preview["after_sha256"] == status_data(output)["sha256"]
    assert status_data(world)["sha256"] == source_hash
    for x, y, z, expected in [(0, -48, 0, "minecraft:blue_concrete"),
                               (31, -47, 5, "minecraft:air"),
                               (16, -46, 0, "minecraft:quartz_block"),
                               (48, -45, 0, "minecraft:glass"),
                               (63, -45, 15, "minecraft:white_concrete")]:
        pointer = f"/blocks/minecraft:overworld/{x}/{y}/{z}"
        assert inspect_data(output, pointer=pointer)["block"] == expected


def test_batch_late_conflict_and_oversized_box_do_not_publish(tmp_path: Path):
    world = _world(tmp_path)
    plan = _plan(world, [
        {"op": "clear-box", "dimension": "minecraft:overworld",
         "min": [0, -40, 0], "max": [15, -40, 15]},
        _step(),
    ])
    output = tmp_path / "no-partial"
    with pytest.raises(FileEditError) as conflict:
        apply_data(world, plan, output)
    assert conflict.value.code == "BLOCK_CONFLICT"
    assert not output.exists()
    assert inspect_data(world, pointer="/blocks/minecraft:overworld/0/-40/0")["block"] == "minecraft:stone"
    huge = _plan(world, [{"op": "fill-box", "dimension": "minecraft:overworld",
                          "min": [0, -64, 0], "max": [100, 100, 100],
                          "block": "minecraft:stone"}])
    with pytest.raises(FileEditError) as too_large:
        preview_data(world, huge)
    assert too_large.value.code == "PLAN_TOO_LARGE"


def test_batch_accepts_26_3_string_palette_and_invalidates_derived_cache(tmp_path: Path):
    world = _world(tmp_path)
    region_path = world / "dimensions/minecraft/overworld/region/r.0.0.mca"
    region = bytearray(region_path.read_bytes())
    first = _chunk_record(0, plain_palette=True, derived_cache=True)
    region[8192:12288] = first + b"\0" * (4096 - len(first))
    region_path.write_bytes(region)
    step = {"op": "clear-box", "dimension": "minecraft:overworld",
            "min": [0, -40, 0], "max": [15, -40, 15]}
    output = tmp_path / "plain-output"
    apply_data(world, _plan(world, [step]), output)
    assert inspect_data(output, pointer="/blocks/minecraft:overworld/0/-40/0")["block"] == "minecraft:air"
    edited = (output / "dimensions/minecraft/overworld/region/r.0.0.mca").read_bytes()
    from sfe_files.minecraft_region import _chunk

    _, chunk = _chunk(edited, 0, 0, DATA_VERSION)
    assert "Heightmaps" not in chunk and "isLightOn" not in chunk
    assert "SkyLight" not in chunk["sections"][0]
    assert isinstance(chunk["sections"][0]["block_states"]["palette"][0], tag.String)


@pytest.mark.parametrize("plain_palette", [False, True])
def test_batch_writes_roof_block_states_from_both_palette_forms(tmp_path: Path,
                                                               plain_palette: bool):
    world = _world(tmp_path)
    region_path = world / "dimensions/minecraft/overworld/region/r.0.0.mca"
    if plain_palette:
        region = bytearray(region_path.read_bytes())
        first = _chunk_record(0, plain_palette=True)
        region[8192:12288] = first + b"\0" * (4096 - len(first))
        region_path.write_bytes(region)
    target = "minecraft:deepslate_tile_stairs[facing=north,half=bottom,shape=straight,waterlogged=false]"
    step = {"op": "fill-box", "dimension": "minecraft:overworld",
            "min": [0, -40, 0], "max": [1, -40, 0], "block": target}
    output = tmp_path / f"roof-{plain_palette}"
    preview = preview_data(world, _plan(world, [step]))
    result = apply_data(world, _plan(world, [step]), output)
    assert preview["after_sha256"] == result["after_sha256"]
    assert inspect_data(output, pointer="/blocks/minecraft:overworld/0/-40/0")["block_state"] == target
    edited = (output / "dimensions/minecraft/overworld/region/r.0.0.mca").read_bytes()
    from sfe_files.minecraft_region import _chunk, _entry_state, _unpack

    _, chunk = _chunk(edited, 0, 0, DATA_VERSION)
    states = chunk["sections"][0]["block_states"]
    values = _unpack(states, len(states["palette"]))
    assert _entry_state(states["palette"][values[8 * 256]]) == target
    assert isinstance(states["palette"][0], tag.Compound)


def test_batch_rejects_unlisted_roof_state(tmp_path: Path):
    world = _world(tmp_path)
    step = {"op": "fill-box", "dimension": "minecraft:overworld",
            "min": [0, -40, 0], "max": [0, -40, 0],
            "block": "minecraft:deepslate_tile_stairs[facing=up,half=bottom,shape=straight,waterlogged=false]"}
    with pytest.raises(FileEditError) as rejected:
        preview_data(world, _plan(world, [step]))
    assert rejected.value.code == "UNSUPPORTED_BLOCK"


def test_replace_box_can_filter_an_exact_roof_state(tmp_path: Path):
    world = _world(tmp_path)
    roof = "minecraft:deepslate_tile_stairs[facing=north,half=bottom,shape=straight,waterlogged=false]"
    step = {"op": "fill-box", "dimension": "minecraft:overworld",
            "min": [0, -40, 0], "max": [1, -40, 0], "block": roof}
    replace = {"op": "replace-box", "dimension": "minecraft:overworld",
               "min": [0, -40, 0], "max": [0, -40, 0],
               "from": roof, "to": "minecraft:deepslate_tiles"}
    output = tmp_path / "roof-filter"
    apply_data(world, _plan(world, [step, replace]), output)
    assert inspect_data(output, pointer="/blocks/minecraft:overworld/0/-40/0")["block_state"] == "minecraft:deepslate_tiles"
    assert inspect_data(output, pointer="/blocks/minecraft:overworld/1/-40/0")["block_state"] == roof
