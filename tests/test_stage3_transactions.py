import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

from eas_hmi.errors import EngineeringError
from eas_hmi.model import Binding, Equipment, Point, Project, Template, canonical_hash
from eas_hmi.operations import edit
from eas_hmi.operations.history import semantic_diff
from eas_hmi.operations.transaction import Store, atomic_write

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def state(small_project, tmp_path):
    result = Store(tmp_path / "state")
    result.initialize(small_project)
    return result


def snapshot(state):
    return {
        str(p.relative_to(state.root)): p.read_bytes()
        for p in [state.head, *sorted((state.root / "history").glob("*"))]
    }


def test_successful_commit_diff_history_events_and_complete_undo(state):
    before = state.load()
    original_hash = canonical_hash(before)

    def mutate(p):
        edit.move(p, ["BOX_A", "LABEL"], dx=40, dy=5)
        next(n for n in p.all_nodes() if n.id == "OPAQUE_A").payload += "\n<!-- retained art comment -->"
        p.pages[0].nodes[0].metadata["review"] = None

    result = state.transaction("multi-object edit", mutate, actor="human", expected_revision=0)
    assert result["committed"] and result["revision"] == 1
    operation = state.history()[-1]
    assert operation["actor"] == "human"
    assert operation["affected_ids"] == ["BOX_A", "LABEL", "OPAQUE_A"]
    assert operation["hash_before"] == original_hash
    assert operation["hash_after"] == canonical_hash(state.load())
    assert operation["before"]["node:BOX_A"]["x"] == 30
    assert operation["after"]["node:BOX_A"]["x"] == 70
    fields = next(c for c in operation["changes"] if c["object_id"] == "BOX_A")["changes"]
    assert fields["geometry.x"] == {"old": 30, "new": 70}
    assert fields["metadata.review"]["new_present"] is True
    events = state.events_since(0)
    assert {e["object_id"] for e in events} == set(operation["affected_ids"])
    assert all(e["transaction_id"] == result["transaction_id"] for e in events)
    undone = state.undo(expected_revision=1)
    assert undone["revision"] == 2
    assert canonical_hash(state.load()) == original_hash
    restored = state.load().model_dump(exclude={"revision"})
    assert restored == before.model_dump(exclude={"revision"})
    assert len(state.history()) == 3
    assert state.history()[-1]["command"].startswith("undo ")


@pytest.mark.parametrize(
    "mode", ["schema", "reference", "exception", "nonfinite", "duplicate", "identity", "revision"]
)
def test_candidate_failure_rolls_back_every_object(state, mode):
    original = snapshot(state)

    def mutate(p):
        edit.move(p, ["BOX_A"], dx=10)
        if mode == "schema":
            edit.resize(p, ["LABEL"], width=-1)
        elif mode == "reference":
            p.pages[0].nodes[1].equipment_ref = "UNKNOWN"
        elif mode == "exception":
            raise RuntimeError("operation failed after first object")
        elif mode == "nonfinite":
            p.metadata["bad"] = float("nan")
        elif mode == "duplicate":
            p.pages[0].nodes.append(p.pages[0].nodes[0].model_copy(deep=True))
        elif mode == "identity":
            p.id = "OTHER"
        else:
            p.revision = 42

    with pytest.raises((EngineeringError, RuntimeError)):
        state.transaction("invalid candidate", mutate)
    assert snapshot(state) == original
    assert state.load().revision == 0
    assert len(list(state.commits.glob("*.json"))) == 1


def test_revision_conflict_precedes_mutation(state):
    called = []
    original = snapshot(state)
    with pytest.raises(EngineeringError) as err:
        state.transaction("stale edit", lambda p: called.append(True), expected_revision=99)
    assert err.value.code == "REVISION_CONFLICT"
    assert not called and snapshot(state) == original
    with pytest.raises(EngineeringError, match="Revision"):
        state.undo(expected_revision=99)


@pytest.mark.parametrize("entry", ["initialize", "transaction", "undo"])
def test_all_writers_validate_actor(state, small_project, entry):
    original = snapshot(state)
    with pytest.raises(EngineeringError) as err:
        if entry == "initialize":
            state.initialize(small_project, actor="unknown")
        elif entry == "transaction":
            state.transaction("x", lambda p: None, actor="unknown")
        else:
            state.undo(actor="unknown")
    assert err.value.code == "INVALID_ACTOR"
    assert snapshot(state) == original


