import json
from pathlib import Path
from unittest.mock import patch

import pytest
from filelock import Timeout
from lxml import etree
from typer.testing import CliRunner

from eas_hmi.cli import app
from eas_hmi.model import Equipment, Point, Template
from eas_hmi.svg.synchronizer import frame_of

runner = CliRunner()


@pytest.fixture
def cli_project(small_project, tmp_path):
    small_project.equipment = [Equipment(id="PUMP_07", name="Synthetic pump 07", type="pump")]
    small_project.points = [
        Point(id="P07_FLT", tag="SYN.P07.FAULT", datatype="bool", role="fault", equipment_ref="PUMP_07")
    ]
    small_project.templates = [Template(id="PUMP", required_bindings=["fault"])]
    small_project.pages[0].nodes[0].equipment_ref = "PUMP_07"
    small_project.pages[0].nodes[0].template_ref = "PUMP"
    source, project = tmp_path / "source.json", tmp_path / "project"
    source.write_text(small_project.model_dump_json(), encoding="utf-8")
    result = runner.invoke(app, ["--project", str(project), "init", "--from-model", str(source)])
    assert result.exit_code == 0, result.output
    return project


def invoke(path, *args, code=0):
    result = runner.invoke(app, ["--project", str(path), *map(str, args)])
    assert result.exit_code == code, (result.output, result.exception)
    return json.loads(result.stdout)


def test_installed_cli_help_and_stage_scope():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("init", "inspect", "move", "render", "sync-from-svg", "undo"):
        assert command in result.stdout
    assert "export-manifest" in result.stdout  # Added in stage 4; earlier commands remain available.


def test_empty_init_and_read_commands(tmp_path):
    project = tmp_path / "empty"
    assert invoke(project, "init")["revision"] == 0
    assert invoke(project, "status", "--json")["nodes"] == 0
    assert invoke(project, "query", "--json") == {"count": 0, "nodes": []}
    assert invoke(project, "validate", "--json")["ok"]
    assert invoke(project, "init", code=2)["code"] == "PROJECT_EXISTS"
    assert invoke(project, "undo", code=2)["code"] == "NOTHING_TO_UNDO"


def test_binding_validation_single_edits_and_diff(cli_project):
    p = cli_project
    assert (
        invoke(p, "validate", "--strict", "--json", code=2)["issues"][0]["code"] == "MISSING_REQUIRED_BINDING"
    )
    assert (
        invoke(p, "query", "--kind", "shape", "--equipment", "PUMP_07", "--missing-binding", "fault")["count"]
        == 1
    )
    assert invoke(p, "bind", "BOX_A", "fault", "P07_FLT")["revision"] == 1
    assert invoke(p, "validate", "--strict")["ok"]
    assert invoke(p, "inspect", "P07_FLT")["references"] == ["BOX_A"]
    before = invoke(p, "status")["canonical_hash"]
    assert (
        invoke(p, "--actor", "human", "--expected-revision", "1", "move", "BOX_A", "--dx", "40")["revision"]
        == 2
    )
    assert invoke(p, "inspect", "BOX_A")["target"]["x"] == 70
    latest = invoke(p, "diff", "--json")
    assert latest["actor"] == "human"
    assert latest["changes"][0]["changes"]["geometry.x"] == {"old": 30, "new": 70}
    human = runner.invoke(app, ["--project", str(p), "diff"])
    assert human.exit_code == 0 and "geometry.x" in human.stdout and "30.0 -> 70.0" in human.stdout
    assert invoke(p, "--json", "diff", "--revision", "1")["revision_after"] == 1
    assert invoke(p, "undo")["revision"] == 3
    assert invoke(p, "status")["canonical_hash"] == before
    assert invoke(p, "unbind", "BOX_A", "fault")["revision"] == 4
    assert len(invoke(p, "history", "--limit", "2")["operations"]) == 2


def test_schema_reference_and_stale_edits_leave_state_unchanged(cli_project):
    p = cli_project
    original = invoke(p, "status")
    bad_commands = [
        (["set", "BOX_A", "width", "-5"], "VALIDATION_FAILED", 2),
        (["set", "BOX_A", "metadata.bad", "NaN"], "VALIDATION_FAILED", 2),
        (["set", "BOX_A", "equipment_ref", "unknown"], "VALIDATION_FAILED", 2),
        (["set", "BOX_A", "id", "OTHER"], "PROPERTY_NOT_EDITABLE", 2),
        (["bind", "BOX_A", "fault", "unknown"], "INVALID_POINT_REFERENCE", 2),
        (["--expected-revision", "9", "move", "BOX_A", "--dx", "1"], "REVISION_CONFLICT", 3),
        (["--actor", "someone", "move", "BOX_A", "--dx", "1"], "INVALID_ACTOR", 2),
    ]
    for args, error, code in bad_commands:
        assert invoke(p, *args, code=code)["code"] == error
        assert invoke(p, "status") == original
    assert len(invoke(p, "history")["operations"]) == 1


