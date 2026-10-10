"""UID-addressed, source-preserving edits for individual iCalendar events."""

from __future__ import annotations

import re
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime

from .content_lines import decode_text, encode_text, property_line, replace_value, split
from .plan_ops import FileEditError, _fields, _pointer_parts

OPS = {"set-event-field", "reschedule"}
TEXT_FIELDS = {"SUMMARY", "DESCRIPTION", "LOCATION"}
RECURRENCE = {"RRULE", "RDATE", "EXDATE", "EXRULE", "RECURRENCE-ID"}
SINGLE_FIELDS = TEXT_FIELDS | {
    "UID", "DTSTAMP", "DTSTART", "DTEND", "DURATION", "CLASS", "CREATED", "GEO", "LAST-MODIFIED",
    "ORGANIZER", "PRIORITY", "SEQUENCE", "STATUS", "TRANSP", "URL", "RECURRENCE-ID",
}
CONTRACT = {
    "addressing": (
        "Select one top-level VEVENT by its exact UID: /events/<uid>. JSON Pointer escaping uses ~1 for / "
        "and ~0 for ~. Duplicate UIDs are readable in the summary but cannot be individually addressed."
    ),
    "operations": {
        "set-event-field": {
            "example": {
                "op": "set-event-field", "uid": "review-1@example.test", "field": "LOCATION", "value": "Room B",
            },
            "description": "Insert or replace SUMMARY, DESCRIPTION or LOCATION with a plain Unicode string.",
        },
        "reschedule": {
            "example": {
                "op": "reschedule", "uid": "review-1@example.test",
                "start": "2026-10-01T09:00:00Z", "end": "2026-10-01T10:00:00Z",
            },
            "description": (
                "Set DTSTART and DTEND. Both arguments must be YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ, "
                "matching the original event's DATE or UTC DATE-TIME type. End is exclusive and must be later."
            ),
        },
    },
    "limitations": [
        "UTF-8 iCalendar VERSION 2.0 with exactly one VCALENDAR. This is a local file editor, not a scheduling client.",
        (
            "Only one non-recurring VEVENT is edited per step. Recurrence properties, duplicate UIDs, "
            "duplicate singleton fields and event child components other than VALARM block target edits."
        ),
        (
            "Rescheduling supports existing DATE or UTC DTSTART only; floating times, TZID times and DURATION "
            "events are rejected. Text fields on timezone-based events remain editable."
        ),
        (
            "VALARM, VTIMEZONE, unknown properties and unmodified folded lines are preserved. Relative alarms "
            "follow the new event times; absolute alarm timestamps remain unchanged."
        ),
        (
            "DTSTAMP, SEQUENCE and LAST-MODIFIED are preserved for deterministic local edits. No invitation "
            "or calendar update is sent, and scheduling publication metadata is not generated."
        ),
        "An existing TEXT field with ALTREP is rejected because its alternate representation could become stale.",
    ],
    "standard": "https://www.rfc-editor.org/rfc/rfc5545",
}


@dataclass
class Event:
    start: int
    end: int = -1
    properties: list[int] = field(default_factory=list)
    children: list[str] = field(default_factory=list)


def _fail(message, code="INVALID_ICS"):
    raise FileEditError(code, message)


def _events(document):
    stack = []
    events = []
    current = None
    calendar_properties = []
    calendar_count = 0
    for index, line in enumerate(document.lines):
        if not line.name:
            continue
        if line.group:
            _fail("Grouped content lines are not supported in iCalendar")
        if line.name in {"BEGIN", "END"}:
            if line.params or not re.fullmatch(r"[A-Za-z0-9-]+", line.value):
                _fail("Invalid component boundary")
            component = line.value.upper()
            if line.name == "BEGIN":
                if not stack:
                    if component != "VCALENDAR" or calendar_count:
                        _fail("Expected exactly one VCALENDAR")
                    calendar_count += 1
                elif component == "VCALENDAR":
                    _fail("Nested VCALENDAR is invalid")
                if component == "VEVENT":
                    if stack != ["VCALENDAR"]:
                        _fail("VEVENT must be a direct child of VCALENDAR")
                    current = Event(index)
                elif current is not None:
                    if component == "VALARM" and stack[-1] != "VEVENT":
                        _fail("VALARM must be a direct child of its event")
                    current.children.append(component)
                stack.append(component)
            else:
                if not stack or stack[-1] != component:
                    _fail("Mismatched component END")
                stack.pop()
                if component == "VEVENT":
                    current.end = index
                    events.append(current)
                    current = None
        else:
            if not stack:
                _fail("Property appears outside VCALENDAR")
            if stack == ["VCALENDAR"]:
                calendar_properties.append(index)
            elif current is not None and stack[-1] == "VEVENT":
                current.properties.append(index)
    if stack or calendar_count != 1:
        _fail("Unclosed or missing VCALENDAR")
    versions = [document.lines[i].value for i in calendar_properties if document.lines[i].name == "VERSION"]
    prodids = [i for i in calendar_properties if document.lines[i].name == "PRODID"]
    if versions != ["2.0"] or len(prodids) != 1:
        _fail("VCALENDAR requires one VERSION:2.0 and one PRODID")
    return events


