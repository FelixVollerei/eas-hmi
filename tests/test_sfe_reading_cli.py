"""Exercise batch-two public CLI workflows and their atomic write contract."""

import io
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from typer.testing import CliRunner

from sfe_files.cli import app

ROOT = Path(__file__).resolve().parents[1] / "examples" / "sfe"
RUNNER = CliRunner()


def invoke(*args, code=0):
    result = RUNNER.invoke(app, list(map(str, args)))
    assert result.exit_code == code, (result.stdout, result.exception)
    return json.loads(result.stdout)


@pytest.mark.parametrize(("name", "pointer"), [
    ("notes.odt", "/paragraphs/2"),
    ("costs.ods", "/sheets/Costs/B3"),
    ("briefing.odp", "/slides/1/objects/status"),
    ("page.html", "/elements/status"),
    ("handbook.epub", "/chapters/review/elements/status"),
])
def test_batch_two_workflow(tmp_path, name, pointer):
    sample = ROOT / name
    plan = sample.with_suffix(".plan.json")
    source = tmp_path / name
    original = sample.read_bytes()
    source.write_bytes(original)
    output = tmp_path / ("edited-" + name)
    before = invoke("status", source)
    invoke("inspect", source, "--pointer", pointer)
    preview = invoke("preview", source, plan)
    assert preview["diff"] and not preview["committed"]
    assert source.read_bytes() == original and not output.exists()
    applied = invoke("apply", source, plan, "--output", output)
    after = invoke("status", output)
    invoke("inspect", output, "--pointer", pointer)
    assert preview["before_sha256"] == before["sha256"]
    assert preview["after_sha256"] == applied["after_sha256"] == after["sha256"]
    assert source.read_bytes() == original
    if name != "page.html":
        assert preview["diff_kind"] == "semantic"
        parts = preview["package_parts"]
        assert parts["modified"]
        with ZipFile(io.BytesIO(original)) as old, ZipFile(output) as new:
            untouched = set(old.namelist()) - set(parts["modified"]) - set(parts["removed"])
            assert untouched and all(old.read(part) == new.read(part) for part in untouched)
    rejected = tmp_path / ("rejected-" + name)
    stale = invoke("apply", output, plan, "--output", rejected, code=2)
    assert stale["code"] == "BASE_CONFLICT" and not rejected.exists()
    invalid = json.loads(plan.read_text(encoding="utf-8"))
    invalid["steps"].append({"op": "unsupported"})
    bad_path = tmp_path / "invalid.json"
    bad_path.write_text(json.dumps(invalid), encoding="utf-8")
    failed = invoke("apply", source, bad_path, "--output", rejected, code=2)
    assert failed["code"] == "INVALID_STEP" and not rejected.exists()
    assert source.read_bytes() == original


def test_html_htm_alias(tmp_path):
    source = tmp_path / "page.htm"
    source.write_bytes((ROOT / "page.html").read_bytes())
    assert invoke("status", source)["format"] == "html"
