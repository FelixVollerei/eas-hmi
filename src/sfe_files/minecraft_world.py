"""EAFS directory profile for a bounded Minecraft Java 26.3 world edit."""

from __future__ import annotations

import gzip
import hashlib
import io
import os
import shutil
import struct
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path

import nbtlib

from .core import PROTOCOL
from .minecraft_batch_plan import MAX_BLOCK_EDITS, _location, _step_edits
from .minecraft_batch_plan import OPS as BATCH_OPS
from .minecraft_region import BUILD_BLOCKS, SAFE_BLOCKS, BlockEdit, apply_edits, inspect_block_state
from .plan_ops import FileEditError, _json

FORMAT = "minecraft-java-world"
VERSION = "26.3"
OPS = BATCH_OPS  # Public contract facade consumed by service.contract_data.
CONTRACT = {
    "addressing": "A world directory; /blocks/minecraft:overworld/<x>/<y>/<z> selects one generated block.",
    "operations": {
        "replace-block": {
            "example": {"op": "replace-block", "dimension": "minecraft:overworld",
                        "x": 0, "y": -40, "z": 0,
                        "from": "minecraft:stone", "to": "minecraft:diamond_block"},
            "description": "Replace one existing plain full-cube block with another supported block.",
        },
        "fill-box": {"example": {"op": "fill-box", "dimension": "minecraft:overworld",
                                  "min": [0, 64, 0], "max": [31, 67, 31],
                                  "block": "minecraft:quartz_block"},
                     "description": "Fill an inclusive box, replacing its previous blocks."},
        "clear-box": {"example": {"op": "clear-box", "dimension": "minecraft:overworld",
                                   "min": [0, 64, 0], "max": [31, 67, 31]},
                      "description": "Remove blocks in an inclusive box (set to air)."},
        "replace-box": {"example": {"op": "replace-box", "dimension": "minecraft:overworld",
                                     "min": [0, 64, 0], "max": [31, 67, 31],
                                     "from": "minecraft:stone", "to": "minecraft:stone_bricks"},
                        "description": "Within an inclusive box, replace only blocks matching from."},
        "paint-runs": {"example": {"op": "paint-runs", "dimension": "minecraft:overworld",
                                    "palette": {"w": "minecraft:white_concrete", "g": "minecraft:glass"},
                                    "runs": [[0, 70, 0, 16, "w"], [0, 71, 0, 16, "g"]]},
                       "description": "Paint ordered +X runs from a compact block palette."},
    },
    "limitations": [
        "Minecraft Java 26.3 world directories only; worlds must be closed while editing.",
        "Only generated Overworld chunks with inline zlib region records are supported.",
        "replace-block keeps its six-block exact-match allowlist; batch targets use build_blocks.",
        "Batch edits can replace existing block properties, but cannot target block entities.",
        "Derived chunk heightmap and lighting tags are removed; in-game regeneration needs verification.",
        "At most 500000 block positions per plan; no new chunks or sections are created.",
        "Entity data and biome changes are unsupported.",
        "base.sha256 is a deterministic hash of all relative paths and file bytes in the world directory.",
        "apply writes a new world directory; it does not edit the source world.",
    ],
    "safe_blocks": sorted(SAFE_BLOCKS),
    "build_blocks": sorted(BUILD_BLOCKS),
}


@dataclass(frozen=True)
class WorldInfo:
    version: str
    data_version: int


@dataclass(frozen=True)
class PreparedWorld:
    before_sha256: str
    after_sha256: str
    changed_files: dict[Path, bytes]
    changes: list[dict]
    change_count: int
    step_count: int


def _world_info(world: Path) -> WorldInfo:
    if not world.is_dir():
        raise FileEditError("INVALID_WORLD", "Minecraft world path must be a directory")
    level = world / "level.dat"
    if not level.is_file():
        raise FileEditError("INVALID_WORLD", "World is missing level.dat")
    try:
        document = nbtlib.File.parse(io.BytesIO(gzip.decompress(level.read_bytes())))
        data = document["Data"]
        version = str(data["Version"]["Name"])
        data_version = int(data["DataVersion"])
    except (EOFError, gzip.BadGzipFile, KeyError, TypeError, ValueError,
            UnicodeError, struct.error, zlib.error) as exc:
        raise FileEditError("INVALID_WORLD", "level.dat could not be decoded") from exc
    if version != VERSION:
        raise FileEditError("UNSUPPORTED_VERSION", f"Demo supports Java {VERSION}; found {version}")
    if data_version <= 0:
        raise FileEditError("INVALID_WORLD", "World DataVersion must be positive")
    return WorldInfo(version, data_version)


