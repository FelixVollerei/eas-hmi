"""Small, version-gated Anvil region operations for the Minecraft demo."""

from __future__ import annotations

import io
import struct
import zlib
from dataclasses import dataclass

import nbtlib
from nbtlib import tag

from .plan_ops import FileEditError

SECTOR = 4096
SAFE_BLOCKS = frozenset({
    "minecraft:stone", "minecraft:deepslate", "minecraft:diamond_block",
    "minecraft:emerald_block", "minecraft:gold_block", "minecraft:iron_block",
})
BUILD_BLOCKS = SAFE_BLOCKS | frozenset({
    "minecraft:air", "minecraft:stone_bricks", "minecraft:polished_deepslate",
    "minecraft:quartz_block", "minecraft:smooth_quartz", "minecraft:glass",
    "minecraft:obsidian", "minecraft:sea_lantern", "minecraft:glowstone",
    "minecraft:grass_block", "minecraft:dark_oak_log", "minecraft:dark_oak_planks",
    "minecraft:deepslate_tiles", "minecraft:gold_block", "minecraft:moss_block",
    "minecraft:bricks", "minecraft:red_terracotta", "minecraft:yellow_terracotta",
    "minecraft:blue_stained_glass", "minecraft:green_terracotta",
    "minecraft:oak_leaves[distance=7,persistent=true,waterlogged=false]",
    *(f"minecraft:deepslate_tile_stairs[facing={facing},half={half},shape=straight,waterlogged=false]"
      for facing in ("north", "south", "east", "west") for half in ("bottom", "top")),
    *(f"minecraft:deepslate_tile_slab[type={kind},waterlogged=false]"
      for kind in ("bottom", "top")),
    *(f"minecraft:{color}_concrete" for color in (
        "white", "orange", "magenta", "light_blue", "yellow", "lime", "pink",
        "gray", "light_gray", "cyan", "purple", "blue", "brown", "green",
        "red", "black",
    )),
})


@dataclass(frozen=True)
class BlockEdit:
    x: int
    y: int
    z: int
    target: str
    expected: str | None = None
    strict: bool = False


@dataclass(frozen=True)
class BlockChange:
    x: int
    y: int
    z: int
    before: str
    after: str


def _chunk(region: bytes, chunk_x: int, chunk_z: int, data_version: int):
    if len(region) < 2 * SECTOR or len(region) % SECTOR:
        raise FileEditError("INVALID_REGION", "Region size must be a whole number of sectors")
    slot = (chunk_x % 32) + 32 * (chunk_z % 32)
    location = region[4 * slot : 4 * slot + 4]
    offset, count = int.from_bytes(location[:3], "big"), location[3]
    if not offset or not count:
        raise FileEditError("CHUNK_NOT_FOUND", f"Generated chunk {chunk_x},{chunk_z} was not found")
    start = offset * SECTOR
    end = start + count * SECTOR
    if offset < 2 or end > len(region):
        raise FileEditError("INVALID_REGION", "Chunk allocation is outside the region file")
    length = struct.unpack_from(">I", region, start)[0]
    if length < 2 or length + 4 > count * SECTOR or region[start + 4] != 2:
        raise FileEditError("UNSUPPORTED_REGION", "Demo requires an inline zlib-compressed chunk")
    try:
        document = nbtlib.File.parse(io.BytesIO(zlib.decompress(region[start + 5 : start + 4 + length])))
        if (int(document["DataVersion"]) != data_version or int(document["xPos"]) != chunk_x
                or int(document["zPos"]) != chunk_z):
            raise FileEditError("UNSUPPORTED_CHUNK", "Chunk version or coordinates do not match the world")
    except (KeyError, TypeError, ValueError, zlib.error, EOFError, struct.error) as exc:
        raise FileEditError("INVALID_CHUNK", "Chunk NBT could not be decoded") from exc
    return slot, document


