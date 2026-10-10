"""Generate a data-only EAFS plan for a 300 by 300 Chinese mansion."""

from __future__ import annotations

import argparse
import html
import json
from dataclasses import dataclass, field
from pathlib import Path

from sfe_files.minecraft_region import _chunk
from sfe_files.minecraft_world import status_data

DIMENSION = "minecraft:overworld"
SIZE = 300
ORIGIN = (-560, -240)
FLOOR = 63
AIR = "minecraft:air"
ROOF = "minecraft:deepslate_tiles"
LEAVES = "minecraft:oak_leaves[distance=7,persistent=true,waterlogged=false]"


@dataclass
class MansionPlan:
    x0: int
    z0: int
    floor: int
    steps: list[dict] = field(default_factory=list)
    labels: list[tuple[str, int, int, int, int, str]] = field(default_factory=list)
    positions: int = 0

    def box(self, x1: int, y1: int, z1: int, x2: int, y2: int, z2: int,
            block: str) -> None:
        if not (0 <= x1 <= x2 < SIZE and 0 <= z1 <= z2 < SIZE and -64 <= y1 <= y2 <= 319):
            raise ValueError("Mansion geometry exceeds the 300×300 footprint or build height")
        self.steps.append({"op": "fill-box", "dimension": DIMENSION,
                           "min": [self.x0 + x1, y1, self.z0 + z1],
                           "max": [self.x0 + x2, y2, self.z0 + z2], "block": block})
        self.positions += (x2 - x1 + 1) * (y2 - y1 + 1) * (z2 - z1 + 1)

    def clear(self, x1: int, y1: int, z1: int, x2: int, y2: int, z2: int) -> None:
        self.box(x1, y1, z1, x2, y2, z2, AIR)

    def mark(self, name: str, x1: int, z1: int, x2: int, z2: int,
             color: str) -> None:
        self.labels.append((name, x1, z1, x2, z2, color))