def test_no_op_and_initialization_guards(state, small_project):
    original = snapshot(state)
    assert state.transaction("no-op", lambda p: edit.move(p, ["BOX_A"], 0, 0))["committed"] is False
    assert snapshot(state) == original
    with pytest.raises(EngineeringError, match="already exists"):
        state.initialize(small_project)
    with pytest.raises(EngineeringError, match="Initialization"):
        state.undo()


def test_invalid_initialization_does_not_create_project(small_project, tmp_path):
    small_project.pages[0].nodes[0].equipment_ref = "UNKNOWN"
    root = tmp_path / "invalid"
    with pytest.raises(EngineeringError):
        Store(root).initialize(small_project)
    assert not root.exists()


def test_binding_changes_are_semantic_and_undoable(small_project, tmp_path):
    small_project.equipment = [Equipment(id="PUMP", name="Synthetic pump", type="pump")]
    small_project.points = [
        Point(id="FLT", tag="SYN.PUMP.FLT", datatype="bool", equipment_ref="PUMP", role="fault")
    ]
    small_project.templates = [Template(id="PUMP_TEMPLATE", required_bindings=["fault"])]
    small_project.pages[0].nodes[0].equipment_ref = "PUMP"
    small_project.pages[0].nodes[0].template_ref = "PUMP_TEMPLATE"
    state = Store(tmp_path / "binding")
    state.initialize(small_project)
    before = canonical_hash(state.load())
    state.transaction("bind fault", lambda p: edit.bind(p, "BOX_A", "fault", "FLT"))
    fields = state.history()[-1]["changes"][0]["changes"]
    assert fields["bindings.fault.point_ref"]["new"] == "FLT"
    assert not state.history()[-1]["validation"]
    state.undo()
    assert canonical_hash(state.load()) == before


@pytest.mark.parametrize("name", ["operations.jsonl", "events.jsonl", "revision.json"])
@pytest.mark.parametrize("mode", ["delete", "truncate", "replace"])
def test_projection_recovers_from_corruption_and_is_idempotent(state, name, mode):
    state.transaction("move", lambda p: edit.move(p, ["BOX_A"], dx=10))
    expected = snapshot(state)
    target = state.root / "history" / name
    if mode == "delete":
        target.unlink()
    else:
        target.write_bytes(b"{partial" if mode == "truncate" else b"{}\n")
    reopened = Store(state.root)
    assert reopened.load().revision == 1
    assert snapshot(reopened) == expected
    reopened.load()
    assert snapshot(reopened) == expected


@pytest.mark.parametrize(
    "phase",
    [
        "before-commit",
        "after-commit",
        "before-HEAD.json",
        "head-temp",
        "after-HEAD.json",
        "after-operations.jsonl",
        "after-events.jsonl",
    ],
)
def test_real_process_crash_releases_lock_and_recovers_at_commit_boundary(state, phase):
    before = snapshot(state)
    result = subprocess.run(
        [sys.executable, ROOT / "tests/helpers/crash_writer.py", state.root, phase],
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 73, result.stderr.decode(errors="replace")
    reopened = Store(state.root, lock_timeout=1)
    current = reopened.load()
    committed = phase in ("after-HEAD.json", "after-operations.jsonl", "after-events.jsonl")
    assert current.revision == (1 if committed else 0)
    assert current.pages[0].nodes[0].x == (70 if committed else 30)
    if not committed:
        assert snapshot(reopened) == before
    assert len(reopened.history()) == (2 if committed else 1)
    assert len(reopened.events_since(0)) == (1 if committed else 0)
    # Reopening twice neither duplicates operations nor repeats events.
    stable = snapshot(reopened)
    Store(state.root).load()
    assert snapshot(reopened) == stable


def test_after_commit_projection_io_failure_reports_committed_and_recovers(state):
    def fail_projection(path, data):
        if path.name == "events.jsonl":
            raise OSError("injected disk write error")
        atomic_write(path, data)

    with patch("eas_hmi.operations.transaction.atomic_write", side_effect=fail_projection):
        result = state.transaction("move", lambda p: edit.move(p, ["BOX_A"], dx=1))
    assert result["committed"] and "Committed" in result["warnings"][0]
    assert Store(state.root).load().revision == 1
    assert len(state.history()) == 2


def test_commit_corruption_fails_loudly_without_falling_back(state):
    state.transaction("move", lambda p: edit.move(p, ["BOX_A"], dx=1))
    head = json.loads(state.head.read_text())
    commit = state.commits / f"{head['commit']}.json"
    data = json.loads(commit.read_text())
    data["model"]["pages"][0]["nodes"][0]["x"] = 900
    commit.write_text(json.dumps(data))
    original = state.head.read_bytes()
    with pytest.raises(EngineeringError) as err:
        state.load()
    assert err.value.code == "STORE_CORRUPT"
    assert state.head.read_bytes() == original


def test_concurrent_real_cli_writers_only_one_expected_revision_succeeds(state):
    command = [
        sys.executable,
        "-m",
        "eas_hmi.cli",
        "--project",
        str(state.root),
        "--expected-revision",
        "0",
        "move",
        "BOX_A",
        "--dx",
        "10",
        "--json",
    ]

    def invoke():
        return subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: invoke(), range(2)))
    assert sorted(r.returncode for r in results) == [0, 3]
    assert state.load().pages[0].nodes[0].x == 40
    assert len(state.history()) == 2


