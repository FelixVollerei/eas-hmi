"""The new-file guarantee must survive another writer racing with apply."""

from pathlib import Path

import pytest

from sfe_files import service
from sfe_files.plan_ops import FileEditError

ROOT = Path(__file__).resolve().parents[1]


def test_apply_does_not_replace_output_created_after_precheck(tmp_path, monkeypatch):
    samples = ROOT / "examples" / "sfe"
    source = tmp_path / "settings.json"
    source.write_bytes((samples / source.name).read_bytes())
    plan = (samples / "settings.plan.json").read_bytes()
    output = tmp_path / "edited.json"
    original_prepare = service.prepare

    def competing_writer(*args):
        candidate = original_prepare(*args)
        output.write_bytes(b"another writer's file")
        return candidate

    monkeypatch.setattr(service, "prepare", competing_writer)
    with pytest.raises(FileEditError) as caught:
        service.apply_data(source, plan, output)
    assert caught.value.code == "OUTPUT_EXISTS"
    assert output.read_bytes() == b"another writer's file"
    assert not list(tmp_path.glob(".edited.json.*.tmp"))


def test_cleanup_failure_after_publication_reports_committed(tmp_path, monkeypatch):
    samples = ROOT / "examples" / "sfe"
    source = tmp_path / "settings.json"
    source.write_bytes((samples / source.name).read_bytes())
    plan = (samples / "settings.plan.json").read_bytes()
    output = tmp_path / "edited.json"
    expected = service.prepare(source, source.read_bytes(), plan).output
    original_unlink = Path.unlink

    def reject_temp_cleanup(path, *args, **kwargs):
        if path.suffix == ".tmp":
            raise PermissionError("simulated cleanup denial")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", reject_temp_cleanup)
    result = service.apply_data(source, plan, output)

    assert result["ok"] and result["committed"]
    assert result["warnings"] == [
        "Output published; temporary-file cleanup failed: simulated cleanup denial"
    ]
    assert output.read_bytes() == expected
