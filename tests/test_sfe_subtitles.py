import json
from pathlib import Path

import pytest

from sfe_files import srt_profile as srt
from sfe_files import vtt_profile as vtt
from sfe_files.core import FileEditError, sha256

ROOT = Path(__file__).resolve().parents[1]


def test_srt_ordinal_preserves_number_bom_newline_and_untouched_block():
    untouched = b"20\r\n00:00:04,000 --> 00:00:07,000\r\nSecond cue\r\nwith another line"
    raw = b"\xef\xbb\xbf10\r\n00:00:01,000 --> 00:00:05,000\r\nFirst cue\r\n\r\n" + untouched
    document = srt.parse(raw)
    assert srt.serialize(document) == raw
    assert srt.inspect(document, "/cues/2")["value"]["number"] == "20"
    srt.edit(document, {"op": "set-text", "cue": 1, "text": "更新字幕\nSecond line"})
    srt.edit(document, {"op": "shift-time", "delta_ms": 250, "cues": [1]})
    output = srt.serialize(document)
    assert output.startswith(b"\xef\xbb\xbf10\r\n00:00:01,250 --> 00:00:05,250\r\n")
    assert output.endswith(untouched)
    assert "更新字幕\r\nSecond line\r\n\r\n".encode() in output
    assert srt.inspect(srt.parse(output), "/cues/1")["value"]["text"] == "更新字幕\nSecond line"


def test_vtt_metadata_settings_and_unselected_cue_bytes_retained():
    metadata = (
        b"WEBVTT Review\r\nX-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:900000\r\n\r\n"
        b"STYLE\r\n::cue { color: lime; }\r\n \r\n/* space line above is CSS content */\r\n\r\n"
        b"REGION\r\nid:bottom\r\nwidth:80%\r\n\r\n"
        b"NOTE  Keep this comment.\r\nIt mentions 00:01.000.\r\n\r\n"
    )
    cue1 = b"speaker-a\r\n00:01.000\t-->  00:05.000 region:bottom\talign:start\r\nHello\r\n\r\n"
    cue2 = b"00:04.000 --> 00:06.000 position:20%\r\nOverlap is allowed.\r\n"
    raw = metadata + cue1 + cue2
    document = vtt.parse(raw)
    assert vtt.summary(document)["cue_count"] == 2
    assert vtt.inspect(document, "/cues/1")["value"]["identifier"] == "speaker-a"
    assert vtt.inspect(document, "/cues/2")["value"]["identifier"] is None
    vtt.edit(document, {"op": "set-text", "cue": 1, "text": "Hello again."})
    vtt.edit(document, {"op": "set-timing", "cue": 1, "start_ms": 1250, "end_ms": 5250})
    output = vtt.serialize(document)
    assert output.startswith(metadata)
    assert output.endswith(cue2)
    assert b"00:01.250\t-->  00:05.250 region:bottom\talign:start\r\nHello again.\r\n\r\n" in output
    assert vtt.inspect(vtt.parse(output), "/cues/1")["value"]["settings"] == "region:bottom\talign:start"


def test_shift_all_is_validated_as_one_change_and_keeps_overlap():
    raw = b"WEBVTT\n\n00:01.000 --> 00:05.000\nA\n\n00:02.000 --> 00:06.000\nB\n"
    document = vtt.parse(raw)
    vtt.edit(document, {"op": "shift-time", "delta_ms": 5000})
    assert [(cue.start_ms, cue.end_ms) for cue in document.cues] == [(6000, 10000), (7000, 11000)]
    assert vtt.parse(vtt.serialize(document)).cues[0].end_ms == 10000


@pytest.mark.parametrize(
    "step",
    [
        {"op": "shift-time", "delta_ms": -1001},
        {"op": "shift-time", "delta_ms": 2000, "cues": [1]},
        {"op": "set-timing", "cue": 1, "start_ms": 2500, "end_ms": 4000},
        {"op": "set-timing", "cue": 1, "start_ms": 1000, "end_ms": 1000},
    ],
)
def test_vtt_negative_invalid_range_or_file_order_change_is_atomic(step):
    raw = b"WEBVTT\n\n00:01.000 --> 00:02.000\nA\n\n00:02.000 --> 00:03.000\nB\n"
    document = vtt.parse(raw)
    with pytest.raises(FileEditError, match="timing|nondecreasing"):
        vtt.edit(document, step)
    assert vtt.serialize(document) == raw
    assert document.cues[0].start_ms == 1000


