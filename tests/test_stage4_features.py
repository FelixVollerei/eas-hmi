import csv
import json
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from eas_hmi.cli import app
from eas_hmi.errors import EngineeringError
from eas_hmi.export.bundle import export_bundle
from eas_hmi.export.manifest import FIELDS, binding_rows, manifest_files
from eas_hmi.model import Node, canonical_hash
from eas_hmi.operations import edit
from eas_hmi.operations.transaction import Store
from eas_hmi.query import Query

runner = CliRunner()


@pytest.fixture
def project():
    from eas_hmi.model import Project

    return Project.model_validate_json(
        (Path(__file__).parents[1] / "examples/stage4/model.json").read_text(encoding="utf-8")
    )


@pytest.fixture
def state(project, tmp_path):
    state = Store(tmp_path / "project")
    state.initialize(project)
    return state


def invoke(state, *args, code=0):
    result = runner.invoke(app, ["--project", str(state.root), *map(str, args)])
    assert result.exit_code == code, (result.stdout, result.exception)
    return json.loads(result.stdout)


def test_query_and_template_filters(state):
    assert [
        n["id"]
        for n in invoke(
            state,
            "query",
            "--kind",
            "equipment_card",
            "--equipment-type",
            "pump",
            "--template",
            "PUMP",
            "--missing-binding",
            "fault",
            "--json",
        )["nodes"]
    ] == ["CARD_A"]
    assert invoke(state, "query", "--equipment-type", "sensor", "--page", "spare")["count"] == 0


def test_context_depth_target_and_connections(project, state):
    q = Query(project)
    assert [n["id"] for n in q.context("CARD_A", depth=0)["nodes"]] == ["CARD_A"]
    one = q.context("CARD_A", depth=1)
    assert set(n["id"] for n in one["nodes"]) == {"CARD_A", "LINK", "STATUS"}
    assert one["directly_related_nodes"] == ["LINK", "STATUS"]
    two = q.context("CARD_A", depth=2)
    assert two["connected_nodes"] == ["CARD_B"]
    assert q.context("TITLE")["parent"]["id"] == "GROUP"
    assert q.context("GROUP")["children"] == ["TITLE"]
    assert q.context("PUMP_01")["target"]["type"] == "pump"
    assert q.context("P01_RUN")["target"]["tag"] == "SYN.P01.RUN"
    assert invoke(state, "context", "CARD_A", "--depth", 2, "--json")["connected_nodes"] == ["CARD_B"]
    assert invoke(state, "context", "missing", code=2)["code"] == "OBJECT_NOT_FOUND"
    assert invoke(state, "context", "CARD_A", "--depth", 4, code=2)["code"] == "USAGE_ERROR"


def test_context_limits_and_payload_omission(project):
    page = project.pages[0]
    for i in range(300):
        page.nodes.append(Node(id=f"A{i}", kind="shape", page_id=page.id, equipment_ref="PUMP_01"))
    q = Query(project)
    c = q.context("STATUS", limit=2, depth=3)
    assert c["nodes"][0]["id"] == "STATUS" and len(c["nodes"]) == 2 and c["truncated"]
    o = q.context("OPAQUE")
    assert "payload" not in o["target"] and o["target"]["payload_omitted"]
    Query(project).node("TITLE").text = "a" * 10000
    assert len(Query(project).context("TITLE")["target"]["text"]) == 513
    assert len(json.dumps(o)) < 5000
    for depth, limit in ((-1, 2), (4, 2), (1, 0), (1, 201)):
        with pytest.raises(EngineeringError, match="Depth"):
            q.context("CARD_A", depth, limit)


@pytest.mark.parametrize("mode", ["left", "center-x", "top", "center-y"])
def test_align_single_commit_and_undo(state, mode):
    original = canonical_hash(state.load())
    result = invoke(state, "align", "--ids", "CARD_A,CARD_B", "--mode", mode)
    assert result["committed"] and len(state.history()) == 2
    a, b = (Query(state.load()).node(i) for i in ("CARD_A", "CARD_B"))
    if mode == "left":
        assert a.x == b.x == 40
    elif mode == "top":
        assert a.y == b.y == 300
    elif mode == "center-x":
        assert a.x + a.width / 2 == b.x + b.width / 2
    else:
        assert a.y + a.height / 2 == b.y + b.height / 2
    invoke(state, "undo")
    assert canonical_hash(state.load()) == original


@pytest.mark.parametrize("axis", ["x", "y"])
def test_distribute_fixed_gap_and_grid(state, axis):
    invoke(state, "distribute", "--ids", "CARD_B,CARD_A", "--axis", axis, "--gap", 17)
    a, b = (Query(state.load()).node(i) for i in ("CARD_A", "CARD_B"))
    assert getattr(b, axis) - getattr(a, axis) == getattr(a, "width" if axis == "x" else "height") + 17
    invoke(state, "distribute", "--ids", "SHAPE,CARD_A,CARD_B", "--columns", 2)
    q = Query(state.load())
    assert q.node("CARD_A").y == q.node("CARD_B").y < q.node("SHAPE").y
    assert q.node("CARD_A").x == q.node("SHAPE").x