def parse(data: bytes):
    document = split(data)
    _events(document)
    return document


def _properties(document, event, name):
    return [i for i in event.properties if document.lines[i].name == name]


def _uid(document, event):
    values = _properties(document, event, "UID")
    return decode_text(document.lines[values[0]].value) if len(values) == 1 else None


def _params(line):
    values = {}
    chunks = re.split(r';(?=(?:[^"]*"[^"]*")*[^"]*$)', line.params.lstrip(";"))
    for chunk in filter(None, chunks):
        key, separator, value = chunk.partition("=")
        key = key.upper()
        if not separator or key in values:
            _fail("Invalid or duplicate property parameter", "COMPLEX_TARGET")
        values[key] = value[1:-1] if value.startswith('"') and value.endswith('"') else value
    return values


def _date_view(line):
    params = _params(line)
    value = line.value
    if params.get("VALUE", "").upper() == "DATE" and re.fullmatch(r"\d{8}", value):
        return {"kind": "date", "value": f"{value[:4]}-{value[4:6]}-{value[6:]}"}
    if re.fullmatch(r"\d{8}T\d{6}Z?", value):
        iso = f"{value[:4]}-{value[4:6]}-{value[6:8]}T{value[9:11]}:{value[11:13]}:{value[13:]}"
        return {"kind": "utc-datetime" if value.endswith("Z") else "local-datetime", "value": iso,
                **({"tzid": params["TZID"]} if "TZID" in params else {})}
    return {"kind": "unsupported", "value": value, "params": line.params}


def _reason(document, event, uid_counts):
    uid = _uid(document, event)
    counts = Counter(document.lines[i].name for i in event.properties)
    if not uid or uid_counts[uid] != 1:
        return "Event requires a unique nonempty UID"
    if counts["DTSTAMP"] != 1:
        return "Event requires exactly one DTSTAMP"
    if any(counts[name] > 1 for name in SINGLE_FIELDS):
        return "Event contains duplicate singleton properties"
    if any(counts[name] for name in RECURRENCE):
        return "Recurring events and recurrence exceptions are unsupported"
    if any(child != "VALARM" for child in event.children):
        return "Event contains unsupported child components"
    if counts["DTEND"] and counts["DURATION"]:
        return "DTEND and DURATION cannot coexist"
    return None


def _event_view(document, event, uid_counts):
    value = {"uid": _uid(document, event), "components": event.children}
    for name in sorted(TEXT_FIELDS):
        indices = _properties(document, event, name)
        value[name.lower()] = decode_text(document.lines[indices[0]].value) if len(indices) == 1 else None
    for name in ("DTSTART", "DTEND"):
        indices = _properties(document, event, name)
        value["start" if name == "DTSTART" else "end"] = _date_view(document.lines[indices[0]]) if len(indices) == 1 else None
    reason = _reason(document, event, uid_counts)
    value["editable"] = reason is None
    if reason:
        value["reason"] = reason
    value["properties"] = [
        {"name": document.lines[i].name, "params": document.lines[i].params, "value": document.lines[i].value}
        for i in event.properties
    ]
    return value


def summary(document) -> dict:
    events = _events(document)
    counts = Counter(_uid(document, event) for event in events)
    items = []
    for event in events[:200]:
        view = _event_view(document, event, counts)
        items.append({key: view[key] for key in ("uid", "summary", "start", "end", "editable")})
    return {"event_count": len(events), "events": items, "truncated": len(events) > 200}


def _select(document, uid):
    if not isinstance(uid, str) or not uid:
        _fail("uid must be a nonempty string", "INVALID_STEP")
    events = _events(document)
    selected = [event for event in events if _uid(document, event) == uid]
    if len(selected) != 1:
        _fail(f"Expected one event with UID {uid}; found {len(selected)}", "EVENT_NOT_UNIQUE")
    return selected[0], Counter(_uid(document, event) for event in events)