def test_render_sync_dryrun_commit_noop_stale_and_undo(cli_project, tmp_path):
    p = cli_project
    old_hash = invoke(p, "status")["canonical_hash"]
    svg = tmp_path / "semantic.svg"
    assert invoke(p, "render", "--output", svg)["revision"] == 0
    assert invoke(p, "sync-from-svg", svg)["committed"] is False
    root = etree.fromstring(svg.read_bytes())
    root.xpath('//*[@data-eas-id="BOX_A"]')[0].set("transform", "translate(20 10)")
    text = frame_of(root.xpath('//*[@data-eas-id="LABEL"]')[0])[0]
    text.text = "人类文本修改 · synthetic"
    svg.write_bytes(etree.tostring(root))
    preview = invoke(p, "sync-from-svg", svg, "--dry-run")
    assert preview["dry_run"] and not preview["committed"]
    assert invoke(p, "status")["canonical_hash"] == old_hash
    assert invoke(p, "sync-from-svg", svg)["revision"] == 1
    latest = invoke(p, "diff", "--json")
    assert latest["actor"] == "human" and latest["affected_ids"] == ["BOX_A", "LABEL"]
    assert invoke(p, "inspect", "LABEL")["target"]["text"] == text.text
    assert invoke(p, "sync-from-svg", svg, code=3)["code"] == "REVISION_CONFLICT"
    assert (
        invoke(p, "--expected-revision", "0", "sync-from-svg", svg, "--dry-run", code=3)["code"]
        == "REVISION_CONFLICT"
    )
    invoke(p, "undo")
    assert invoke(p, "status")["canonical_hash"] == old_hash
    assert len(invoke(p, "history")["operations"]) == 3


def test_resize_set_lock_and_automatic_render(cli_project):
    p = cli_project
    result = invoke(p, "--auto-render", "resize", "BOX_A", "--width", "180", "--height", "80")
    assert result["rendered_revision"] == 1 and Path(result["rendered"][0]).is_file()
    invoke(p, "set", "BOX_A", "locked", "true")
    assert invoke(p, "move", "BOX_A", "--dx", "1", code=2)["code"] == "NODE_LOCKED"
    invoke(p, "set", "BOX_A", "locked", "false")
    invoke(p, "set", "BOX_A", "metadata.note", '"reviewed"')
    assert invoke(p, "inspect", "BOX_A")["target"]["metadata"]["note"] == "reviewed"
    with patch("eas_hmi.cli.render_svg", side_effect=OSError("raster-independent SVG output failure")):
        result = invoke(p, "--auto-render", "move", "BOX_A", "--dx", "1")
    assert result["committed"] and result["render_failed"]
    assert "Committed" in result["warnings"][0]
    assert invoke(p, "status")["revision"] == result["revision"]


def test_derived_output_cannot_overwrite_canonical_or_history(cli_project):
    for name in ("HEAD.json", ".eas/commits/x.svg", "history/operations.jsonl", ".eas.lock"):
        assert (
            invoke(cli_project, "render", "--output", cli_project / name, code=2)["code"]
            == "PROTECTED_PROJECT_PATH"
        )
    assert invoke(cli_project, "status")["revision"] == 0


def test_io_errors_missing_objects_and_lock_timeout_are_machine_readable(cli_project, tmp_path):
    assert invoke(tmp_path / "missing", "status", code=2)["code"] == "PROJECT_NOT_FOUND"
    assert invoke(cli_project, "sync-from-svg", tmp_path / "none.svg", code=4)["code"] == "IO_ERROR"
    assert invoke(cli_project, "inspect", "unknown", code=2)["code"] == "OBJECT_NOT_FOUND"
    assert invoke(cli_project, "diff", "--revision", "99", "--json", code=2)["code"] == "REVISION_NOT_FOUND"
    with patch("eas_hmi.cli.Store.load", side_effect=Timeout("lock")):
        assert invoke(cli_project, "status", code=4)["code"] == "PROJECT_BUSY"


def test_raw_invalid_geometry_fixture_can_be_validated(tmp_path):
    source = tmp_path / "broken.json"
    source.write_text('{"id":"bad","name":"bad","pages":[{"id":"p","name":"p","width":-1}]}')
    result = invoke(tmp_path, "validate", "--file", source, "--json", code=2)
    assert result["issues"][0]["code"] == "SCHEMA_VALIDATION"


def test_usage_errors_are_json(cli_project):
    assert invoke(cli_project, "move", "BOX_A", "--dx", "not-a-number", code=2)["code"] == "USAGE_ERROR"
    assert invoke(cli_project, "unknown-command", code=2)["code"] == "USAGE_ERROR"
    assert invoke(cli_project, "query", "--bad-option", "--json", code=2)["code"] == "USAGE_ERROR"