@pytest.mark.parametrize(
    "args",
    [
        ["align", "--ids", "CARD_A,CARD_A", "--mode", "left"],
        ["align", "--ids", "CARD_A,", "--mode", "left"],
        ["align", "--ids", "CARD_A,TITLE", "--mode", "left"],
        ["align", "--ids", "CARD_A,CARD_B", "--mode", "right"],
        ["distribute", "--ids", "CARD_A,CARD_B", "--axis", "z"],
        ["distribute", "--ids", "CARD_A,CARD_B", "--gap", "-1"],
        ["batch", "eval", "--value", "print(1)"],
        ["batch", "move", "--kind", "absent", "--dx", "20"],
        ["batch", "set", "--kind", "equipment_card", "--property", "width", "--value", "-1"],
        ["batch", "set", "--property", "width"],
        ["batch", "move", "--property", "width", "--value", "1"],
    ],
)
def test_invalid_bulk_operations_rollback(state, args):
    head = state.head.read_bytes()
    history = state.history()
    invoke(state, *args, code=2)
    assert state.head.read_bytes() == head and state.history() == history


def test_batch_atomic_selector_lock_and_revision(state):
    assert (
        invoke(state, "batch", "move", "--kind", "equipment_card", "--equipment-type", "pump", "--dx", "40")[
            "revision"
        ]
        == 1
    )
    assert Query(state.load()).node("CARD_A").x == 80
    assert Query(state.load()).node("CARD_B").x == 310
    assert Query(state.load()).node("STATUS").x == 540
    invoke(state, "--expected-revision", "0", "batch", "move", "--dx", "9", code=3)
    invoke(state, "set", "CARD_B", "locked", "true")
    before = state.head.read_bytes()
    invoke(state, "batch", "move", "--kind", "equipment_card", "--dx", 9, code=2)
    assert before == state.head.read_bytes()
    invoke(state, "batch", "set", "--kind", "equipment_card", "--property", "locked", "--value", "false")
    invoke(
        state,
        "batch",
        "set",
        "--kind",
        "equipment_card",
        "--property",
        "metadata.reviewed",
        "--value",
        "true",
    )
    assert Query(state.load()).node("CARD_B").metadata["reviewed"] is True
    count = len(state.history())
    assert not invoke(state, "batch", "move", "--kind", "equipment_card", "--dx", 0)["committed"]
    assert len(state.history()) == count
    invoke(state, "set", "CARD_B", "rotation", "30")
    invoke(state, "align", "--ids", "CARD_A,CARD_B", "--mode", "left", code=2)


def test_binding_manifest_unicode_csv_and_no_mutation(project, state, tmp_path):
    project.points[0].tag = 'SYN,"温度"\nline'
    files, count = manifest_files(project)
    rows = json.loads(files["bindings.json"])
    csv_rows = list(csv.DictReader(files["bindings.csv"].decode("utf-8-sig").splitlines(keepends=True)))
    assert count == 4 and rows == csv_rows and set(rows[0]) == set(FIELDS)
    assert rows[0]["point_tag"] == 'SYN,"温度"\nline'
    before = state.head.read_bytes()
    result = invoke(state, "export-manifest", "--json")
    assert result["rows"] == 4 and state.head.read_bytes() == before
    for name, path in result["files"].items():
        assert Path(path).name == name and Path(path).is_file()
    assert (
        invoke(state, "export-manifest", "--output-dir", state.root / "history", code=2)["code"]
        == "PROTECTED_PROJECT_PATH"
    )
    project.points.clear()
    with pytest.raises(EngineeringError):
        binding_rows(project)


def test_events_cursor_and_watch_once(state):
    assert len(invoke(state, "events", "--since-revision", -1)["events"]) > 0
    assert invoke(state, "events", "--since-revision", 0)["events"] == []
    invoke(state, "batch", "move", "--kind", "equipment_card", "--dx", 10)
    rows = invoke(state, "events", "--since-revision", 0)["events"]
    assert {r["object_id"] for r in rows} == {"CARD_A", "CARD_B"}
    assert {r["revision"] for r in rows} == {1}
    result = runner.invoke(
        app, ["--project", str(state.root), "watch", "--since-revision", "0", "--once", "--json"]
    )
    assert result.exit_code == 0 and [json.loads(line) for line in result.stdout.splitlines()] == rows
    silent = runner.invoke(app, ["--project", str(state.root), "watch", "--once"])
    assert silent.exit_code == 0 and not silent.stdout


