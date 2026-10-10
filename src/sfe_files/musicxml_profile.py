"""Bounded MusicXML 4.0 note edits with measure-duration validation."""

from __future__ import annotations

import math
from fractions import Fraction

from .plan_ops import FileEditError, _fields, _pointer_parts
from .xml_document import read_xml

OPS = {"set-pitch", "set-duration", "set-dynamics"}
TYPES = {"whole": Fraction(4), "half": Fraction(2), "quarter": Fraction(1),
         "eighth": Fraction(1, 2), "16th": Fraction(1, 4)}
CONTRACT = {
    "addressing": "Inspect /parts/P1/measures/1/voices/1/notes/2; measure number and part ID come from XML.",
    "operations": {
        "set-pitch": {"example": {"op": "set-pitch", "part": "P1", "measure": "1", "voice": "1", "note": 2, "step": "F", "octave": 4, "alter": 0}},
        "set-duration": {"example": {"op": "set-duration", "part": "P1", "measure": "1", "voice": "1", "note": 2, "duration": 8, "type": "half"}},
        "set-dynamics": {"example": {"op": "set-dynamics", "part": "P1", "measure": "1", "voice": "1", "note": 2, "value": 78}},
    },
    "limitations": [
        "MusicXML 4.0 score-partwise, ordinary monophonic measures with inherited divisions and time signature.",
        "No chords, tuplets, grace/cue notes, ties, backups or forwards in edited measures.",
        "set-duration requires matching symbolic type; whole measure duration is validated after all plan steps.",
        "set-dynamics changes the note playback dynamics percentage, not printed expressive marks.",
        "Other score content is retained; XML formatting may normalize. No audio synthesis or score rendering check.",
    ],
    "reference": "https://www.w3.org/2021/06/musicxml40/",
}


def _bad(message, code="INVALID_MUSICXML"):
    raise FileEditError(code, message)


def parse(data):
    document = read_xml(data, code="INVALID_MUSICXML")
    if document.root.tag != "score-partwise" or document.root.get("version") != "4.0":
        _bad("Expected MusicXML 4.0 score-partwise")
    document.edited_measures = set()
    return document


def _part(document, ident):
    found = [x for x in document.root.findall("part") if x.get("id") == ident]
    if len(found) != 1:
        _bad("Part ID not found or ambiguous", "TARGET_NOT_UNIQUE")
    return found[0]


def _measure(part, number):
    found = [x for x in part.findall("measure") if x.get("number") == number]
    if len(found) != 1:
        _bad("Measure number not found or ambiguous", "TARGET_NOT_UNIQUE")
    return found[0]


def _notes(measure, voice):
    return [x for x in measure.findall("note") if (x.findtext("voice") or "1") == voice]


def _note(document, part, measure, voice, index):
    if any(not isinstance(x, str) or not x for x in (part, measure, voice)) or type(index) is not int:
        _bad("part, measure, voice and note address are required", "INVALID_STEP")
    notes = _notes(_measure(_part(document, part), measure), voice)
    if not 1 <= index <= len(notes):
        _bad("Note number out of range", "TARGET_NOT_FOUND")
    return notes[index - 1]


def _view(note):
    pitch = note.find("pitch")
    return {"pitch": ({"step": pitch.findtext("step"), "alter": int(pitch.findtext("alter") or "0"),
                       "octave": int(pitch.findtext("octave"))} if pitch is not None else None),
            "duration": int(note.findtext("duration")), "type": note.findtext("type"),
            "dynamics": note.get("dynamics"), "voice": note.findtext("voice") or "1"}


def summary(document):
    return {"parts": [{"id": part.get("id"), "measures": len(part.findall("measure"))}
                      for part in document.root.findall("part")]}


def inspect(document, pointer=""):
    parts = _pointer_parts(pointer)
    if not parts:
        return {"pointer": pointer, "value": summary(document)}
    if (len(parts) != 8 or parts[0] != "parts" or parts[2] != "measures" or parts[4] != "voices"
            or parts[6] != "notes" or not parts[7].isdigit()):
        _bad("Use /parts/<id>/measures/<number>/voices/<id>/notes/<number>", "INVALID_INSPECT")
    return {"pointer": pointer, "value": _view(_note(document, parts[1], parts[3], parts[5], int(parts[7])))}


def projection(document):
    return {"parts": {part.get("id"): {measure.get("number"): [_view(note) for note in measure.findall("note")]
                                     for measure in part.findall("measure")}
                      for part in document.root.findall("part")}}


