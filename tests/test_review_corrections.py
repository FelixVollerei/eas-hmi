import json
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from eas_hmi.cli import app
from eas_hmi.errors import EngineeringError
from eas_hmi.model import Page
from eas_hmi.operations import edit
from eas_hmi.operations.transaction import Store
from eas_hmi.validation import validate


@pytest.fixture
def history_store(small_project, tmp_path):
    state = Store(tmp_path / "history")
    state.initialize(small_project)
    for _ in range(12):
        state.transaction("move", lambda p: edit.move(p, ["BOX_A"], dx=1))
    return state


def test_checked_projections_replace_historical_model_reads(history_store):
    state = history_store
    expected = state.history(audit=True)
    with patch.object(state, "_chain", side_effect=AssertionError("historical model read")):
        assert state.history(limit=3) == expected[-3:]
        assert state.history(revision=4) == [expected[4]]
        assert state.history(revision=99) == []
        events = state.events_since(10)
        assert [e["revision"] for e in events] == [11, 12]
        assert state.events_since(12) == []
        state.transaction("incremental publish", lambda p: edit.move(p, ["BOX_A"], dx=1))
    assert state.load().revision == 13


def test_idle_events_read_only_current_commit_and_no_projections(history_store):
    state = history_store
    with (
        patch.object(state, "_projection", side_effect=AssertionError("idle audit read")),
        patch.object(state, "_commit", wraps=state._commit) as commits,
    ):
        assert state.events_since(12) == []
        assert commits.call_count == 1


def test_projected_events_equal_authoritative_events(history_store):
    state = history_store
    chain = state._chain(state._read_head())
    for cursor in (-1, 0, 4, 11, 12, 99):
        assert state.events_since(cursor) == [e for c in chain for e in c["events"] if e["revision"] > cursor]


def test_same_length_projection_damage_recovers_and_old_damage_audits(history_store):
    state = history_store
    expected = state.history(limit=1)
    path = state.root / "history/operations.jsonl"
    original = path.read_bytes()
    path.write_bytes(original.replace(b'"command":"move"', b'"command":"fake"', 1))
    assert len(path.read_bytes()) == len(original)
    assert state.history(limit=1) == expected
    assert path.read_bytes() == original
    old = state.history(revision=0)[0]["transaction_id"]
    (state.commits / f"{old}.json").write_text("{}")
    # A fast read is not a full historical integrity audit.
    assert state.history(limit=1) == expected
    with pytest.raises(EngineeringError, match="committed record"):
        state.history(audit=True)


def test_info_default_rule_and_all_three_out_of_bounds_levels(small_project, tmp_path):
    small_project.pages.append(Page(id="future", name="Future page"))
    for strict in (False, True):
        info = next(i for i in validate(small_project, strict=strict) if i["code"] == "EMPTY_PAGE")
        assert info["severity"] == "INFO" and info["suggested_action"]
    small_project.pages[0].nodes[0].x = -999
    for severity in ("ERROR", "WARNING", "INFO"):
        small_project.metadata["out_of_bounds_severity"] = severity
        path = tmp_path / "fixture.json"
        path.write_text(small_project.model_dump_json(), encoding="utf-8")
        result = CliRunner().invoke(app, ["validate", "--file", str(path), "--strict", "--json"])
        data = json.loads(result.stdout)
        assert result.exit_code == (2 if severity == "ERROR" else 0)
        assert all(i["severity"] == severity for i in data["issues"] if i["code"] == "OUT_OF_BOUNDS")


def test_all_24_command_help_and_json_compatibility(history_store):
    assert len(app.registered_commands) == 24
    runner = CliRunner()
    for cmd in app.registered_commands:
        name = cmd.name or cmd.callback.__name__.replace("_", "-")
        assert runner.invoke(app, [name, "--help"]).exit_code == 0, name
    args = ["--project", str(history_store.root)]
    for cmd in ("status", "history", "events"):
        plain = runner.invoke(app, [*args, cmd])
        flagged = runner.invoke(app, [*args, cmd, "--json"])
        assert plain.exit_code == flagged.exit_code == 0
        assert json.loads(plain.stdout) == json.loads(flagged.stdout)
    assert json.loads(runner.invoke(app, [*args, "diff", "--json"]).stdout)["revision_after"] == 12
