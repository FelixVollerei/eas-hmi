"""iCalendar edits preserve component boundaries, event identity and untouched source."""

import json
from pathlib import Path

import pytest

from sfe_files import ics_profile as ics
from sfe_files.core import FileEditError, sha256

FIXTURE = Path(__file__).parents[1] / "examples" / "sfe" / "schedule.ics"
UID = "review-1@example.test"


def event_source(properties="", *, dates="DTSTART:20261001T090000Z\r\nDTEND:20261001T100000Z\r\n"):
    return (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//EAS//Tests//EN\r\n"
        "BEGIN:VEVENT\r\nUID:test@example.test\r\nDTSTAMP:20260926T090000Z\r\n"
        + dates + "SUMMARY:Original title\r\n" + properties + "END:VEVENT\r\nEND:VCALENDAR\r\n"
    ).encode()


def apply(source, *steps):
    document = ics.parse(source)
    for step in steps:
        document = ics.edit(document, step)
    return ics.serialize(document)


def test_reading_fixture_exposes_uid_titles_dates_and_noop_is_exact():
    source = FIXTURE.read_bytes()
    document = ics.parse(source)
    assert ics.serialize(document) == source
    assert ics.summary(document)["event_count"] == 2
    event = ics.inspect(document, "/events/review-1@example.test")["value"]
    assert event["summary"] == "Gateway commissioning review"
    assert event["start"] == {"kind": "utc-datetime", "value": "2026-10-01T02:00:00Z"}
    assert event["components"] == ["VALARM"]
    event = ics.inspect(document, "/events/handover~12~0ops@example.test")["value"]
    assert event["uid"] == "handover/2~ops@example.test"
    assert event["start"] == {
        "kind": "local-datetime", "value": "2026-10-02T10:00:00", "tzid": "Asia/Bangkok",
    }
    json.dumps(ics.summary(document), allow_nan=False)


def test_text_edit_preserves_timezone_alarms_unknown_properties_and_metadata():
    source = FIXTURE.read_bytes()
    result = apply(source, {"op": "set-event-field", "uid": UID, "field": "LOCATION", "value": "Room B"})
    assert result == source.replace(b"LOCATION:Room A", b"LOCATION:Room B")


def test_text_edit_on_timezone_event_keeps_dates_and_timezone_definition():
    source = FIXTURE.read_bytes()
    result = apply(source, {
        "op": "set-event-field", "uid": "handover/2~ops@example.test", "field": "SUMMARY", "value": "Final handover",
    })
    assert result == source.replace(b"SUMMARY:Operations handover", b"SUMMARY:Final handover")


def test_added_property_precedes_valarm_and_does_not_edit_alarm_description():
    source = event_source(
        "BEGIN:VALARM\r\nTRIGGER:-PT15M\r\nACTION:DISPLAY\r\nDESCRIPTION:Alarm text\r\nEND:VALARM\r\n"
    )
    result = apply(source, {
        "op": "set-event-field", "uid": "test@example.test", "field": "DESCRIPTION", "value": "Event text",
    })
    assert b"DESCRIPTION:Event text\r\nBEGIN:VALARM" in result
    assert b"DESCRIPTION:Alarm text\r\nEND:VALARM" in result


def test_escaped_multibyte_text_is_folded_by_octets_and_round_trips():
    text = "设备调试,phase;one\\path\n" * 8
    result = apply(event_source(), {
        "op": "set-event-field", "uid": "test@example.test", "field": "DESCRIPTION", "value": text,
    })
    assert b"\r\n " in result
    assert all(len(line) <= 75 for line in result.split(b"\r\n"))
    assert ics.inspect(ics.parse(result), "/events/test@example.test")["value"]["description"] == text
    assert b"\\," in result and b"\\;" in result and b"\\n" in result


def test_untouched_folding_lf_bom_and_parameter_spelling_preserved():
    source = event_source("DESCRIPTION:First part\r\n second part\r\nLOCATION;LANGUAGE=en:Room A\r\n")
    source = b"\xef\xbb\xbf" + source.replace(b"\r\n", b"\n")
    result = apply(source, {
        "op": "set-event-field", "uid": "test@example.test", "field": "LOCATION", "value": "Room B",
    })
    assert result == source.replace(b"LOCATION;LANGUAGE=en:Room A", b"LOCATION;LANGUAGE=en:Room B")