def test_semantic_diff_tracks_null_dotted_metadata_and_order(small_project):
    changed = small_project.model_copy(deep=True)
    changed.pages[0].nodes.reverse()
    changed.metadata["a.b"] = None
    changes = semantic_diff(small_project, changed)
    assert {c["object_type"] for c in changes} == {"page", "project"}
    project_change = next(c for c in changes if c["object_type"] == "project")
    assert project_change["changes"]["metadata.a\\.b"]["new_present"] is True


def test_rebinding_same_role_preserves_metadata_order_and_is_no_op(small_project, tmp_path):
    small_project.points = [
        Point(id="RUN", tag="SYN.RUN", datatype="bool"),
        Point(id="FAULT", tag="SYN.FAULT", datatype="bool"),
    ]
    small_project.pages[0].nodes[0].bindings = [
        Binding(role="run", point_ref="RUN", metadata={"note": "keep"}),
        Binding(role="fault", point_ref="FAULT"),
    ]
    state = Store(tmp_path / "binding-noop")
    state.initialize(small_project)
    before = snapshot(state)
    result = state.transaction("same binding", lambda p: edit.bind(p, "BOX_A", "run", "RUN"))
    assert not result["committed"] and snapshot(state) == before


@pytest.mark.parametrize("target", ["head-json", "head-revision", "parent"])
def test_authoritative_corruption_never_silently_repairs_from_projections(state, target):
    state.transaction("move", lambda p: edit.move(p, ["BOX_A"], dx=1))
    head = json.loads(state.head.read_text())
    if target == "head-json":
        state.head.write_text("{broken")
    elif target == "head-revision":
        head["revision"] = 100
        state.head.write_text(json.dumps(head))
    else:
        current = json.loads((state.commits / f"{head['commit']}.json").read_text())
        (state.commits / f"{current['parent']}.json").write_text("{}")
        (state.root / "history/revision.json").unlink()
    with pytest.raises(EngineeringError) as exc:
        Store(state.root).load()
    assert exc.value.code == "STORE_CORRUPT"


def test_concurrent_writers_without_expected_revision_do_not_lose_updates(state):
    command = [
        sys.executable,
        "-m",
        "eas_hmi.cli",
        "--project",
        str(state.root),
        "move",
        "BOX_A",
        "--dx",
        "1",
    ]
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(
            pool.map(
                lambda _: subprocess.run(command, capture_output=True, timeout=15, check=False), range(3)
            )
        )
    assert all(r.returncode == 0 for r in results)
    assert state.load().pages[0].nodes[0].x == 33
    assert len(state.history()) == 4


def test_default_numeric_values_have_stable_hash_across_validation(small_project):
    assert canonical_hash(small_project) == canonical_hash(Project.model_validate(small_project.model_dump()))
    assert canonical_hash(small_project) == canonical_hash(
        Project.model_validate_json(small_project.model_dump_json())
    )
