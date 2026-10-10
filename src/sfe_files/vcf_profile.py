"""Source-preserving vCard 3.0/4.0 contact properties with stable UID addressing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .content_lines import (
    ContentLine,
    TextFile,
    decode_text,
    encode_text,
    property_line,
    replace_value,
    split,
)
from .plan_ops import FileEditError, _fields, _pointer_parts

OPS = {"set-property", "add-property"}
TEXT_NAMES = {"FN", "TITLE", "ROLE", "NOTE", "EMAIL"}
MUTABLE_NAMES = TEXT_NAMES | {"TEL", "URL", "ORG"}
CONTRACT = {
    "addressing": {
        "inspect": "/contacts/<UID> or /records/<1-based-record-number>",
        "escaping": "Escape / as ~1 and ~ as ~0 in a UID pointer segment.",
        "edit": "Exact uid plus property name; set-property index is 1-based among that name, including groups.",
    },
    "operations": {
        "set-property": {
            "example": {"op": "set-property", "uid": "urn:uuid:service-01", "name": "EMAIL", "index": 2,
                        "value": "support@example.com"},
            "description": "Replace one occurrence, retaining its original group and parameters.",
        },
        "add-property": {
            "example": {"op": "add-property", "uid": "urn:uuid:service-01", "name": "TEL",
                        "params": ";VALUE=uri;TYPE=work", "value": "tel:+66-2-555-0100"},
            "description": "Append a property before END:VCARD. Optional params begins with ; and group is a token.",
        },
    },
    "mutable_properties": sorted(MUTABLE_NAMES),
    "limitations": [
        "UTF-8 vCard 3.0 or 4.0 only. VERSION 2.1 and legacy text encodings are rejected.",
        "UID is required for edits. UID-less cards can be inspected with /records/N; duplicate UIDs are rejected.",
        "FN, TITLE, ROLE, NOTE, EMAIL and text TEL use JSON strings. ORG requires an array of organization components.",
        "TEL in vCard 4 defaults to TEXT per RFC 6350; VALUE=uri explicitly selects URI semantics.",
        "vCard 3 TEL uses a phone-number TEXT value; URL always uses a URI. URI values are not TEXT-escaped.",
        "N, ADR, PHOTO and unknown properties are read-only and remain byte-for-byte intact, including folds.",
        "REV is preserved; editing does not generate timestamps or alter synchronization metadata.",
        "Modified lines fold at 75 UTF-8 octets. Untouched lines retain original spelling, folding and endings.",
    ],
}


@dataclass
class Contact:
    start: int
    end: int
    uid: str | None
    version: str


@dataclass
class VcfDocument:
    file: TextFile
    contacts: list[Contact]


def _error(message: str, code: str = "INVALID_VCF"):
    raise FileEditError(code, message)


def _parameters(raw: str) -> dict[str, list[str]]:
    """Read value/encoding parameters without changing their original spelling."""
    if not isinstance(raw, str) or (raw and not raw.startswith(";")):
        _error("params must be a string beginning with ;", "INVALID_STEP")
    if not raw:
        return {}
    if re.search(r"[\x00-\x1f\x7f]", raw):
        _error("Unsupported control character in vCard parameter")
    chunks, start, quoted = [], 1, False
    for index, char in enumerate(raw[1:], 1):
        if char == '"':
            quoted = not quoted
        elif char == ";" and not quoted:
            chunks.append(raw[start:index])
            start = index + 1
        elif (char == ":" and not quoted) or char in "\r\n":
            _error("Invalid vCard parameter syntax")
    chunks.append(raw[start:])
    if quoted:
        _error("Unclosed quoted vCard parameter")
    out: dict[str, list[str]] = {}
    for chunk in chunks:
        key, equals, value = chunk.partition("=")
        if not equals or not re.fullmatch(r"[A-Za-z0-9-]+", key):
            _error("Expected named vCard parameters (NAME=value)")
        if not re.fullmatch(r'(?:"[^"\r\n]*"|[^";:,\r\n]+)(?:,(?:"[^"\r\n]*"|[^";:,\r\n]+))*', value):
            _error("Invalid vCard parameter value")
        out.setdefault(key.upper(), []).append(value.strip('"'))
    for key in ("VALUE", "CHARSET", "ENCODING"):
        if len(out.get(key, [])) > 1:
            _error(f"Duplicate {key} parameter is ambiguous")
    return out


def _parameter(parameters: dict[str, list[str]], name: str) -> str | None:
    values = parameters.get(name)
    return values[0].lower() if values else None


def _encoding(parameters: dict[str, list[str]], version: str):
    charset = _parameter(parameters, "CHARSET")
    if charset is not None and charset not in {"utf-8", "utf8"}:
        _error("Only UTF-8 contact text is supported", "UNSUPPORTED_VCF")
    encoding = _parameter(parameters, "ENCODING")
    if encoding is not None and (version == "4.0" or encoding != "b"):
        _error("Legacy or invalid vCard encodings are unsupported", "UNSUPPORTED_VCF")


def _kind(line: ContentLine, version: str) -> str:
    parameters = _parameters(line.params)
    if "ENCODING" in parameters:
        return "opaque"
    value_type = _parameter(parameters, "VALUE")
    if line.name in TEXT_NAMES | {"ORG"}:
        return "text" if value_type in {None, "text"} else "opaque"
    if line.name == "URL":
        return "uri" if value_type in {None, "uri"} else "opaque"
    if line.name == "TEL":
        if version == "4.0":
            return "uri" if value_type == "uri" else "text" if value_type in {None, "text"} else "opaque"
        return "text" if value_type in {None, "text", "phone-number"} else "opaque"
    return "opaque"


def _components(value: str) -> list[str]:
    """Split ORG on unescaped semicolons before applying TEXT decoding."""
    parts, start, escaped = [], 0, False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ";":
            parts.append(decode_text(value[start:index]))
            start = index + 1
    parts.append(decode_text(value[start:]))
    return parts


def _scan(file: TextFile) -> list[Contact]:
    contacts, start = [], None
    seen = set()
    for index, line in enumerate(file.lines):
        if line.name == "BEGIN":
            if start is not None or line.value.upper() != "VCARD" or line.group or line.params:
                _error("Expected flat BEGIN:VCARD / END:VCARD records")
            start = index
        elif line.name == "END":
            if start is None or line.value.upper() != "VCARD" or line.group or line.params:
                _error("Unmatched END:VCARD")
            properties = [item for item in file.lines[start + 1:index] if item.name]
            versions = [item for item in properties if item.name == "VERSION"]
            if len(versions) != 1 or versions[0].group or versions[0].params:
                _error("Each contact requires one ungrouped VERSION property")
            version = versions[0].value
            if version not in {"3.0", "4.0"}:
                _error("Only vCard VERSION:3.0 and VERSION:4.0 are supported", "UNSUPPORTED_VCF")
            if version == "4.0" and properties[0].name != "VERSION":
                _error("vCard 4.0 VERSION must immediately follow BEGIN:VCARD")
            if not any(item.name == "FN" for item in properties):
                _error("Each contact requires an FN property")
            if version == "3.0" and not any(item.name == "N" for item in properties):
                _error("vCard 3.0 requires an N property")
            for item in properties:
                _encoding(_parameters(item.params), version)
            uids = [item for item in properties if item.name == "UID"]
            if len(uids) > 1:
                _error("Each contact can have at most one UID", "DUPLICATE_UID")
            uid = None
            if uids:
                uid_line = uids[0]
                uid = (
                    decode_text(uid_line.value)
                    if version == "3.0" or _parameter(_parameters(uid_line.params), "VALUE") == "text"
                    else uid_line.value
                )
                if not uid or uid in seen:
                    _error("Contact UIDs must be nonempty and unique", "DUPLICATE_UID")
                seen.add(uid)
            contacts.append(Contact(start, index, uid, version))
            start = None
        elif start is None and line.name:
            _error("Properties outside a vCard record are unsupported")
    if start is not None or not contacts:
        _error("Expected complete vCard records")
    return contacts


def parse(data: bytes) -> VcfDocument:
    file = split(data)
    return VcfDocument(file, _scan(file))


def _lines(doc: VcfDocument, contact: Contact):
    return doc.file.lines[contact.start + 1:contact.end]


def _brief(doc: VcfDocument, contact: Contact, record: int) -> dict:
    names = [line for line in _lines(doc, contact) if line.name == "FN"]
    return {
        "record": record,
        "uid": contact.uid,
        "version": contact.version,
        "fn": decode_text(names[0].value) if _kind(names[0], contact.version) == "text" else names[0].value,
        "editable": contact.uid is not None,
    }


def summary(doc: VcfDocument) -> dict:
    return {
        "contact_count": len(doc.contacts),
        "contacts": [_brief(doc, item, index + 1) for index, item in enumerate(doc.contacts[:200])],
        "truncated": len(doc.contacts) > 200,
    }


def _find(doc: VcfDocument, uid: str) -> Contact:
    if not isinstance(uid, str):
        _error("uid must be a string", "INVALID_STEP")
    matches = [contact for contact in doc.contacts if contact.uid == uid]
    if len(matches) != 1:
        _error("No contact has the requested UID", "TARGET_NOT_FOUND")
    return matches[0]


def inspect(doc: VcfDocument, pointer: str = "") -> dict:
    if not isinstance(pointer, str):
        _error("pointer must be a string", "INVALID_POINTER")
    parts = _pointer_parts(pointer)
    if not parts:
        return {"pointer": pointer, "value": summary(doc)}
    if len(parts) == 2 and parts[0] == "contacts":
        contact = _find(doc, parts[1])
    elif len(parts) == 2 and parts[0] == "records" and re.fullmatch(r"[1-9][0-9]*", parts[1]):
        index = int(parts[1]) - 1
        if index >= len(doc.contacts):
            _error("Contact record not found", "TARGET_NOT_FOUND")
        contact = doc.contacts[index]
    else:
        _error("Use /contacts/<UID> or /records/<1-based-record-number>", "INVALID_POINTER")
    properties, counts = [], {}
    for line in _lines(doc, contact):
        if not line.name:
            continue
        counts[line.name] = counts.get(line.name, 0) + 1
        kind = _kind(line, contact.version)
        value: Any = line.value
        if kind == "text":
            value = _components(value) if line.name == "ORG" else decode_text(value)
        item = {
            "name": line.name, "index": counts[line.name], "group": line.group, "params": line.params,
            "value_type": kind, "editable": contact.uid is not None and kind != "opaque",
        }
        if len(line.value) > 20000:
            item.update(value_omitted=True, value_length=len(line.value))
        else:
            item["value"] = value
        properties.append(item)
    return {
        "pointer": pointer, "uid": contact.uid, "version": contact.version,
        "properties": properties[:500], "property_count": len(properties), "truncated": len(properties) > 500,
    }


def _uri(value: str):
    if (
        not re.fullmatch(r"[A-Za-z][A-Za-z0-9+.-]*:[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+", value)
        or re.search(r"%(?![0-9A-Fa-f]{2})", value)
    ):
        _error("URI values require a scheme and valid ASCII URI characters; encode spaces/non-ASCII", "INVALID_URI")


def _value(line: ContentLine, version: str, value: Any) -> str:
    kind = _kind(line, version)
    if kind == "opaque":
        _error("This property has an unsupported value type or encoding", "UNSUPPORTED_VCF_PROPERTY")
    if line.name == "ORG":
        if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
            _error("ORG value must be a nonempty array of organization components", "INVALID_VALUE")
        return ";".join(encode_text(item) for item in value)
    if not isinstance(value, str):
        _error("Property value must be a string", "INVALID_VALUE")
    if kind == "uri":
        _uri(value)
        return value
    if line.name in {"EMAIL", "TEL"} and any(char in value for char in "\r\n"):
        _error("EMAIL and TEL must be single-line values", "INVALID_VALUE")
    return encode_text(value)


def edit(doc: VcfDocument, step: dict) -> VcfDocument:
    if not isinstance(step, dict) or not isinstance(step.get("op"), str) or step["op"] not in OPS:
        _error("Unsupported vCard operation", "INVALID_STEP")
    required = {"op", "uid", "name", "value"}
    if step["op"] == "set-property":
        required.add("index")
        _fields(step, required, required)
    else:
        _fields(step, required | {"params", "group"}, required)
    name = step["name"]
    if not isinstance(name, str) or name not in MUTABLE_NAMES:
        _error("Use a supported uppercase property name; structured N/ADR and identity fields are read-only",
               "UNSUPPORTED_VCF_PROPERTY")
    contact = _find(doc, step["uid"])
    if step["op"] == "set-property":
        index = step["index"]
        if type(index) is not int or index < 1:
            _error("index must be a 1-based property occurrence", "INVALID_STEP")
        matches = [i for i in range(contact.start + 1, contact.end) if doc.file.lines[i].name == name]
        if index > len(matches):
            _error("Property occurrence not found", "TARGET_NOT_FOUND")
        target = matches[index - 1]
        line = doc.file.lines[target]
        replacement = replace_value(line, _value(line, contact.version, step["value"]), doc.file.newline)
        doc.file.lines[target] = replacement
    else:
        params, group = step.get("params", ""), step.get("group")
        parameters = _parameters(params)
        _encoding(parameters, contact.version)
        if group is not None and (not isinstance(group, str) or not re.fullmatch(r"[A-Za-z0-9-]+", group)):
            _error("group must be a vCard token", "INVALID_STEP")
        line = property_line(name, "", doc.file.newline, params=params, group=group)
        line = property_line(name, _value(line, contact.version, step["value"]), doc.file.newline,
                             params=params, group=group)
        doc.file.lines.insert(contact.end, line)
        doc.contacts = _scan(doc.file)
    return doc


def serialize(doc: VcfDocument) -> bytes:
    result = doc.file.serialize()
    parse(result)
    return result
