"""Source-preserving UTF-8 content lines shared by iCalendar and vCard."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .plan_ops import FileEditError


@dataclass
class ContentLine:
    name: str
    group: str | None
    params: str  # Includes the leading semicolon, if present.
    value: str
    raw: str
    prefix: str  # Original spelling of group, name and parameters before the colon.


@dataclass
class TextFile:
    lines: list[ContentLine]
    newline: str
    bom: bool = False

    def serialize(self) -> bytes:
        return (b"\xef\xbb\xbf" if self.bom else b"") + "".join(line.raw for line in self.lines).encode("utf-8")


def _line(raw: str) -> ContentLine:
    unfolded = re.sub(r"(?:\r\n|\n|\r)[ \t]", "", raw).rstrip("\r\n")
    if not unfolded:
        return ContentLine("", None, "", "", raw, "")
    quoted = False
    colon = None
    for index, char in enumerate(unfolded):
        if char == '"':
            quoted = not quoted
        elif char == ":" and not quoted:
            colon = index
            break
    if colon is None:
        raise FileEditError("INVALID_CONTENT_LINE", "Expected property:value outside quoted parameters")
    prefix, value = unfolded[:colon], unfolded[colon + 1:]
    head, separator, parameters = prefix.partition(";")
    group, dot, name = head.rpartition(".")
    if not dot:
        group, name = None, head
    if (not re.fullmatch(r"[A-Za-z0-9-]+", name)
            or (dot and not group) or (group and not re.fullmatch(r"[A-Za-z0-9-]+", group))):
        raise FileEditError("INVALID_CONTENT_LINE", "Invalid property name or group")
    return ContentLine(name.upper(), group, separator + parameters, value, raw, prefix)


def split(data: bytes) -> TextFile:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError as exc:
        raise FileEditError("INVALID_CONTENT_LINE", "Only UTF-8 content-line files are supported") from exc
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", text):
        raise FileEditError("INVALID_CONTENT_LINE", "Unsupported control character")
    match = re.search(r"\r\n|\n|\r", text)
    newline = match.group() if match else "\r\n"
    # splitlines() would also split legal Unicode paragraph separators in property values.
    physical = re.findall(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+$", text)
    blocks: list[str] = []
    for line in physical:
        if line.startswith((" ", "\t")):
            if not blocks or not blocks[-1].strip():
                raise FileEditError("INVALID_CONTENT_LINE", "Folded line has no preceding property")
            blocks[-1] += line
        else:
            blocks.append(line)
    return TextFile([_line(block) for block in blocks], newline, data.startswith(b"\xef\xbb\xbf"))


def _fold(value: str, newline: str, terminal: bool = True) -> str:
    if newline not in {"\r\n", "\n", "\r"} or "\r" in value or "\n" in value:
        raise FileEditError("INVALID_CONTENT_LINE", "Content lines cannot contain literal newlines")
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", value):
        raise FileEditError("INVALID_CONTENT_LINE", "Unsupported control character")
    result = []
    width = 0
    try:
        for char in value:
            count = len(char.encode("utf-8"))
            if width + count > 75:
                result.append(newline + " ")
                width = 1
            result.append(char)
            width += count
    except UnicodeError as exc:
        raise FileEditError("INVALID_CONTENT_LINE", "Invalid Unicode in property value") from exc
    return "".join(result) + (newline if terminal else "")


def property_line(name: str, value: str, newline: str = "\r\n", params: str = "", group: str | None = None):
    if not isinstance(value, str) or not isinstance(params, str) or (params and not params.startswith(";")):
        raise FileEditError("INVALID_CONTENT_LINE", "Property value must be text; params must start with ;")
    prefix = (group + "." if group else "") + name + params
    return _line(_fold(prefix + ":" + value, newline))


def replace_value(line: ContentLine, value: str, newline: str) -> ContentLine:
    return _line(_fold(line.prefix + ":" + value, newline, terminal=line.raw.endswith(("\r", "\n"))))


def encode_text(value: str) -> str:
    if not isinstance(value, str) or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]", value):
        raise FileEditError("INVALID_TEXT", "Expected text without control characters or invalid Unicode")
    return (
        value.replace("\r\n", "\n").replace("\r", "\n")
        .replace("\\", "\\\\").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")
    )


def decode_text(value: str) -> str:
    def unescape(match):
        char = match[1]
        if char not in {"n", "N", ",", ";", "\\"}:
            raise FileEditError("INVALID_TEXT", "Unsupported TEXT escape sequence")
        return "\n" if char in {"n", "N"} else char

    return re.sub(r"\\(.|$)", unescape, value)