def inspect(document, pointer: str = "") -> dict:
    if not isinstance(pointer, str):
        _fail("pointer must be a string", "INVALID_POINTER")
    if not pointer:
        return {"pointer": pointer, "value": summary(document)}
    parts = _pointer_parts(pointer)
    if len(parts) != 2 or parts[0] != "events":
        _fail("Use /events/<escaped-uid>", "INVALID_POINTER")
    event, counts = _select(document, parts[1])
    return {"pointer": pointer, "value": _event_view(document, event, counts)}


def _argument_date(value):
    if not isinstance(value, str):
        _fail("start and end must be date or UTC datetime strings", "INVALID_STEP")
    try:
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            return "date", date.fromisoformat(value), value.replace("-", "")
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value):
            return "utc-datetime", datetime.fromisoformat(value), value.replace("-", "").replace(":", "")
    except ValueError as exc:
        raise FileEditError("INVALID_TIME", str(exc)) from exc
    _fail("Use YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ", "INVALID_TIME")


def _existing_date(line):
    params = _params(line)
    if "TZID" in params:
        _fail("Rescheduling TZID-based events is unsupported", "UNSUPPORTED_TIME")
    view = _date_view(line)
    if view["kind"] not in {"date", "utc-datetime"}:
        _fail("Rescheduling requires existing DATE or UTC DTSTART/DTEND", "UNSUPPORTED_TIME")
    expected_value = "DATE" if view["kind"] == "date" else "DATE-TIME"
    if params.get("VALUE", expected_value).upper() != expected_value:
        _fail("Unsupported DTSTART/DTEND VALUE parameter", "UNSUPPORTED_TIME")
    return _argument_date(view["value"])


def edit(document, step: dict):
    if not isinstance(step, dict) or not isinstance(step.get("op"), str) or step["op"] not in OPS:
        _fail("Unsupported ICS operation", "INVALID_STEP")
    op = step["op"]
    fields = {"op", "uid", "field", "value"} if op == "set-event-field" else {"op", "uid", "start", "end"}
    _fields(step, fields, fields)
    event, counts = _select(document, step["uid"])
    reason = _reason(document, event, counts)
    if reason:
        _fail(reason, "COMPLEX_TARGET")
    replacements = {}
    additions = []
    if op == "set-event-field":
        name, value = step["field"], step["value"]
        if not isinstance(name, str) or name not in TEXT_FIELDS or not isinstance(value, str):
            _fail("field must be SUMMARY, DESCRIPTION or LOCATION and value must be a string", "INVALID_STEP")
        encoded = encode_text(value)
        indices = _properties(document, event, name)
        if indices:
            line = document.lines[indices[0]]
            params = _params(line)
            if "ALTREP" in params or params.get("VALUE", "TEXT").upper() != "TEXT":
                _fail("Existing field has an alternate or non-TEXT representation", "COMPLEX_TARGET")
            replacements[indices[0]] = replace_value(line, encoded, document.newline)
        else:
            additions.append(property_line(name, encoded, document.newline))
    else:
        if _properties(document, event, "DURATION"):
            _fail("DURATION-based events require a duration-aware operation", "COMPLEX_TARGET")
        starts, ends = _properties(document, event, "DTSTART"), _properties(document, event, "DTEND")
        if len(starts) != 1:
            _fail("Rescheduling requires one existing DTSTART", "COMPLEX_TARGET")
        old_start = _existing_date(document.lines[starts[0]])
        start, end = _argument_date(step["start"]), _argument_date(step["end"])
        if start[0] != end[0] or start[0] != old_start[0]:
            _fail("start and end must retain the event's DATE or UTC datetime type", "INVALID_TIME")
        if end[1] <= start[1]:
            _fail("Exclusive end must be later than start", "INVALID_TIME")
        if ends and _existing_date(document.lines[ends[0]])[0] != old_start[0]:
            _fail("Original DTSTART and DTEND have different value types", "INVALID_TIME")
        replacements[starts[0]] = replace_value(document.lines[starts[0]], start[2], document.newline)
        if ends:
            replacements[ends[0]] = replace_value(document.lines[ends[0]], end[2], document.newline)
        else:
            additions.append(property_line("DTEND", end[2], document.newline, params=";VALUE=DATE" if end[0] == "date" else ""))
    result = deepcopy(document)
    for index, line in replacements.items():
        result.lines[index] = line
    # Event properties must precede VALARM children.
    insert_at = next((i for i in range(event.start + 1, event.end) if result.lines[i].name == "BEGIN"), event.end)
    result.lines[insert_at:insert_at] = additions
    _events(result)
    return result


def serialize(document) -> bytes:
    return document.serialize()