def _surface_heights(world: Path, x0: int, z0: int) -> list[list[int]]:
    regions: dict[Path, bytes] = {}
    chunks: dict[tuple[int, int], list[int]] = {}
    result: list[list[int]] = []
    for z in range(z0, z0 + SIZE):
        row = []
        for x in range(x0, x0 + SIZE):
            cx, cz = x // 16, z // 16
            key = (cx, cz)
            if key not in chunks:
                relative = (Path("dimensions/minecraft/overworld/region")
                            / f"r.{cx // 32}.{cz // 32}.mca")
                if relative not in regions:
                    regions[relative] = (world / relative).read_bytes()
                _, document = _chunk(regions[relative], cx, cz, 5023)
                if str(document.get("Status")) != "minecraft:full":
                    raise ValueError(f"Chunk {cx},{cz} is not fully generated")
                words = document["Heightmaps"]["WORLD_SURFACE"]
                if len(words) != 37:
                    raise ValueError(f"Chunk {cx},{cz} has an unexpected heightmap")
                chunks[key] = [((int(words[i // 7]) & ((1 << 64) - 1))
                                >> ((i % 7) * 9)) & 511 for i in range(256)]
            row.append(chunks[key][(z % 16) * 16 + x % 16] - 64)
        result.append(row)
    return result


def _flatten(plan: MansionPlan, heights: list[list[int]]) -> None:
    runs = []
    highest = max(max(row) for row in heights)
    for y in range(plan.floor, highest):
        for z, row in enumerate(heights):
            x = 0
            while x < SIZE:
                if row[x] <= y:
                    x += 1
                    continue
                start = x
                while x < SIZE and row[x] > y:
                    x += 1
                runs.append([plan.x0 + start, y, plan.z0 + z, x - start, "air"])
                plan.positions += x - start
    if runs:
        plan.steps.append({"op": "paint-runs", "dimension": DIMENSION,
                           "palette": {"air": AIR}, "runs": runs})


def _path(plan: MansionPlan, x1: int, z1: int, x2: int, z2: int) -> None:
    plan.box(x1, plan.floor, z1, x2, plan.floor, z2, "minecraft:stone_bricks")
    plan.box(x1, plan.floor + 1, z1, x2, plan.floor + 1, z2,
             "minecraft:smooth_quartz")


def _roof_stair(facing: str) -> str:
    return ("minecraft:deepslate_tile_stairs"
            f"[facing={facing},half=bottom,shape=straight,waterlogged=false]")


def _hall(plan: MansionPlan, name: str, x1: int, z1: int, x2: int, z2: int,
          *, grand: bool = False) -> None:
    base = plan.floor
    wall_top = base + (10 if grand else 8)
    plan.box(x1, base, z1, x2, base, z2, "minecraft:stone_bricks")
    plan.box(x1 + 1, base + 1, z1 + 1, x2 - 1, base + 1, z2 - 1,
             "minecraft:dark_oak_planks")
    for z in (z1, z2):
        plan.box(x1, base + 1, z, x2, wall_top, z, "minecraft:white_concrete")
        plan.box(x1, base + 3, z, x2, base + 5, z, "minecraft:blue_stained_glass")
    for x in (x1, x2):
        plan.box(x, base + 1, z1, x, wall_top, z2, "minecraft:white_concrete")
    for x in range(x1, x2 + 1, 8):
        for z in (z1, z2):
            plan.box(x, base + 1, z - 1, x, wall_top + 1, z + 1,
                     "minecraft:red_concrete")
            plan.box(x, wall_top + 1, z - 1, x, wall_top + 1, z + 1,
                     "minecraft:gold_block")
    for z in range(z1 + 8, z2, 8):
        for x in (x1, x2):
            plan.box(x - 1, base + 1, z, x + 1, wall_top + 1, z,
                     "minecraft:red_concrete")
    mid = (x1 + x2) // 2
    plan.clear(mid - 3, base + 1, z2, mid + 3, base + 5, z2)
    plan.box(mid - 4, base + 6, z2, mid + 4, base + 6, z2,
             "minecraft:gold_block")
    # Swept eaves: stepped tile rows rise to a decorated longitudinal ridge.
    north, south = z1 - 3, z2 + 3
    rise = min(9, (south - north) // 4)
    for z in range(north, south + 1):
        offset = min(z - north, south - z)
        y = wall_top + 2 + min(rise, (offset + 1) // 2)
        block = (_roof_stair("north") if z == north else
                 _roof_stair("south") if z == south else ROOF)
        plan.box(x1 - 3, y, z, x2 + 3, y, z, block)
        if z in (north, south):
            plan.box(x1 - 3, y + 1, z, x1 + 1, y + 1, z,
                     "minecraft:deepslate_tile_slab[type=bottom,waterlogged=false]")
            plan.box(x2 - 1, y + 1, z, x2 + 3, y + 1, z,
                     "minecraft:deepslate_tile_slab[type=bottom,waterlogged=false]")
    ridge_y = wall_top + 3 + rise
    plan.box(x1 - 3, ridge_y, (north + south) // 2,
             x2 + 3, ridge_y, (north + south) // 2, "minecraft:gold_block")
    plan.mark(name, x1 - 3, north, x2 + 3, south, "#4c5864")


def _tree(plan: MansionPlan, x: int, z: int) -> None:
    y = plan.floor + 1
    plan.box(x, y, z, x, y + 5, z, "minecraft:dark_oak_log")
    plan.box(x - 2, y + 4, z - 2, x + 2, y + 5, z + 2, LEAVES)
    plan.box(x - 1, y + 6, z - 1, x + 1, y + 6, z + 1, LEAVES)


def _garden(plan: MansionPlan, x1: int, z1: int, x2: int, z2: int,
            name: str) -> None:
    y = plan.floor
    plan.box(x1, y, z1, x2, y, z2, "minecraft:moss_block")
    plan.box(x1, y + 1, z1, x2, y + 1, z1, "minecraft:stone_bricks")
    plan.box(x1, y + 1, z2, x2, y + 1, z2, "minecraft:stone_bricks")
    plan.box(x1, y + 1, z1, x1, y + 1, z2, "minecraft:stone_bricks")
    plan.box(x2, y + 1, z1, x2, y + 1, z2, "minecraft:stone_bricks")
    for x in range(x1 + 7, x2 - 4, 13):
        for z in range(z1 + 7, z2 - 4, 13):
            _tree(plan, x, z)
    plan.mark(name, x1, z1, x2, z2, "#58774f")


def _pond(plan: MansionPlan, x1: int, z1: int, x2: int, z2: int) -> None:
    y = plan.floor
    plan.box(x1, y, z1, x2, y, z2, "minecraft:blue_concrete")
    plan.box(x1 + 1, y + 1, z1 + 1, x2 - 1, y + 1, z2 - 1,
             "minecraft:blue_stained_glass")
    for x in (x1, x2):
        plan.box(x, y + 1, z1, x, y + 1, z2, "minecraft:stone_bricks")
    for z in (z1, z2):
        plan.box(x1, y + 1, z, x2, y + 1, z, "minecraft:stone_bricks")
    plan.mark("池", x1, z1, x2, z2, "#4d91ad")


def _compose(plan: MansionPlan) -> None:
    y = plan.floor
    plan.box(0, y, 0, 299, y, 299, "minecraft:grass_block")
    # Enclosure, corner towers and a southern ceremonial entrance.
    for x1, z1, x2, z2 in ((2, 2, 5, 297), (294, 2, 297, 297),
                           (2, 2, 297, 5), (2, 294, 297, 297)):
        plan.box(x1, y + 1, z1, x2, y + 7, z2, "minecraft:white_concrete")
        plan.box(x1, y + 8, z1, x2, y + 8, z2, ROOF)
    for x in (7, 292):
        for z in (7, 292):
            plan.box(x - 3, y + 1, z - 3, x + 3, y + 12, z + 3,
                     "minecraft:red_concrete")
            plan.box(x - 5, y + 13, z - 5, x + 5, y + 13, z + 5, ROOF)
    plan.clear(139, y + 1, 294, 160, y + 8, 299)
    plan.mark("围墙与南门", 2, 2, 297, 297, "#a85d58")

    # Main north-south processional axis and four stone court platforms.
    _path(plan, 143, 8, 156, 292)
    for x1, z1, x2, z2 in ((91, 130, 208, 161), (99, 210, 200, 251),
                           (100, 65, 199, 73), (115, 24, 184, 31)):
        _path(plan, x1, z1, x2, z2)
    for x1, z1, x2, z2 in ((8, 8, 291, 11), (8, 288, 291, 291),
                           (8, 8, 11, 291), (288, 8, 291, 291)):
        _path(plan, x1, z1, x2, z2)

    _hall(plan, "正门楼", 113, 257, 186, 285)
    _hall(plan, "仪门与前厅", 101, 171, 198, 205)
    _hall(plan, "正殿", 88, 77, 211, 122, grand=True)
    _hall(plan, "后寝", 108, 25, 191, 59)
    for name, x1, z1, x2, z2 in (
        ("西厢", 24, 160, 80, 216), ("东厢", 219, 160, 275, 216),
        ("西书房", 24, 73, 74, 123), ("东书房", 225, 73, 275, 123),
        ("西客舍", 25, 226, 81, 264), ("东客舍", 218, 226, 274, 264),
        ("西偏院", 27, 25, 75, 60), ("东偏院", 224, 25, 272, 60),
    ):
        _hall(plan, name, x1, z1, x2, z2)

    _garden(plan, 14, 127, 76, 153, "西竹园")
    _garden(plan, 223, 127, 285, 153, "东竹园")
    _garden(plan, 83, 16, 105, 61, "西后园")
    _garden(plan, 194, 16, 216, 61, "东后园")
    _pond(plan, 25, 274, 71, 287)
    _pond(plan, 228, 274, 274, 287)
    for x in (18, 282):
        for z in (18, 65, 125, 220, 282):
            plan.box(x, y + 1, z, x, y + 3, z, "minecraft:stone_bricks")
            plan.box(x, y + 4, z, x, y + 4, z, "minecraft:glowstone")
            plan.box(x - 1, y + 5, z - 1, x + 1, y + 5, z + 1, ROOF)


def _svg(plan: MansionPlan) -> str:
    marks = []
    for name, x1, z1, x2, z2, color in plan.labels:
        marks.append(f'<rect x="{x1}" y="{z1}" width="{x2-x1+1}" height="{z2-z1+1}" '
                     f'fill="{color}" stroke="#e8d7a6" stroke-width="0.6"/>')
        if name != "围墙与南门":
            marks.append(f'<text x="{(x1+x2)/2}" y="{(z1+z2)/2}" '
                         f'text-anchor="middle" dominant-baseline="middle" font-size="5" '
                         f'fill="white">{html.escape(name)}</text>')
    return ("<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"-8 -18 316 333\" "
            "width=\"948\" height=\"999\"><rect x=\"0\" y=\"0\" width=\"300\" "
            "height=\"300\" fill=\"#79986a\"/><path d=\"M150 0V300\" "
            "stroke=\"#d7ccae\" stroke-width=\"14\"/>"
            + "".join(marks)
            + '<text x="150" y="-7" text-anchor="middle" font-size="8">北 · 300 × 300 中式宅邸</text>'
            + '<text x="150" y="308" text-anchor="middle" font-size="7">南门 · Minecraft Java 26.3</text></svg>')


def generate(world: Path, plan_path: Path, svg_path: Path) -> MansionPlan:
    info = status_data(world)
    if info["summary"]["data_version"] != 5023:
        raise ValueError("This mansion plan requires Java 26.3 DataVersion 5023")
    plan = MansionPlan(*ORIGIN, FLOOR)
    heights = _surface_heights(world, plan.x0, plan.z0)
    _flatten(plan, heights)
    _compose(plan)
    if plan.positions > 500_000:
        raise ValueError(f"Mansion plan needs {plan.positions} positions; EAFS limit is 500000")
    payload = {"protocol": "sfe/0.1", "format": "minecraft-java-world",
               "base": {"sha256": info["sha256"]}, "steps": plan.steps}
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    svg_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                         encoding="utf-8")
    svg_path.write_text(_svg(plan), encoding="utf-8")
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("world", type=Path)
    parser.add_argument("--plan", type=Path, default=Path("build/ChineseMansion_300x300.plan.json"))
    parser.add_argument("--svg", type=Path, default=Path("build/ChineseMansion_300x300.svg"))
    args = parser.parse_args()
    plan = generate(args.world, args.plan, args.svg)
    print(json.dumps({"plan": str(args.plan), "svg": str(args.svg),
                      "origin": [plan.x0, plan.z0], "size": [SIZE, SIZE],
                      "floor_y": plan.floor, "steps": len(plan.steps),
                      "target_positions": plan.positions}, ensure_ascii=False))


if __name__ == "__main__":
    main()