@pytest.mark.parametrize(
    "step",
    [
        {"op": "shift-time", "delta_ms": 200},
        {"op": "set-timing", "cue": 1, "start_ms": 1100, "end_ms": 3100},
    ],
)
def test_vtt_inline_timestamps_reject_timing_edits(step):
    raw = b"WEBVTT\n\n00:01.000 --> 00:03.000\nReady <00:02.000>now\n"
    document = vtt.parse(raw)
    assert vtt.inspect(document, "/cues/1")["value"]["embedded_timestamps"]
    with pytest.raises(FileEditError, match="inline timestamp"):
        vtt.edit(document, step)
    assert vtt.serialize(document) == raw
    vtt.edit(document, {"op": "set-text", "cue": 1, "text": "Ready now"})
    vtt.edit(document, step)
    assert document.cues[0].start_ms > 1000


@pytest.mark.parametrize(
    "profile,raw",
    [
        (srt, b"1\n00:60:00,000 --> 00:61:00,000\nBad\n"),
        (srt, b"1\n00:00:00.000 --> 00:00:01.000\nWrong separator\n"),
        (srt, b"1\n00:00:00,00 --> 00:00:01,000\nMissing digit\n"),
        (vtt, b"WEBVTT\n\n00:61.000 --> 01:02.000\nBad\n"),
        (vtt, b"WEBVTT\n\n00:02.000 --> 00:04.000\nA\n\n00:01.000 --> 00:03.000\nB\n"),
        (vtt, b"WEBVTT\n00:01.000 --> 00:03.000\nNo blank after header\n"),
        (vtt, b"WEBVTT\n\n00:01.000 --> 00:03.000\nA\n\nSTYLE\n::cue {color:red;}\n"),
        (vtt, b"WEBVTT\n\n00:01.000 --> 00:03.000\nBad --> payload\n"),
    ],
)
def test_subtitle_strict_parse_rejects_invalid_inputs(profile, raw):
    with pytest.raises(FileEditError):
        profile.parse(raw)


@pytest.mark.parametrize(
    "step",
    [
        {"op": "set-text", "cue": 1, "text": "One\n\nTwo"},
        {"op": "set-text", "cue": 1, "text": "One\n \nTwo"},
        {"op": "set-text", "cue": 1, "text": "-->"},
        {"op": "set-text", "cue": 1, "text": "bad\x00text"},
        {"op": "set-text", "cue": 1, "text": "\ud800"},
        {"op": "shift-time", "delta_ms": True},
        {"op": "shift-time", "delta_ms": 1, "cues": [1, 1]},
        {"op": "shift-time", "delta_ms": 1, "cues": []},
        {"op": "shift-time", "delta_ms": 1, "cues": [True]},
        {"op": "set-timing", "cue": 1, "start_ms": 1.2, "end_ms": 3000},
        {"op": "set-text", "cue": 1, "text": "A", "extra": 1},
    ],
)
def test_subtitle_invalid_steps_cannot_break_block_syntax(step):
    raw = b"WEBVTT\n\n00:01.000 --> 00:03.000\nA\n"
    document = vtt.parse(raw)
    with pytest.raises(FileEditError):
        vtt.edit(document, step)
    assert vtt.serialize(document) == raw


def test_vtt_hour_rollover_and_srt_no_final_newline():
    document = vtt.parse(b"WEBVTT\n\n59:59.900 --> 01:00:00.900\nA")
    vtt.edit(document, {"op": "shift-time", "delta_ms": 200})
    assert b"01:00:00.100 --> 01:00:01.100" in vtt.serialize(document)
    assert not vtt.serialize(document).endswith(b"\n")
    document = srt.parse(b"9\r00:00:01,000 --> 00:00:03,000\rOld")
    srt.edit(document, {"op": "set-text", "cue": 1, "text": "New\nline"})
    assert srt.serialize(document) == b"9\r00:00:01,000 --> 00:00:03,000\rNew\rline"


def test_subtitle_published_plans_match_sources_and_roundtrip():
    for name, profile in (("captions.srt", srt), ("subtitles.vtt", vtt)):
        source = ROOT / "examples/sfe" / name
        raw = source.read_bytes()
        plan = json.loads(source.with_suffix(".plan.json").read_text(encoding="utf-8"))
        assert plan["base"]["sha256"] == sha256(raw)
        document = profile.parse(raw)
        for step in plan["steps"]:
            profile.edit(document, step)
        output = profile.serialize(document)
        after = profile.parse(output)
        assert after.cues[1].text == "The pump is ready for commissioning."
        assert after.cues[0].start_ms == 1250
        assert source.read_bytes() == raw
