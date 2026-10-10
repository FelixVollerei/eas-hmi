"""Subtitle cue editing that retains untouched text blocks byte-for-byte."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .plan_ops import FileEditError, _fields

OPS = {"set-text", "shift-time", "set-timing"}
SRT_TIME = r"[0-9]{2,}:[0-5][0-9]:[0-5][0-9],[0-9]{3}"
VTT_TIME = r"(?:[0-9]{2,}:)?[0-5][0-9]:[0-5][0-9]\.[0-9]{3}"


@dataclass
class Cue:
    block: int
    timing_line: int
    identifier: str | None
    settings: str
    start_ms: int
    end_ms: int
    text: str


@dataclass
class Document:
    format: str
    blocks: list[str]
    cues: list[Cue]
    bom: bool
    newline: str


def contract(fmt):
    return {
        "addressing": "One-based cue ordinal, bound to plan base bytes; inspect with --pointer /cues/1.",
        "operations": {
            "set-text": {
                "example": {"op": "set-text", "cue": 1, "text": "Ready for review."},
                "description": "Replace cue text. Multiline text is allowed, blank lines are rejected.",
            },
            "shift-time": {
                "example": {"op": "shift-time", "delta_ms": 250, "cues": [1, 2]},
                "description": "Shift selected cues by integer milliseconds; omit cues for all cues.",
            },
            "set-timing": {
                "example": {"op": "set-timing", "cue": 1, "start_ms": 1000, "end_ms": 3500},
                "description": "Set a nonnegative start and a strictly later end, in integer milliseconds.",
            },
        },
        "limitations": [
            "UTF-8 only, with optional BOM. Original line endings, separators and unchanged blocks are retained.",
            "Cue ordinal is independent of original SRT numbers or optional WebVTT identifiers, which are preserved.",
            "Timestamps require exactly three millisecond digits and minute/second values from 00 through 59.",
            "Overlapping cues are allowed. WebVTT start times must remain nondecreasing in file order.",
            "Timing edits on WebVTT cues containing inline timestamp tags are rejected; replace such text explicitly first.",
            "Text replacement rejects blank lines, NUL and invalid UTF-8; WebVTT also rejects the reserved --> sequence.",
            "WebVTT header, NOTE, STYLE, REGION blocks and cue settings are retained without CSS or player validation.",
            "SRT supports numbered blocks and HH:MM:SS,mmm times; optional proprietary timing suffixes are unsupported.",
            "No cue insertion/deletion, renumbering, markup rendering, media synchronization or player compatibility check.",
        ],
        "references": [
            "https://www.w3.org/TR/webvtt1/"
            if fmt == "vtt"
            else "https://www.loc.gov/preservation/digital/formats/fdd/fdd000569.shtml"
        ],
    }


def _lines(text):
    return [line for line in re.findall(r"[^\r\n]*(?:\r\n|\n|\r|$)", text) if line]


def _clean(line):
    return line.rstrip("\r\n")


def _timing_pattern(fmt):
    timestamp = SRT_TIME if fmt == "srt" else VTT_TIME
    tail = r"[ \t]*" if fmt == "srt" else r"(?:[ \t]+[^\r\n]*)?"
    return re.compile(
        rf"^(?P<start>{timestamp})(?P<arrow>[ \t]+-->[ \t]+)(?P<end>{timestamp})(?P<tail>{tail})$"
    )


def _milliseconds(timestamp):
    parts = timestamp.replace(",", ".").split(":")
    if len(parts) == 2:
        parts.insert(0, "0")
    hours, minutes = int(parts[0]), int(parts[1])
    seconds, milliseconds = parts[2].split(".")
    return ((hours * 60 + minutes) * 60 + int(seconds)) * 1000 + int(milliseconds)


def _timestamp(value, fmt, original):
    seconds, ms = divmod(value, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    fraction = "," if fmt == "srt" else "."
    body = f"{minutes:02}:{seconds:02}{fraction}{ms:03}"
    return f"{hours:02}:" + body if fmt == "srt" or original.count(":") == 2 or hours else body


def _validate_times(cues, overrides=None, *, ordered):
    overrides = overrides or {}
    previous = -1
    for index, cue in enumerate(cues):
        start, end = overrides.get(index, (cue.start_ms, cue.end_ms))
        if start < 0 or end <= start:
            raise FileEditError("INVALID_TIMING", "Cue timing requires start_ms >= 0 and end_ms > start_ms")
        if ordered and start < previous:
            raise FileEditError(
                "INVALID_TIMING", "WebVTT cue start times must be nondecreasing in file order"
            )
        previous = start


def parse(data: bytes, fmt: str) -> Document:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError as exc:
        raise FileEditError("INVALID_SUBTITLE", "Subtitle input must be UTF-8") from exc
    if "\x00" in text:
        raise FileEditError("INVALID_SUBTITLE", "Subtitle input cannot contain NUL")
    lines = _lines(text)
    newline = next((line[len(_clean(line)) :] for line in lines if line != _clean(line)), "\n")
    blocks, active = [], []
    for line in lines:
        blank = _clean(line) == "" if fmt == "vtt" else not _clean(line).strip(" \t")
        if blank:
            if active:
                blocks.append("".join(active))
                active = []
            blocks.append(line)
        else:
            active.append(line)
    if active:
        blocks.append("".join(active))
    document = Document(fmt, blocks, [], data.startswith(b"\xef\xbb\xbf"), newline)
    pattern = _timing_pattern(fmt)
    header_seen = False
    for index, raw in enumerate(blocks):
        content = [_clean(line) for line in _lines(raw)]
        if not content or all(not item.strip(" \t") for item in content):
            if fmt == "vtt" and not header_seen:
                raise FileEditError("INVALID_SUBTITLE", "WebVTT must begin with WEBVTT")
            continue
        first = content[0]
        if fmt == "vtt" and not header_seen:
            if not re.fullmatch(r"WEBVTT(?:[ \t].*)?", first) or any("-->" in item for item in content):
                raise FileEditError(
                    "INVALID_SUBTITLE", "WebVTT requires a WEBVTT header and a blank line before cues"
                )
            header_seen = True
            continue
        if fmt == "vtt" and re.match(r"NOTE(?:[ \t]|$)", first):
            continue
        if fmt == "vtt" and first in {"STYLE", "REGION"}:
            if document.cues:
                raise FileEditError("INVALID_SUBTITLE", "WebVTT STYLE and REGION blocks must precede cues")
            continue
        if fmt == "srt":
            if not re.fullmatch(r"[0-9]+", first) or len(content) < 3:
                raise FileEditError(
                    "INVALID_SUBTITLE", "SRT block requires a numeric index, timing line and text"
                )
            timing_line, identifier = 1, first
        else:
            timing_line = 0 if pattern.fullmatch(first) else 1
            identifier = first if timing_line else None
            if identifier is not None and "-->" in identifier:
                raise FileEditError("INVALID_SUBTITLE", "Invalid WebVTT timing line or identifier")
        match = pattern.fullmatch(content[timing_line]) if len(content) > timing_line else None
        if match is None:
            raise FileEditError("INVALID_SUBTITLE", f"Invalid {fmt.upper()} timing line in block {index + 1}")
        payload = "\n".join(content[timing_line + 1 :])
        if fmt == "vtt" and "-->" in payload:
            raise FileEditError("INVALID_SUBTITLE", "WebVTT cue text cannot contain -->")
        document.cues.append(
            Cue(
                index,
                timing_line,
                identifier,
                match["tail"].strip(),
                _milliseconds(match["start"]),
                _milliseconds(match["end"]),
                payload,
            )
        )
    if fmt == "vtt" and not header_seen:
        raise FileEditError("INVALID_SUBTITLE", "WebVTT requires a WEBVTT header")
    _validate_times(document.cues, ordered=fmt == "vtt")
    return document


def _cue_index(document, value):
    if type(value) is not int or value < 1:
        raise FileEditError("INVALID_STEP", "cue must be a positive integer ordinal")
    if value > len(document.cues):
        raise FileEditError("TARGET_NOT_FOUND", f"Cue not found: {value}")
    return value - 1


def _view(document, index):
    cue = document.cues[index]
    return {
        "cue": index + 1,
        "number" if document.format == "srt" else "identifier": cue.identifier,
        "start_ms": cue.start_ms,
        "end_ms": cue.end_ms,
        "text": cue.text,
        "settings": cue.settings,
        "embedded_timestamps": document.format == "vtt" and bool(re.search(r"<[0-9]", cue.text)),
    }


def summary(document):
    views = [_view(document, index) for index in range(min(len(document.cues), 100))]
    truncated = len(document.cues) > 100
    for view in views:
        truncated = truncated or len(view["text"]) > 200
        view["text"] = view["text"][:200]
    return {
        "cue_count": len(document.cues),
        "cues": views,
        "truncated": truncated,
        "duration_ms": max((cue.end_ms for cue in document.cues), default=0),
    }


def inspect(document, pointer=""):
    if pointer == "":
        return {"summary": summary(document)}
    if not isinstance(pointer, str) or not re.fullmatch(r"/cues/[1-9][0-9]*", pointer):
        raise FileEditError("INVALID_POINTER", "Use /cues/1 with a one-based cue ordinal")
    return {"pointer": pointer, "value": _view(document, _cue_index(document, int(pointer[6:])))}


def edit(document, step):
    if not isinstance(step, dict) or not isinstance(step.get("op"), str) or step["op"] not in OPS:
        raise FileEditError("INVALID_STEP", "Unsupported subtitle operation")
    op = step["op"]
    fields = {
        "set-text": {"op", "cue", "text"},
        "shift-time": {"op", "delta_ms", "cues"},
        "set-timing": {"op", "cue", "start_ms", "end_ms"},
    }[op]
    _fields(step, fields, fields - {"cues"})
    if op == "set-text":
        index = _cue_index(document, step["cue"])
        text = step["text"]
        if not isinstance(text, str):
            raise FileEditError("INVALID_STEP", "text must be a string")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if any(not line.strip(" \t") for line in text.split("\n")) or "\x00" in text:
            raise FileEditError(
                "INVALID_STEP", "Cue text must be nonempty and cannot contain blank lines or NUL"
            )
        if document.format == "vtt" and "-->" in text:
            raise FileEditError("INVALID_STEP", "WebVTT cue text cannot contain -->")
        try:
            text.encode("utf-8")
        except UnicodeError as exc:
            raise FileEditError("INVALID_STEP", "Cue text must be valid UTF-8") from exc
        cue = document.cues[index]
        raw = document.blocks[cue.block]
        lines = _lines(raw)
        newline = next(
            (line[len(_clean(line)) :] for line in lines if line != _clean(line)), document.newline
        )
        prefix = "".join(lines[: cue.timing_line + 1])
        if not prefix.endswith(("\r", "\n")):
            prefix += newline
        document.blocks[cue.block] = (
            prefix + text.replace("\n", newline) + (newline if raw.endswith(("\r", "\n")) else "")
        )
        cue.text = text
        return document
    if op == "shift-time":
        if type(step["delta_ms"]) is not int:
            raise FileEditError("INVALID_STEP", "delta_ms must be an integer")
        if "cues" in step:
            if not isinstance(step["cues"], list) or not step["cues"]:
                raise FileEditError("INVALID_STEP", "cues must be a nonempty list of cue ordinals")
            indices = [_cue_index(document, value) for value in step["cues"]]
            if len(set(indices)) != len(indices):
                raise FileEditError("INVALID_STEP", "cues must not contain duplicates")
        else:
            indices = list(range(len(document.cues)))
        times = {
            i: (document.cues[i].start_ms + step["delta_ms"], document.cues[i].end_ms + step["delta_ms"])
            for i in indices
        }
    else:
        index = _cue_index(document, step["cue"])
        if type(step["start_ms"]) is not int or type(step["end_ms"]) is not int:
            raise FileEditError("INVALID_STEP", "start_ms and end_ms must be integers")
        times = {index: (step["start_ms"], step["end_ms"])}
    _validate_times(document.cues, times, ordered=document.format == "vtt")
    if document.format == "vtt" and any(re.search(r"<[0-9]", document.cues[i].text) for i in times):
        raise FileEditError(
            "UNSUPPORTED_TARGET", "Timing edits on WebVTT inline timestamp cues are unsupported"
        )
    for index, (start, end) in times.items():
        cue = document.cues[index]
        lines = _lines(document.blocks[cue.block])
        old = _clean(lines[cue.timing_line])
        match = _timing_pattern(document.format).fullmatch(old)
        lines[cue.timing_line] = (
            _timestamp(start, document.format, match["start"])
            + match["arrow"]
            + _timestamp(end, document.format, match["end"])
            + match["tail"]
            + lines[cue.timing_line][len(old) :]
        )
        document.blocks[cue.block] = "".join(lines)
        cue.start_ms, cue.end_ms = start, end
    return document


def serialize(document):
    return (b"\xef\xbb\xbf" if document.bom else b"") + "".join(document.blocks).encode("utf-8")