def test_real_watch_catches_commits(state):
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "eas_hmi.cli",
            "--project",
            str(state.root),
            "watch",
            "--since-revision",
            "0",
            "--timeout",
            "1",
            "--interval",
            "0.05",
            "--json",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        time.sleep(0.15)
        state.transaction("move", lambda p: edit.move(p, ["CARD_A"], 9, 0))
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        events = [json.loads(line) for line in stdout.splitlines()]
        assert len(events) == 1 and events[0]["object_id"] == "CARD_A" and events[0]["revision"] == 1
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def test_export_bundle_prepare_failure_leaves_no_partial_files(project, tmp_path):
    directory = tmp_path / "exports"
    with patch("eas_hmi.export.bundle.export_asset", side_effect=EngineeringError("RASTER_FAILED", "test")):
        with pytest.raises(EngineeringError):
            export_bundle(project, directory)
    assert not list(directory.iterdir())
    for kwargs in (
        dict(node_id="CARD_A", page="components"),
        dict(template="missing"),
        dict(page="missing"),
        dict(formats=("jpeg",)),
        dict(formats=("svg", "svg")),
    ):
        with pytest.raises(EngineeringError):
            export_bundle(project, directory, **kwargs)


def test_multi_page_render_and_invalid_export_options(state, tmp_path):
    result = invoke(state, "render", "--output-dir", tmp_path / "rendered")
    assert len(result["paths"]) == 2 and all(Path(p).exists() for p in result["paths"])
    for args in (
        ["render", "--output", tmp_path / "one.svg"],
        ["render", "--output", tmp_path / "one.svg", "--output-dir", tmp_path],
        ["render", "--page", "missing"],
        ["export-assets", "--id", "CARD_A", "--page", "components"],
        ["export-assets", "--output-dir", state.root / ".eas"],
        ["export-assets", "--id", "CARD_A", "--formats", "svg", "--width", "10"],
    ):
        invoke(state, *args, code=2)


def test_template_requirements_and_role_validation(state):
    assert not invoke(state, "validate", "--strict", code=2)["ok"]
    invoke(state, "bind", "CARD_A", "fault", "P01_FLT")
    assert invoke(state, "validate", "--strict")["ok"]
    for role in ("unexpected", "", " fault "):
        before = state.head.read_bytes()
        invoke(state, "bind", "CARD_A", role, "P01_FLT", code=2)
        assert state.head.read_bytes() == before
    invoke(state, "unbind", "CARD_A", "fault")
    assert any(i["code"] == "MISSING_REQUIRED_BINDING" for i in invoke(state, "validate")["issues"])


@pytest.mark.parametrize(
    "mutate,code",
    [
        (lambda p: p["pages"][0]["nodes"].append(p["pages"][0]["nodes"][0].copy()), "DUPLICATE_ID"),
        (lambda p: p["pages"][0]["nodes"][0].update(parent_id="missing"), "INVALID_NODE_PARENT"),
        (lambda p: p["pages"][0]["nodes"][0].update(parent_id="TITLE"), "CIRCULAR_HIERARCHY"),
        (lambda p: p["pages"][0]["nodes"][0].update(page_id="missing"), "INVALID_PAGE_REFERENCE"),
        (lambda p: p["pages"][0]["nodes"][0].update(equipment_ref="missing"), "INVALID_EQUIPMENT_REFERENCE"),
        (lambda p: p["pages"][0]["nodes"][0].update(template_ref="missing"), "INVALID_TEMPLATE_REFERENCE"),
        (lambda p: p["pages"][0]["nodes"][0].update(source_ref="missing"), "INVALID_CONNECTION_REFERENCE"),
        (lambda p: p["pages"][0]["nodes"][0].update(width=-1), "SCHEMA_VALIDATION"),
        (lambda p: p["pages"][0]["nodes"][0].update(x=float("inf")), "SCHEMA_VALIDATION"),
        (lambda p: p["metadata"].update(arbitrary=float("nan")), "SCHEMA_VALIDATION"),
        (lambda p: p["templates"][0]["optional_bindings"].append("run"), "INVALID_TEMPLATE_ROLES"),
        (lambda p: p["equipment"][0].update(parent="missing"), "INVALID_EQUIPMENT_PARENT"),
        (lambda p: p["points"][0].update(equipment_ref="missing"), "INVALID_EQUIPMENT_REFERENCE"),
        (
            lambda p: p["pages"][0]["nodes"][5]["bindings"].append({"role": "run", "point_ref": "P01_RUN"}),
            "DUPLICATE_BINDING",
        ),
    ],
)
def test_validation_structural_rules(project, mutate, code):
    from eas_hmi.validation import validate_raw

    raw = project.model_dump(mode="python")
    mutate(raw)
    issues = validate_raw(raw)
    assert any(i["code"] == code and i["severity"] == "ERROR" for i in issues)


def test_bounds_policy_transformed_children_and_template_roles(project):
    from eas_hmi.validation import validate

    Query(project).node("TITLE").x = 900
    assert any(i["object_id"] == "TITLE" and i["severity"] == "WARNING" for i in validate(project))
    assert any(
        i["object_id"] == "TITLE" and i["severity"] == "ERROR"
        for i in validate(project, out_of_bounds="ERROR")
    )
    assert any(i["code"] == "INVALID_POLICY" for i in validate(project, out_of_bounds="invalid"))
    project.templates[0].required_bindings.append("")
    assert any(i["code"] == "INVALID_TEMPLATE_ROLES" for i in validate(project))