def test_reschedule_utc_preserves_alarm_and_determinism():
    source = event_source(
        "SEQUENCE:4\r\nLAST-MODIFIED:20260926T090000Z\r\nBEGIN:VALARM\r\n"
        "TRIGGER;VALUE=DATE-TIME:20261001T080000Z\r\nACTION:DISPLAY\r\nDESCRIPTION:Fixed alarm\r\nEND:VALARM\r\n"
    )
    step = {"op": "reschedule", "uid": "test@example.test", "start": "2026-10-03T12:00:00Z", "end": "2026-10-03T13:30:00Z"}
    result = apply(source, step)
    expected = source.replace(b"DTSTART:20261001T090000Z", b"DTSTART:20261003T120000Z")
    expected = expected.replace(b"DTEND:20261001T100000Z", b"DTEND:20261003T133000Z")
    assert result == expected
    assert apply(source, step) == result


def test_all_day_reschedule_has_exclusive_end_and_can_insert_dtend():
    source = event_source(dates="DTSTART;VALUE=DATE:20261001\r\n")
    result = apply(source, {"op": "reschedule", "uid": "test@example.test", "start": "2026-10-04", "end": "2026-10-05"})
    assert b"DTSTART;VALUE=DATE:20261004\r\n" in result
    assert b"DTEND;VALUE=DATE:20261005\r\n" in result
    event = ics.inspect(ics.parse(result), "/events/test@example.test")["value"]
    assert event["end"] == {"kind": "date", "value": "2026-10-05"}


@pytest.mark.parametrize("property", [
    "RRULE:FREQ=DAILY", "RDATE:20261003T090000Z", "EXDATE:20261002T090000Z",
    "RECURRENCE-ID:20261001T090000Z", "EXRULE:FREQ=DAILY",
])
def test_recurring_events_are_inspectable_but_edits_rejected(property):
    source = event_source(property + "\r\n")
    document = ics.parse(source)
    assert ics.inspect(document, "/events/test@example.test")["value"]["editable"] is False
    with pytest.raises(FileEditError) as exc:
        ics.edit(document, {"op": "set-event-field", "uid": "test@example.test", "field": "SUMMARY", "value": "x"})
    assert exc.value.code == "COMPLEX_TARGET"
    assert ics.serialize(document) == source


def test_repeated_uid_rejected_without_picking_one_occurrence():
    source = event_source()
    event = source[source.index(b"BEGIN:VEVENT"):source.index(b"END:VEVENT") + len(b"END:VEVENT\r\n")]
    source = source.replace(b"END:VCALENDAR", event + b"END:VCALENDAR")
    document = ics.parse(source)
    assert ics.summary(document)["event_count"] == 2
    assert all(not item["editable"] for item in ics.summary(document)["events"])
    with pytest.raises(FileEditError) as exc:
        ics.inspect(document, "/events/test@example.test")
    assert exc.value.code == "EVENT_NOT_UNIQUE"


@pytest.mark.parametrize("properties", [
    "SUMMARY:Duplicate\r\n", "DTEND:20261001T110000Z\r\n", "DURATION:PT1H\r\n",
    "BEGIN:VTODO\r\nSUMMARY:Nested task\r\nEND:VTODO\r\n",
])
def test_complex_target_edits_are_rejected(properties):
    source = event_source(properties)
    document = ics.parse(source)
    with pytest.raises(FileEditError) as exc:
        ics.edit(document, {"op": "set-event-field", "uid": "test@example.test", "field": "SUMMARY", "value": "x"})
    assert exc.value.code == "COMPLEX_TARGET"
    assert ics.serialize(document) == source


@pytest.mark.parametrize("start,end", [
    ("2026-10-01T10:00:00Z", "2026-10-01T09:00:00Z"),
    ("2026-10-01T09:00:00Z", "2026-10-01T09:00:00Z"),
    ("2026-10-01", "2026-10-02"),
    ("2026-10-01T09:00:00+07:00", "2026-10-01T10:00:00+07:00"),
    ("2026-02-30T09:00:00Z", "2026-10-01T10:00:00Z"),
    ("2026-10-01T09:00:00Z", "2026-10-02"),
])
def test_invalid_times_order_and_type_changes_are_rejected(start, end):
    source = event_source()
    document = ics.parse(source)
    with pytest.raises(FileEditError) as exc:
        ics.edit(document, {"op": "reschedule", "uid": "test@example.test", "start": start, "end": end})
    assert exc.value.code == "INVALID_TIME"
    assert ics.serialize(document) == source