def _fingerprint(world: Path, overrides: dict[Path, bytes] | None = None) -> tuple[str, int]:
    digest = hashlib.sha256()
    files = sorted(path for path in world.rglob("*") if path.is_file() or path.is_symlink())
    if not files:
        raise FileEditError("INVALID_WORLD", "World directory is empty")
    for path in files:
        if path.is_symlink():
            raise FileEditError("UNSUPPORTED_WORLD", "World symlinks are outside this demo")
        relative = path.relative_to(world)
        name = relative.as_posix().encode("utf-8")
        digest.update(len(name).to_bytes(4, "big"))
        digest.update(name)
        if overrides and relative in overrides:
            payload = overrides[relative]
            digest.update(len(payload).to_bytes(8, "big"))
            digest.update(payload)
        else:
            digest.update(path.stat().st_size.to_bytes(8, "big"))
            with path.open("rb") as stream:
                for payload in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(payload)
    return digest.hexdigest(), len(files)


def _region_path(x: int, z: int) -> Path:
    return (Path("dimensions") / "minecraft" / "overworld" / "region"
            / f"r.{x // 512}.{z // 512}.mca")


def _region_bytes(world: Path, relative: Path) -> bytes:
    path = world / relative
    if not path.is_file():
        raise FileEditError("CHUNK_NOT_FOUND", f"Region file is missing: {relative.as_posix()}")
    return path.read_bytes()


def status_data(world: Path) -> dict:
    info = _world_info(world)
    fingerprint, count = _fingerprint(world)
    return {"ok": True, "protocol": PROTOCOL, "format": FORMAT, "path": str(world.resolve()),
            "sha256": fingerprint, "summary": {"version": info.version,
                                              "data_version": info.data_version, "file_count": count}}


def inspect_data(world: Path, pointer: str) -> dict:
    info = _world_info(world)
    fingerprint, _ = _fingerprint(world)
    if not pointer:
        return {"ok": True, "protocol": PROTOCOL, "format": FORMAT,
                "sha256": fingerprint, "version": info.version, "data_version": info.data_version}
    x, y, z = _location(pointer)
    block_state = inspect_block_state(_region_bytes(world, _region_path(x, z)),
                                      x, y, z, info.data_version)
    return {"ok": True, "protocol": PROTOCOL, "format": FORMAT, "sha256": fingerprint,
            "pointer": pointer, "block": block_state.split("[", 1)[0],
            "block_state": block_state}


def _validated_plan(world: Path, plan_data: bytes) -> tuple[dict, str]:
    plan = _json(plan_data)
    if not isinstance(plan, dict) or set(plan) != {"protocol", "format", "base", "steps"}:
        raise FileEditError("INVALID_PLAN", "Plan requires protocol, format, base and steps")
    if plan["protocol"] != PROTOCOL or plan["format"] != FORMAT:
        raise FileEditError("INVALID_PLAN", "Plan protocol or format does not match the world")
    base = plan["base"]
    if not isinstance(base, dict) or set(base) != {"sha256"} or type(base["sha256"]) is not str:
        raise FileEditError("INVALID_PLAN", "Plan base requires sha256")
    if not isinstance(plan["steps"], list) or not plan["steps"]:
        raise FileEditError("INVALID_PLAN", "Plan steps must be a nonempty array")
    before, _ = _fingerprint(world)
    if base["sha256"] != before:
        raise FileEditError("BASE_CONFLICT", "World bytes no longer match the plan base")
    return plan, before


