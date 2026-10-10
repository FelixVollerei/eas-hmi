"""End-to-end Office editing uses byte hashes and preserves unrelated ZIP parts."""

import io
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from typer.testing import CliRunner

from sfe_files.cli import app
from sfe_files.core import FileEditError, format_for

ROOT = Path(__file__).resolve().parents[1]
runner = CliRunner()


def invoke(*args, code=0):
    result = runner.invoke(app, list(map(str, args)))
    assert result.exit_code == code, (result.stdout, result.exception)
    return json.loads(result.stdout)


@pytest.mark.parametrize("name", ["budget.xlsx", "meeting.docx", "project.pptx"])
def test_office_preview_apply_and_failed_batch(tmp_path, name):
    sample = ROOT / "examples/sfe" / name
    plan = sample.with_suffix(".plan.json")
    source = tmp_path / name
    original = sample.read_bytes()
    source.write_bytes(original)
    output = tmp_path / ("edited-" + name)
    before = invoke("status", source)
    invoke("inspect", source)
    preview = invoke("preview", source, plan)
    assert source.read_bytes() == original and not output.exists()
    assert preview["diff_kind"] == "semantic" and preview["diff"]
    applied = invoke("apply", source, plan, "--output", output)
    after = invoke("status", output)
    invoke("inspect", output)
    assert preview["before_sha256"] == before["sha256"]
    assert preview["after_sha256"] == applied["after_sha256"] == after["sha256"]
    assert source.read_bytes() == original
    parts = preview["package_parts"]
    assert parts["modified"]
    with ZipFile(io.BytesIO(original)) as old, ZipFile(output) as new:
        untouched = set(old.namelist()) - set(parts["modified"]) - set(parts["removed"])
        assert untouched and all(old.read(part) == new.read(part) for part in untouched)
    rejected = tmp_path / ("rejected-" + name)
    stale = invoke("apply", output, plan, "--output", rejected, code=2)
    assert stale["code"] == "BASE_CONFLICT" and not rejected.exists()
    bad_plan = json.loads(plan.read_text(encoding="utf-8"))
    bad_plan["steps"].append({"op": "unsupported"})
    bad_path = tmp_path / "bad-plan.json"
    bad_path.write_text(json.dumps(bad_plan), encoding="utf-8")
    failed = invoke("apply", source, bad_path, "--output", rejected, code=2)
    assert failed["code"] == "INVALID_STEP" and not rejected.exists()
    assert source.read_bytes() == original


@pytest.mark.parametrize("extension", [".xls", ".doc", ".ppt"])
def test_legacy_office_error_explains_conversion(extension):
    with pytest.raises(FileEditError) as caught:
        format_for(Path("legacy" + extension))
    assert caught.value.code == "LEGACY_OFFICE_FORMAT"
    assert "Renaming" in str(caught.value)


def test_renamed_binary_office_is_not_ooxml(tmp_path):
    source = tmp_path / "encrypted.docx"
    source.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + bytes(32))
    assert invoke("status", source, code=2)["code"] == "UNSUPPORTED_OFFICE_CONTAINER"