def _measure_context(part, measure):
    divisions = time = None
    for current in part.findall("measure"):
        attrs = current.find("attributes")
        if attrs is not None:
            if attrs.findtext("divisions") is not None:
                divisions = int(attrs.findtext("divisions"))
            tempo = attrs.find("time")
            if tempo is not None:
                time = (int(tempo.findtext("beats")), int(tempo.findtext("beat-type")))
        if current is measure:
            break
    if divisions is None or divisions <= 0 or time is None:
        _bad("Measure needs inherited divisions and time signature", "UNSUPPORTED_MUSICXML")
    return divisions, time


def _validate_measure(part, measure):
    if any(measure.find(tag) is not None for tag in ("backup", "forward")):
        _bad("Polyphonic measure timing is unsupported", "UNSUPPORTED_MUSICXML")
    divisions, (beats, beat_type) = _measure_context(part, measure)
    notes = measure.findall("note")
    if not notes or len({note.findtext("voice") or "1" for note in notes}) != 1:
        _bad("Edited measure must have one voice", "UNSUPPORTED_MUSICXML")
    total = 0
    for note in notes:
        if any(note.find(tag) is not None for tag in ("chord", "grace", "cue", "time-modification", "tie", "beam")):
            _bad("Complex note timing is unsupported", "UNSUPPORTED_MUSICXML")
        if note.find("dot") is not None or note.find("notations/tied") is not None:
            _bad("Dots and ties are unsupported in edited measure", "UNSUPPORTED_MUSICXML")
        kind = note.findtext("type")
        duration = note.findtext("duration")
        if kind not in TYPES or duration is None or not duration.isdigit():
            _bad("Note needs a supported type and positive duration", "UNSUPPORTED_MUSICXML")
        expected = TYPES[kind] * divisions
        if int(duration) != expected:
            _bad("Note duration does not match its type and divisions", "INVALID_MUSICXML")
        total += int(duration)
    measure_length = Fraction(divisions * beats * 4, beat_type)
    if total != measure_length:
        _bad("Edited measure duration does not match time signature", "INVALID_MUSICXML")


def edit(document, step):
    op = step["op"]
    common = {"op", "part", "measure", "voice", "note"}
    additional = {"set-pitch": {"step", "octave", "alter"},
                  "set-duration": {"duration", "type"}, "set-dynamics": {"value"}}[op]
    _fields(step, common | additional, common | additional)
    part = _part(document, step["part"])
    measure = _measure(part, step["measure"])
    if not document.dirty:
        _validate_measure(part, measure)
    note = _note(document, step["part"], step["measure"], step["voice"], step["note"])
    if op == "set-pitch":
        pitch = note.find("pitch")
        if pitch is None:
            _bad("set-pitch needs a pitched note", "UNSUPPORTED_MUSICXML")
        if (not isinstance(step["step"], str) or step["step"] not in set("ABCDEFG")
                or type(step["octave"]) is not int or not 0 <= step["octave"] <= 9):
            _bad("Pitch step or octave is invalid", "INVALID_STEP")
        if type(step["alter"]) is not int or not -2 <= step["alter"] <= 2:
            _bad("alter must be an integer from -2 to 2", "INVALID_STEP")
        pitch.find("step").text = step["step"]
        pitch.find("octave").text = str(step["octave"])
        alter = pitch.find("alter")
        if alter is not None:
            alter.text = str(step["alter"])
        elif step["alter"]:
            from lxml import etree

            alter = etree.Element("alter")
            alter.text = str(step["alter"])
            pitch.insert(1, alter)
    elif op == "set-duration":
        duration, kind = step["duration"], step["type"]
        divisions, _ = _measure_context(part, measure)
        if type(duration) is not int or duration <= 0 or kind not in TYPES or duration != TYPES[kind] * divisions:
            _bad("duration and type must match the measure's divisions", "INVALID_STEP")
        note.find("duration").text = str(duration)
        note.find("type").text = kind
    else:
        value = step["value"]
        if type(value) not in {int, float} or not math.isfinite(value) or not 0 <= value <= 200:
            _bad("dynamics must be a finite percentage from 0 to 200", "INVALID_STEP")
        note.set("dynamics", str(value))
    document.dirty = True
    document.edited_measures.add((step["part"], step["measure"]))
    return document


def serialize(document):
    if document.dirty:
        for part_id, number in document.edited_measures:
            part = _part(document, part_id)
            _validate_measure(part, _measure(part, number))
    return document.serialize()