def _group_edits(steps: list[dict]) -> dict[Path, list[BlockEdit]]:
    by_region: dict[Path, list[BlockEdit]] = {}
    total_edits = 0
    for step in steps:
        if not isinstance(step, dict):
            raise FileEditError("INVALID_STEP", "Each step must be an object")
        edits = _step_edits(step)
        total_edits += len(edits)
        if total_edits > MAX_BLOCK_EDITS:
            raise FileEditError("PLAN_TOO_LARGE", "Plan exceeds 500000 block positions")
        for edit in edits:
            by_region.setdefault(_region_path(edit.x, edit.z), []).append(edit)
    return by_region


def _prepare(world: Path, plan_data: bytes) -> PreparedWorld:
    info = _world_info(world)
    plan, before = _validated_plan(world, plan_data)
    by_region = _group_edits(plan["steps"])
    changed_files: dict[Path, bytes] = {}
    changes: list[dict] = []
    change_count = 0
    for relative, edits in by_region.items():
        original = _region_bytes(world, relative)
        updated, region_changes = apply_edits(original, edits, info.data_version)
        if region_changes:
            changed_files[relative] = updated
        change_count += len(region_changes)
        changes.extend({"dimension": "minecraft:overworld", "x": item.x, "y": item.y,
                        "z": item.z, "before": item.before, "after": item.after}
                       for item in region_changes[:max(0, 10000 - len(changes))])
    if _fingerprint(world)[0] != before:
        raise FileEditError("BASE_CONFLICT", "World changed while preparing the plan")
    after, _ = _fingerprint(world, changed_files)
    return PreparedWorld(before, after, changed_files, changes, change_count, len(plan["steps"]))


def preview_data(world: Path, plan_data: bytes, *, limit: int = 200) -> dict:
    if not 1 <= limit <= 10000:
        raise FileEditError("INVALID_LIMIT", "preview limit must be 1..10000")
    candidate = _prepare(world, plan_data)
    lines = [f"{item['dimension']} ({item['x']},{item['y']},{item['z']}): "
             f"{item['before']} -> {item['after']}\n" for item in candidate.changes[:limit]]
    return {"ok": True, "protocol": PROTOCOL, "format": FORMAT,
            "before_sha256": candidate.before_sha256, "after_sha256": candidate.after_sha256,
            "steps": candidate.step_count, "changed_blocks": candidate.change_count,
            "diff_kind": "semantic", "diff": "".join(lines),
            "truncated": candidate.change_count > limit, "changes": candidate.changes[:limit],
            "changed_files": [path.as_posix() for path in candidate.changed_files], "committed": False}


def apply_data(world: Path, plan_data: bytes, output: Path) -> dict:
    source = world.resolve()
    destination = output.resolve()
    if destination == source or destination.is_relative_to(source) or destination.exists():
        raise FileEditError("OUTPUT_EXISTS", "Output must be a new directory outside the input world")
    candidate = _prepare(world, plan_data)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output.name}.", dir=output.parent) as staging:
        stage = Path(staging)
        shutil.copytree(world, stage, dirs_exist_ok=True)
        if _fingerprint(stage)[0] != candidate.before_sha256:
            raise FileEditError("BASE_CONFLICT", "World changed while copying the source snapshot")
        for relative, payload in candidate.changed_files.items():
            (stage / relative).write_bytes(payload)
        if _fingerprint(stage)[0] != candidate.after_sha256:
            raise FileEditError("INVALID_OUTPUT", "Staged world fingerprint differs from preview")
        if _fingerprint(world)[0] != candidate.before_sha256:
            raise FileEditError("BASE_CONFLICT", "Source world changed before publication")
        if destination.exists():
            raise FileEditError("OUTPUT_EXISTS", "Output directory already exists")
        try:
            os.rename(stage, destination)
        except FileExistsError as exc:
            raise FileEditError("OUTPUT_EXISTS", "Output directory already exists") from exc
    return {"ok": True, "protocol": PROTOCOL, "format": FORMAT,
            "before_sha256": candidate.before_sha256, "after_sha256": candidate.after_sha256,
            "steps": candidate.step_count, "changed_blocks": candidate.change_count,
            "changed_files": [p.as_posix() for p in candidate.changed_files],
            "output": str(destination), "committed": True}