def _block_states(document: nbtlib.File, x: int, y: int, z: int):
    try:
        sections = document["sections"]
        matches = [section for section in sections if int(section["Y"]) == y // 16]
        if len(matches) != 1:
            raise FileEditError("UNSUPPORTED_CHUNK", "Target section is missing or ambiguous")
        states = matches[0]["block_states"]
        palette = states["palette"]
        if not palette:
            raise FileEditError("UNSUPPORTED_CHUNK", "Target section has no block palette")
        values = _unpack(states, len(palette))
        index = (y % 16) * 256 + (z % 16) * 16 + x % 16
        entry = palette[values[index]]
        name, _ = _entry_name(entry)
        return states, palette, values, index, name
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise FileEditError("INVALID_CHUNK", "Section block states could not be decoded") from exc


def _entry_name(entry) -> tuple[str, bool]:
    if isinstance(entry, tag.String):
        return str(entry), True
    if isinstance(entry, tag.Compound):
        if set(entry) == {""}:
            return str(entry[""]), True
        if set(entry) == {"id", "properties"}:
            return str(entry["id"]), False
    raise FileEditError("UNSUPPORTED_BLOCK", "Unknown Minecraft 26.3 block palette entry")


def _entry_state(entry) -> str:
    name, plain = _entry_name(entry)
    if plain:
        return name
    properties = entry["properties"]
    return f"{name}[{','.join(f'{key}={properties[key]}' for key in sorted(properties))}]"


def _plain_entry(palette, name: str):
    if isinstance(palette[0], tag.String):
        return tag.String(name)
    return tag.Compound({"": tag.String(name)})


def _target_entry(target: str, palette):
    if "[" not in target:
        return _plain_entry(palette, target)
    name, fields = target[:-1].split("[", 1)
    properties = dict(field.split("=", 1) for field in fields.split(","))
    return tag.Compound({"id": tag.String(name),
                         "properties": tag.Compound({key: tag.String(value)
                                                     for key, value in properties.items()})})


def _unpack(states, palette_size: int) -> list[int]:
    if "data" not in states:
        if palette_size != 1:
            raise FileEditError("INVALID_CHUNK", "Palette data is missing")
        return [0] * 4096
    width = max(4, (palette_size - 1).bit_length())
    per_long = 64 // width
    words = states["data"]
    if len(words) != (4096 + per_long - 1) // per_long:
        raise FileEditError("INVALID_CHUNK", "Palette data length does not match the palette")
    mask = (1 << width) - 1
    values = [((int(words[i // per_long]) & ((1 << 64) - 1)) >> ((i % per_long) * width)) & mask
              for i in range(4096)]
    if any(value >= palette_size for value in values):
        raise FileEditError("INVALID_CHUNK", "Palette index is out of range")
    return values


def _pack(values: list[int], palette_size: int) -> tag.LongArray:
    width = max(4, (palette_size - 1).bit_length())
    per_long = 64 // width
    words = [0] * ((4096 + per_long - 1) // per_long)
    for index, value in enumerate(values):
        words[index // per_long] |= value << ((index % per_long) * width)
    return tag.LongArray([word if word < 1 << 63 else word - (1 << 64) for word in words])


def inspect_block(region: bytes, x: int, y: int, z: int, data_version: int) -> str:
    _, document = _chunk(region, x // 16, z // 16, data_version)
    return _block_states(document, x, y, z)[4]


def inspect_block_state(region: bytes, x: int, y: int, z: int, data_version: int) -> str:
    _, document = _chunk(region, x // 16, z // 16, data_version)
    _, palette, values, index, _ = _block_states(document, x, y, z)
    return _entry_state(palette[values[index]])


def replace_block(region: bytes, x: int, y: int, z: int, old: str, new: str,
                  data_version: int) -> bytes:
    if old not in SAFE_BLOCKS or new not in SAFE_BLOCKS or old == new:
        raise FileEditError("UNSUPPORTED_BLOCK", "Use two distinct supported plain full-cube blocks")
    return apply_edits(region, [BlockEdit(x, y, z, new, old, True)], data_version)[0]


@dataclass
class SectionEditor:
    """One decoded section palette and its ordered candidate block states."""

    states: tag.Compound
    palette: tag.List
    values: list[int]

    @classmethod
    def open(cls, section: tag.Compound) -> SectionEditor:
        if "block_states" not in section:
            raise FileEditError("UNSUPPORTED_CHUNK", "Target section is missing")
        states = section["block_states"]
        try:
            palette = states["palette"]
        except KeyError as exc:
            raise FileEditError("INVALID_CHUNK", "Target section has no block palette") from exc
        if not palette:
            raise FileEditError("INVALID_CHUNK", "Target section has no block palette")
        return cls(states, palette, _unpack(states, len(palette)))

    def apply(self, edit: BlockEdit, entities: set[tuple[int, int, int]]) -> BlockChange | None:
        index = (edit.y % 16) * 256 + (edit.z % 16) * 16 + edit.x % 16
        entry = self.palette[self.values[index]]
        current, plain = _entry_name(entry)
        current_state = _entry_state(entry)
        if edit.strict and not plain:
            raise FileEditError("UNSUPPORTED_BLOCK", "Blocks with properties are outside replace-block")
        observed = current_state if edit.expected and "[" in edit.expected else current
        if edit.expected is not None and observed != edit.expected:
            if edit.strict:
                raise FileEditError("BLOCK_CONFLICT", f"Expected {edit.expected} at {edit.x},{edit.y},{edit.z}; found {current}")
            return None
        if current_state == edit.target:
            return None
        if (edit.x, edit.y, edit.z) in entities:
            raise FileEditError("UNSUPPORTED_BLOCK", "Target block has a block entity")
        if "[" in edit.target and isinstance(self.palette[0], tag.String):
            self.palette = tag.List[tag.Compound]([
                tag.Compound({"": tag.String(str(item))}) for item in self.palette])
            self.states["palette"] = self.palette
        target_index = next((i for i, item in enumerate(self.palette)
                             if _entry_state(item) == edit.target), None)
        if target_index is None:
            self.palette.append(_target_entry(edit.target, self.palette))
            target_index = len(self.palette) - 1
        self.values[index] = target_index
        return BlockChange(edit.x, edit.y, edit.z, current_state, edit.target)

    def persist(self) -> None:
        self.states["data"] = _pack(self.values, len(self.palette))


def apply_edits(region: bytes, edits: list[BlockEdit], data_version: int) -> tuple[bytes, list[BlockChange]]:
    """Apply ordered block edits while decoding each touched chunk and section once."""
    by_chunk: dict[tuple[int, int], list[BlockEdit]] = {}
    for edit in edits:
        by_chunk.setdefault((edit.x // 16, edit.z // 16), []).append(edit)
    output = bytearray(region)
    changes: list[BlockChange] = []
    for (chunk_x, chunk_z), chunk_edits in by_chunk.items():
        change_start = len(changes)
        slot, document = _chunk(region, chunk_x, chunk_z, data_version)
        try:
            entities = {(int(item["x"]), int(item["y"]), int(item["z"]))
                        for item in document.get("block_entities", [])}
            sections = {int(section["Y"]): section for section in document["sections"]}
        except (KeyError, TypeError, ValueError) as exc:
            raise FileEditError("INVALID_CHUNK", "Chunk sections or block entities could not be decoded") from exc
        cached: dict[int, SectionEditor] = {}
        for edit in chunk_edits:
            section_y = edit.y // 16
            if section_y not in cached:
                section = sections.get(section_y)
                if section is None:
                    raise FileEditError("UNSUPPORTED_CHUNK", "Target section is missing")
                cached[section_y] = SectionEditor.open(section)
            change = cached[section_y].apply(edit, entities)
            if change is not None:
                changes.append(change)
        if len(changes) == change_start:
            continue
        for editor in cached.values():
            editor.persist()
        # Drop derived caches; in-game regeneration must be validated separately.
        document.pop("Heightmaps", None)
        document.pop("isLightOn", None)
        for section in sections.values():
            section.pop("BlockLight", None)
            section.pop("SkyLight", None)
        _append_chunk(output, slot, document)
    return bytes(output), changes


def _append_chunk(region: bytearray, slot: int, document: nbtlib.File) -> None:
    raw = io.BytesIO()
    document.write(raw)
    compressed = zlib.compress(raw.getvalue())
    record = struct.pack(">I", len(compressed) + 1) + b"\x02" + compressed
    sectors = (len(record) + SECTOR - 1) // SECTOR
    if sectors > 255:
        raise FileEditError("UNSUPPORTED_CHUNK", "Edited chunk exceeds an inline region allocation")
    offset = len(region) // SECTOR
    if offset >= 1 << 24:
        raise FileEditError("UNSUPPORTED_REGION", "Region is too large for another chunk allocation")
    region.extend(record)
    region.extend(b"\0" * (sectors * SECTOR - len(record)))
    region[4 * slot : 4 * slot + 4] = offset.to_bytes(3, "big") + bytes([sectors])