@pytest.mark.parametrize("dates", [
    "DTSTART;TZID=Asia/Bangkok:20261001T090000\r\nDTEND;TZID=Asia/Bangkok:20261001T100000\r\n",
    "DTSTART:20261001T090000\r\nDTEND:20261001T100000\r\n",
])
def test_local_and_floating_times_cannot_be_rescheduled(dates):
    document = ics.parse(event_source(dates=dates))
    with pytest.raises(FileEditError) as exc:
        ics.edit(document, {"op": "reschedule", "uid": "test@example.test", "start": "2026-10-01T09:00:00Z", "end": "2026-10-01T10:00:00Z"})
    assert exc.value.code == "UNSUPPORTED_TIME"


def test_duration_event_reschedule_rejected_without_adding_conflicting_dtend():
    source = event_source(dates="DTSTART:20261001T090000Z\r\nDURATION:PT1H\r\n")
    document = ics.parse(source)
    with pytest.raises(FileEditError) as exc:
        ics.edit(document, {"op": "reschedule", "uid": "test@example.test", "start": "2026-10-02T09:00:00Z", "end": "2026-10-02T10:00:00Z"})
    assert exc.value.code == "COMPLEX_TARGET"
    assert ics.serialize(document) == source


def test_alternate_representation_cannot_be_silently_made_stale():
    source = event_source('DESCRIPTION;ALTREP="https://example.test/event;1":Description\r\n')
    with pytest.raises(FileEditError) as exc:
        apply(source, {"op": "set-event-field", "uid": "test@example.test", "field": "DESCRIPTION", "value": "New"})
    assert exc.value.code == "COMPLEX_TARGET"


@pytest.mark.parametrize("step", [
    {"op": "set-event-field", "uid": "test@example.test", "field": "ATTENDEE", "value": "x"},
    {"op": "set-event-field", "uid": "test@example.test", "field": "SUMMARY", "value": None},
    {"op": "set-event-field", "uid": "test@example.test", "field": "SUMMARY", "value": "x", "extra": 1},
    {"op": "reschedule", "uid": "test@example.test", "start": None, "end": "2026-10-02"},
])
def test_exact_step_validation(step):
    with pytest.raises(FileEditError) as exc:
        apply(event_source(), step)
    assert exc.value.code == "INVALID_STEP"


@pytest.mark.parametrize("change", [
    lambda source: source.replace(b"END:VEVENT", b"END:VTODO"),
    lambda source: source.replace(b"VERSION:2.0", b"VERSION:1.0"),
    lambda source: source + source,
    lambda source: source.replace(b"END:VCALENDAR\r\n", b""),
])
def test_invalid_calendar_structure_rejected(change):
    with pytest.raises(FileEditError) as exc:
        ics.parse(change(event_source()))
    assert exc.value.code == "INVALID_ICS"


def test_example_plan_matches_and_executes():
    source = FIXTURE.read_bytes()
    plan = json.loads(FIXTURE.with_name("schedule.plan.json").read_bytes())
    assert plan["base"]["sha256"] == sha256(source)
    document = ics.parse(apply(source, *plan["steps"]))
    event = ics.inspect(document, "/events/review-1@example.test")["value"]
    assert event["location"] == "Room B"
    assert event["start"]["value"] == "2026-10-01T03:00:00Z"
    assert event["end"]["value"] == "2026-10-01T04:00:00Z"
    assert event["description"] == "Confirm sensor mapping, record alarm checks; share the local report."


def test_nested_alarm_structure_is_rejected():
    source = event_source("BEGIN:VALARM\r\nBEGIN:VALARM\r\nEND:VALARM\r\nEND:VALARM\r\n")
    with pytest.raises(FileEditError) as exc:
        ics.parse(source)
    assert exc.value.code == "INVALID_ICS"
