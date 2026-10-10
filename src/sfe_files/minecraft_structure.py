"""Versioned, world-independent architectural scenes for Minecraft builds."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from .minecraft_batch_plan import MAX_BLOCK_EDITS
from .minecraft_region import BUILD_BLOCKS
from .minecraft_world import status_data

FORMAT = "eafs-minecraft-structure/0.1"
ROLES = frozenset({"site", "building", "courtyard", "terrain", "foundation", "floor",
                   "wall", "column", "beam", "roof", "opening", "stair", "path",
                   "landscape", "water", "lighting", "decoration"})
_ID = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")


@dataclass(frozen=True)
class Element:
    id: str
    parent: str | None
    role: str
    block: str | None
    boxes: tuple[tuple[tuple[int, int, int], tuple[int, int, int]], ...]
    overrides: tuple[str, ...]


@dataclass(frozen=True)
class Scene:
    origin: tuple[int, int]
    size: tuple[int, int]
    elements: tuple[Element, ...]


@dataclass(frozen=True)
class ResolvedScene:
    scene: Scene
    # Coordinates are local X/Z and absolute Y. Values index Scene.elements.
    voxels: Mapping[tuple[int, int, int], int]
    overlap_count: int


def _integer_vector(value: object, length: int, label: str) -> tuple[int, ...]:
    if not isinstance(value, list) or len(value) != length or any(type(v) is not int for v in value):
        raise ValueError(f"{label} must contain {length} integers")
    return tuple(value)


def _parse_boxes(raw: object, eid: str, origin: tuple[int, int],
                 size: tuple[int, int]) -> tuple[tuple[tuple[int, int, int], tuple[int, int, int]], ...]:
    if not isinstance(raw, list):
        raise TypeError(f"Element {eid} boxes must be an array")
    boxes = []
    for pair in raw:
        if not isinstance(pair, list) or len(pair) != 2:
            raise ValueError(f"Element {eid} box must be [min,max]")
        low = _integer_vector(pair[0], 3, f"{eid} min")
        high = _integer_vector(pair[1], 3, f"{eid} max")
        if any(a > b for a, b in zip(low, high)) or not (0 <= low[0] <= high[0] < size[0]
                and 0 <= low[2] <= high[2] < size[1] and -64 <= low[1] <= high[1] <= 319):
            raise ValueError(f"Element {eid} box exceeds scene or build height")
        if any(abs(origin[axis] + point[axis * 2]) > 29_999_984
               for axis in (0, 1) for point in (low, high)):
            raise ValueError(f"Element {eid} exceeds Minecraft coordinate bounds")
        boxes.append((low, high))
    return tuple(boxes)


def _parse_identity(raw: dict, seen: set[str]) -> tuple[str, str | None, str]:
    eid, parent, role = (raw[key] for key in ("id", "parent", "role"))
    if type(eid) is not str or not _ID.fullmatch(eid) or eid in seen:
        raise ValueError(f"Invalid or duplicate element id: {eid}")
    if parent is not None and (type(parent) is not str or parent not in seen):
        raise ValueError(f"Element {eid} parent must be an earlier element")
    if type(role) is not str or role not in ROLES:
        raise ValueError(f"Element {eid} has unsupported role")
    return eid, parent, role


def _parse_overrides(raw: object, eid: str, seen: set[str]) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise TypeError(f"Element {eid} overrides must be an array")
    if any(type(ref) is not str or ref not in seen for ref in raw):
        raise ValueError(f"Element {eid} overrides must name earlier elements")
    if len(set(raw)) != len(raw):
        raise ValueError(f"Element {eid} overrides must name distinct earlier elements")
    return tuple(raw)


def _validate_material(eid: str, role: str, block: object,
                       boxes: tuple, overrides: tuple[str, ...]) -> str | None:
    if block is None:
        if boxes or overrides or role not in {"site", "building", "courtyard"}:
            raise ValueError(f"Element {eid} is an invalid group")
    elif type(block) is not str or block not in BUILD_BLOCKS or not boxes:
        raise ValueError(f"Element {eid} requires a supported block and boxes")
    return block


def _parse_element(raw: object, seen: set[str], origin: tuple[int, int],
                   size: tuple[int, int]) -> Element:
    if not isinstance(raw, dict) or set(raw) != {"id", "parent", "role", "block", "boxes", "overrides"}:
        raise ValueError("Each element requires id, parent, role, block, boxes and overrides")
    eid, parent, role = _parse_identity(raw, seen)
    overrides = _parse_overrides(raw["overrides"], eid, seen)
    boxes = _parse_boxes(raw["boxes"], eid, origin, size)
    block = _validate_material(eid, role, raw["block"], boxes, overrides)
    return Element(eid, parent, role, block, boxes, overrides)


def parse_scene(payload: object) -> Scene:
    if not isinstance(payload, dict) or set(payload) != {"format", "origin", "size", "elements"}:
        raise ValueError("Scene requires exactly format, origin, size and elements")
    if payload["format"] != FORMAT:
        raise ValueError(f"Unsupported scene format: {payload['format']}")
    origin = _integer_vector(payload["origin"], 2, "origin")
    size = _integer_vector(payload["size"], 2, "size")
    if not 1 <= size[0] <= 300 or not 1 <= size[1] <= 300:
        raise ValueError("Scene width and depth must be 1..300")
    if any(abs(v) > 29_999_984 for v in origin):
        raise ValueError("Scene origin exceeds Minecraft coordinate bounds")
    if not isinstance(payload["elements"], list) or not payload["elements"]:
        raise ValueError("Scene elements must be a nonempty array")
    elements: list[Element] = []
    seen: set[str] = set()
    for raw in payload["elements"]:
        element = _parse_element(raw, seen, origin, size)
        elements.append(element)
        seen.add(element.id)
    return Scene(origin, size, tuple(elements))


def load_scene(path: Path) -> Scene:
    return parse_scene(json.loads(path.read_text(encoding="utf-8")))


def _points(low: tuple[int, int, int], high: tuple[int, int, int]) -> Iterator[tuple[int, int, int]]:
    for y in range(low[1], high[1] + 1):
        for z in range(low[2], high[2] + 1):
            for x in range(low[0], high[0] + 1):
                yield x, y, z


def resolve(scene: Scene) -> ResolvedScene:
    voxels: dict[tuple[int, int, int], int] = {}
    overlaps = 0
    for index, element in enumerate(scene.elements):
        used_overrides: set[str] = set()
        for low, high in element.boxes:
            volume = ((high[0] - low[0] + 1) * (high[1] - low[1] + 1)
                      * (high[2] - low[2] + 1))
            if volume > MAX_BLOCK_EDITS:
                raise ValueError(f"Element {element.id} exceeds 500000 positions")
            for point in _points(low, high):
                previous = voxels.get(point)
                if previous == index:
                    raise ValueError(f"Element {element.id} overlaps itself at {point}")
                if previous is not None:
                    prior_id = scene.elements[previous].id
                    if prior_id not in element.overrides:
                        raise ValueError(f"Undeclared overlap at {point}: {prior_id} -> {element.id}")
                    used_overrides.add(prior_id)
                    overlaps += 1
                voxels[point] = index
                if len(voxels) > MAX_BLOCK_EDITS:
                    raise ValueError("Scene exceeds 500000 final block positions")
        if used_overrides != set(element.overrides):
            raise ValueError(f"Element {element.id} has unused overrides: "
                             f"{sorted(set(element.overrides) - used_overrides)}")
    return ResolvedScene(scene, MappingProxyType(voxels), overlaps)


def inspect(resolved: ResolvedScene, x: int, y: int, z: int) -> dict:
    local = (x - resolved.scene.origin[0], y, z - resolved.scene.origin[1])
    index = resolved.voxels.get(local)
    if index is None:
        return {"position": [x, y, z], "element": None}
    element = resolved.scene.elements[index]
    ancestors = []
    by_id = {item.id: item for item in resolved.scene.elements}
    parent = element.parent
    while parent is not None:
        ancestors.append(parent)
        parent = by_id[parent].parent
    return {"position": [x, y, z], "local": list(local), "element": element.id,
            "parent": element.parent, "ancestors": ancestors, "role": element.role,
            "block": element.block}


def statistics(resolved: ResolvedScene) -> dict:
    by_element: Counter[str] = Counter()
    by_role: Counter[str] = Counter()
    by_block: Counter[str] = Counter()
    for index in resolved.voxels.values():
        element = resolved.scene.elements[index]
        by_element[element.id] += 1
        by_role[element.role] += 1
        by_block[element.block] += 1
    return {"size": list(resolved.scene.size), "origin": list(resolved.scene.origin),
            "elements": len(resolved.scene.elements), "final_blocks": len(resolved.voxels),
            "declared_overlaps": resolved.overlap_count,
            "by_element": dict(sorted(by_element.items())),
            "by_role": dict(sorted(by_role.items())),
            "by_block": dict(sorted(by_block.items()))}


def _runs(resolved: ResolvedScene) -> list[list]:
    ordered = sorted(resolved.voxels.items(), key=lambda item: (item[0][1], item[0][2], item[0][0]))
    runs: list[list] = []
    for (x, y, z), index in ordered:
        if runs and runs[-1][1:3] == [y, z] and runs[-1][4] == index and runs[-1][0] + runs[-1][3] == x:
            runs[-1][3] += 1
        else:
            runs.append([x, y, z, 1, index])
    return runs


def compile_plan(resolved: ResolvedScene, world: Path) -> dict:
    info = status_data(world)
    if info["summary"]["data_version"] != 5023:
        raise ValueError("Scene compilation requires Java 26.3 DataVersion 5023")
    if not resolved.voxels:
        raise ValueError("Scene contains no blocks")
    palette = {str(index): element.block for index, element in enumerate(resolved.scene.elements)
               if element.block is not None}
    x0, z0 = resolved.scene.origin
    runs = [[x0 + x, y, z0 + z, length, str(index)]
            for x, y, z, length, index in _runs(resolved)]
    return {"protocol": "sfe/0.1", "format": "minecraft-java-world",
            "base": {"sha256": info["sha256"]},
            "steps": [{"op": "paint-runs", "dimension": "minecraft:overworld",
                       "palette": palette, "runs": runs}]}
