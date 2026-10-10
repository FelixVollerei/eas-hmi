"""Exercise each daily-file profile through the public CLI and fixed sample plans."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sfe_files.cli import app

ROOT = Path(__file__).resolve().parents[1]
runner = CliRunner()


def invoke(*args, code=0):
    result = runner.invoke(app, list(map(str, args)))
    assert result.exit_code == code, (result.stdout, result.exception)
    return json.loads(result.stdout)


@pytest.mark.parametrize(("name", "pointer"), [
    ("captions.srt", "/cues/2"),
    ("subtitles.vtt", "/cues/2"),
    ("schedule.ics", "/events/review-1@example.test"),
    ("contacts.vcf", "/records/1"),
    ("records.jsonl", "/by-key/id/P-01/status"),
])
def test_daily_profile_public_workflow(tmp_path, name, pointer):
    source = ROOT / "examples/sfe" / name
    plan = source.with_suffix(".plan.json")
    original = source.read_bytes()
    output = tmp_path / name
    before = invoke("status", source)
    invoke("inspect", source, "--pointer", pointer)
    preview = invoke("preview", source, plan)
    assert preview["diff"] and not preview["committed"]
    assert not output.exists() and source.read_bytes() == original
    applied = invoke("apply", source, plan, "--output", output)
    after = invoke("status", output)
    invoke("inspect", output, "--pointer", pointer)
    assert preview["before_sha256"] == before["sha256"]
    assert preview["after_sha256"] == applied["after_sha256"] == after["sha256"]
    assert original == source.read_bytes()
    rejected = tmp_path / ("rejected-" + name)
    stale = invoke("apply", output, plan, "--output", rejected, code=2)
    assert stale["code"] == "BASE_CONFLICT" and not rejected.exists()
    invalid = json.loads(plan.read_bytes())
    invalid["steps"].append({"op": "unsupported"})
    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text(json.dumps(invalid), encoding="utf-8")
    failed = invoke("apply", source, invalid_path, "--output", rejected, code=2)
    assert failed["code"] == "INVALID_STEP" and not rejected.exists()
    assert original == source.read_bytes()


def test_jsonl_extension_alias_and_no_phantom_bom_in_preview(tmp_path):
    source = tmp_path / "records.ndjson"
    sample = ROOT / "examples/sfe/records.jsonl"
    source.write_bytes(sample.read_bytes())
    assert invoke("status", source)["format"] == "jsonl"
    output = tmp_path / "out.jsonl"
    invoke("apply", source, sample.with_suffix(".plan.json"), "--output", output)
    assert invoke("inspect", output, "--pointer", "/records/1/status")["value"] == "ready"
    sample = ROOT / "examples/sfe/captions.srt"
    preview = invoke("preview", sample, sample.with_suffix(".plan.json"))
    assert "\ufeff" not in preview["diff"]
