"""Bounded Minecraft batch step parsing and expansion."""

from __future__ import annotations

from .minecraft_region import BUILD_BLOCKS, SAFE_BLOCKS, BlockEdit
from .plan_ops import FileEditError

OPS = frozenset({"replace-block", "fill-box", "clear-box", "replace-box", "paint-runs"})
MAX_BLOCK_EDITS = 500_000


def _location(pointer: str) -> tuple[int, int, int]:
    parts = pointer.split("/")
    if len(parts) != 6 or parts[:3] != ["", "blocks", "minecraft:overworld"]:
        raise FileEditError("INVALID_POINTER", "Use /blocks/minecraft:overworld/<x>/<y>/<z>")
    try:
        coordinates = tuple(int(part) for part in parts[3:])
    except ValueError as exc:
        raise FileEditError("INVALID_POINTER", "Block coordinates must be integers") from exc
    if any(str(value) != part for value, part in zip(coordinates, parts[3:])):
        raise FileEditError("INVALID_POINTER", "Block coordinates must use canonical integers")
    x, y, z = coordinates
    if abs(x) > 29_999_984 or abs(z) > 29_999_984 or not -64 <= y <= 319:
        raise FileEditError("UNSUPPORTED_COORDINATE", "Coordinate is outside the demo's Overworld bounds")
    return x, y, z


def _step_coordinates(step: dict) -> tuple[int, int, int]:
    if set(step) != {"op", "dimension", "x", "y", "z", "from", "to"}:
        raise FileEditError("INVALID_STEP", "replace-block requires dimension, x, y, z, from and to")
    if step["op"] != "replace-block" or step["dimension"] != "minecraft:overworld":
        raise FileEditError("INVALID_STEP", "Only Overworld replace-block is supported")
    if any(type(step[axis]) is not int for axis in ("x", "y", "z")):
        raise FileEditError("INVALID_STEP", "Block coordinates must be integers")
    x, y, z = step["x"], step["y"], step["z"]
    _location(f"/blocks/minecraft:overworld/{x}/{y}/{z}")
    if type(step["from"]) is not str or type(step["to"]) is not str:
        raise FileEditError("INVALID_STEP", "Block names must be strings")
    return x, y, z


def _box(step: dict) -> tuple[range, range, range, int]:
    corners = []
    for key in ("min", "max"):
        value = step.get(key)
        if not isinstance(value, list) or len(value) != 3 or any(type(v) is not int for v in value):
            raise FileEditError("INVALID_STEP", "Box min and max must each be three integers")
        _location(f"/blocks/minecraft:overworld/{value[0]}/{value[1]}/{value[2]}")
        corners.append(value)
    low, high = corners
    if any(a > b for a, b in zip(low, high)):
        raise FileEditError("INVALID_STEP", "Box min must not exceed max")
    size = (high[0] - low[0] + 1) * (high[1] - low[1] + 1) * (high[2] - low[2] + 1)
    return (range(low[0], high[0] + 1), range(low[1], high[1] + 1),
            range(low[2], high[2] + 1), size)


def _build_block(value: object) -> str:
    if type(value) is not str or value not in BUILD_BLOCKS:
        raise FileEditError("UNSUPPORTED_BLOCK", "Block is outside the Minecraft build palette")
    return value


def _step_edits(step: dict) -> list[BlockEdit]:
    op = step.get("op")
    if type(op) is not str or op not in OPS or step.get("dimension") != "minecraft:overworld":
        raise FileEditError("INVALID_STEP", "Unknown Minecraft operation or dimension")
    if op == "replace-block":
        return [_single_edit(step)]
    if op == "paint-runs":
        return _paint_edits(step)
    return _box_edits(step, op)


def _single_edit(step: dict) -> BlockEdit:
    x, y, z = _step_coordinates(step)
    if step["from"] not in SAFE_BLOCKS or step["to"] not in SAFE_BLOCKS or step["from"] == step["to"]:
        raise FileEditError("UNSUPPORTED_BLOCK", "Use two distinct supported plain full-cube blocks")
    return BlockEdit(x, y, z, step["to"], step["from"], True)


def _paint_edits(step: dict) -> list[BlockEdit]:
    if set(step) != {"op", "dimension", "palette", "runs"}:
        raise FileEditError("INVALID_STEP", "paint-runs requires palette and runs")
    palette, runs = step["palette"], step["runs"]
    if not isinstance(palette, dict) or not palette or not isinstance(runs, list) or not runs:
        raise FileEditError("INVALID_STEP", "paint-runs requires nonempty palette and runs")
    if any(type(key) is not str or not key for key in palette):
        raise FileEditError("INVALID_STEP", "Palette keys must be nonempty strings")
    colors = {key: _build_block(value) for key, value in palette.items()}
    edits: list[BlockEdit] = []
    for run in runs:
        x, y, z, length, key = _checked_run(run, colors)
        if len(edits) + length > MAX_BLOCK_EDITS:
            raise FileEditError("PLAN_TOO_LARGE", "Plan exceeds 500000 block positions")
        edits.extend(BlockEdit(x + dx, y, z, colors[key]) for dx in range(length))
    return edits


def _checked_run(run: object, colors: dict[str, str]) -> tuple[int, int, int, int, str]:
    if (not isinstance(run, list) or len(run) != 5
            or any(type(value) is not int for value in run[:4])
            or type(run[4]) is not str or run[4] not in colors or run[3] < 1):
        raise FileEditError("INVALID_STEP", "Run must be [x,y,z,length,palette_key]")
    x, y, z, length, key = run
    _location(f"/blocks/minecraft:overworld/{x}/{y}/{z}")
    _location(f"/blocks/minecraft:overworld/{x + length - 1}/{y}/{z}")
    return x, y, z, length, key


def _box_edits(step: dict, op: str) -> list[BlockEdit]:
    fields = {"op", "dimension", "min", "max"}
    if op == "fill-box":
        fields.add("block")
    elif op == "replace-box":
        fields.update(("from", "to"))
    if set(step) != fields:
        raise FileEditError("INVALID_STEP", f"{op} has missing or extra fields")
    xs, ys, zs, size = _box(step)
    if size > MAX_BLOCK_EDITS:
        raise FileEditError("PLAN_TOO_LARGE", "Plan exceeds 500000 block positions")
    target = ("minecraft:air" if op == "clear-box" else
              _build_block(step["block"] if op == "fill-box" else step["to"]))
    expected = _build_block(step["from"]) if op == "replace-box" else None
    return [BlockEdit(x, y, z, target, expected)
            for z in zs for y in ys for x in xs]
