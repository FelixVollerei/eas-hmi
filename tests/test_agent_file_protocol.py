import json
from pathlib import Path

from typer.testing import CliRunner

from eas_hmi.cli import app as eas_app
from eas_hmi.model import Project, canonical_hash
from eas_hmi.operations.transaction import Store
from sfe_files.cli import app as files_app
from sfe_files.core import operations_for, prepare, sha256
from sfe_files.service import contract_data

runner = CliRunner()
ROOT = Path(__file__).resolve().parents[1]


def test_file_capabilities_are_detached_from_registry():
    catalog = contract_data()
    catalog["formats"]["json"]["instruction_set"]["operations"]["add"]["description"] = "changed"
    catalog["formats"]["jsonl"]["instruction_set"]["addressing"] = "changed"
    operations = operations_for("jsonl")
    operations.clear()

    fresh = contract_data()
    assert fresh["formats"]["json"]["instruction_set"]["operations"]["add"]["description"] != "changed"
    assert fresh["formats"]["jsonl"]["instruction_set"]["addressing"] != "changed"
    assert "set-field" in operations_for("jsonl")


def invoke(app, *args, code=0):
    result = runner.invoke(app, [str(arg) for arg in args])
    assert result.exit_code == code, (result.stdout, result.exception)
    return json.loads(result.stdout)


def test_eas_multistep_preview_commit_and_rollback(tmp_path):
    project = Project.model_validate_json((ROOT / "examples/stage4/model.json").read_text(encoding="utf-8"))
    state = Store(tmp_path / "project")
    state.initialize(project)
    plan = {
        "protocol": "sfe/0.1",
        "format": "eas-hmi",
        "base": {"revision": 0, "semantic_hash": canonical_hash(state.load())},
        "steps": [
            {"op": "move", "ids": ["CARD_A"], "dx": 10.0, "dy": 5.0},
            {"op": "set", "ids": ["CARD_A"], "property": "metadata.reviewed", "value": True},
        ],
    }
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps(plan), encoding="utf-8")
    head = state.head.read_bytes()
    preview = invoke(eas_app, "--project", state.root, "apply-plan", plan_file, "--dry-run")
    assert preview["would_commit"] and preview["next_revision"] == 1
    assert state.head.read_bytes() == head
    result = invoke(eas_app, "--project", state.root, "apply-plan", plan_file)
    assert result["committed"] and result["revision"] == 1
    assert len(state.history()) == 2
    node = next(n for n in state.load().all_nodes() if n.id == "CARD_A")
    assert (node.x, node.y, node.metadata["reviewed"]) == (50.0, 305.0, True)
    assert (
        invoke(eas_app, "--project", state.root, "apply-plan", plan_file, code=3)["code"]
        == "REVISION_CONFLICT"
    )
    invoke(eas_app, "--project", state.root, "undo")
    assert canonical_hash(state.load()) == plan["base"]["semantic_hash"]

    bad = dict(plan)
    bad["base"] = {"revision": 2, "semantic_hash": canonical_hash(state.load())}
    bad["steps"] = [
        {"op": "move", "ids": ["CARD_A"], "dx": 4.0},
        {"op": "set", "ids": ["CARD_A"], "property": "width", "value": -1},
    ]
    plan_file.write_text(json.dumps(bad), encoding="utf-8")
    head = state.head.read_bytes()
    assert (
        invoke(eas_app, "--project", state.root, "apply-plan", plan_file, code=2)["code"]
        == "VALIDATION_FAILED"
    )
    assert state.head.read_bytes() == head


def test_eas_plan_rejects_unknown_step_and_wrong_hash(tmp_path):
    project = Project.model_validate_json((ROOT / "examples/stage4/model.json").read_text(encoding="utf-8"))
    state = Store(tmp_path / "project")
    state.initialize(project)
    plan_file = tmp_path / "plan.json"
    plan = {
        "protocol": "sfe/0.1",
        "format": "eas-hmi",
        "base": {"revision": 0, "semantic_hash": "0" * 64},
        "steps": [{"op": "move", "ids": ["CARD_A"], "dx": 1.0}],
    }
    plan_file.write_text(json.dumps(plan), encoding="utf-8")
    assert (
        invoke(eas_app, "--project", state.root, "apply-plan", plan_file, code=2)["code"] == "BASE_CONFLICT"
    )
    plan["base"]["semantic_hash"] = canonical_hash(state.load())
    plan["steps"] = [{"op": "run-shell", "command": "echo no"}]
    plan_file.write_text(json.dumps(plan), encoding="utf-8")
    assert invoke(eas_app, "--project", state.root, "apply-plan", plan_file, code=2)["code"] == "INVALID_PLAN"
    assert invoke(eas_app, "agent-contract")["format"] == "eas-hmi"


def test_json_file_profile(tmp_path):
    source = tmp_path / "data.json"
    source.write_text('{"title":"Old","items":[1]}\n', encoding="utf-8")
    raw = source.read_bytes()
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "protocol": "sfe/0.1",
                "format": "json",
                "base": {"sha256": sha256(raw)},
                "steps": [
                    {"op": "set", "pointer": "/title", "value": "New"},
                    {"op": "append", "pointer": "/items", "value": 2},
                    {"op": "add", "pointer": "/active", "value": True},
                ],
            }
        ),
        encoding="utf-8",
    )
    assert invoke(files_app, "inspect", source, "--pointer", "/title")["value"] == "Old"
    assert "New" in invoke(files_app, "preview", source, plan)["diff"]
    output = tmp_path / "edited.json"
    invoke(files_app, "apply", source, plan, "--output", output)
    assert json.loads(output.read_text(encoding="utf-8")) == {"title": "New", "items": [1, 2], "active": True}
    assert source.read_bytes() == raw
    assert invoke(files_app, "apply", source, plan, "--output", output, code=2)["code"] == "OUTPUT_EXISTS"
    source.write_text('{"title":"Changed","items":[1]}', encoding="utf-8")
    assert invoke(files_app, "preview", source, plan, code=2)["code"] == "BASE_CONFLICT"


def test_csv_file_profile_keyed_edit_and_rollback(tmp_path):
    source = tmp_path / "data.csv"
    source.write_text("id,name,status\na,Alpha,open\nb,Beta,open\n", encoding="utf-8")
    raw = source.read_bytes()
    plan = tmp_path / "plan.json"
    base = {
        "protocol": "sfe/0.1",
        "format": "csv",
        "base": {"sha256": sha256(raw)},
        "steps": [
            {"op": "set-cell", "key_column": "id", "key": "b", "column": "status", "value": "done"},
            {"op": "append-row", "key_column": "id", "row": {"id": "c", "name": "Gamma", "status": "open"}},
        ],
    }
    plan.write_text(json.dumps(base), encoding="utf-8")
    assert invoke(files_app, "inspect", source, "--key-column", "id", "--key", "b")["row"]["name"] == "Beta"
    output = tmp_path / "edited.csv"
    invoke(files_app, "apply", source, plan, "--output", output)
    assert "b,Beta,done" in output.read_text(encoding="utf-8")
    assert "c,Gamma,open" in output.read_text(encoding="utf-8")
    assert source.read_bytes() == raw
    base["steps"].append({"op": "delete-row", "key_column": "id", "key": "missing"})
    plan.write_text(json.dumps(base), encoding="utf-8")
    assert invoke(files_app, "preview", source, plan, code=2)["code"] == "ROW_NOT_UNIQUE"


def test_markdown_file_profile_skips_fenced_heading(tmp_path):
    source = tmp_path / "notes.md"
    source.write_text(
        "# Notes\n\n## Scope\nOld text.\n\n```md\n## Fake\n```\n\n## Next\nKeep.\n", encoding="utf-8"
    )
    raw = source.read_bytes()
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "protocol": "sfe/0.1",
                "format": "markdown",
                "base": {"sha256": sha256(raw)},
                "steps": [
                    {"op": "replace-section", "heading": "Scope", "body": "New text."},
                    {"op": "append-section", "heading": "Decision", "level": 2, "body": "Approved."},
                ],
            }
        ),
        encoding="utf-8",
    )
    headings = invoke(files_app, "status", source)["summary"]["headings"]
    assert [h["heading"] for h in headings] == ["Notes", "Scope", "Next"]
    output = tmp_path / "edited.md"
    invoke(files_app, "apply", source, plan, "--output", output)
    edited = output.read_text(encoding="utf-8")
    assert "New text." in edited and "Old text." not in edited
    assert "## Next\nKeep." in edited and "## Decision" in edited
    assert source.read_bytes() == raw


def test_markdown_replace_section_after_heading_without_final_newline(tmp_path):
    source = tmp_path / "notes.md"
    raw = b"# Findings"
    source.write_bytes(raw)
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "protocol": "sfe/0.1",
                "format": "markdown",
                "base": {"sha256": sha256(raw)},
                "steps": [
                    {"op": "replace-section", "heading": "Findings", "body": "new body"},
                ],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "edited.md"
    invoke(files_app, "apply", source, plan, "--output", output)
    section = invoke(files_app, "inspect", output, "--heading", "Findings")
    assert section["heading"]["heading"] == "Findings"
    assert section["body"].strip() == "new body"
    assert source.read_bytes() == raw


def test_published_plan_examples_still_match_sources():
    sample = ROOT / "examples/sfe"
    for plan in sample.glob("*.plan.json"):
        if plan.name == "eas.plan.json":
            continue
        stem = plan.name.removesuffix(".plan.json")
        sources = [path for path in sample.glob(f"{stem}.*") if path.name != plan.name]
        assert len(sources) == 1, plan.name
        assert json.loads(plan.read_text(encoding="utf-8"))["base"]["sha256"] == sha256(
            sources[0].read_bytes()
        ), sources[0].name
    for name in (
        "settings.json",
        "assets.csv",
        "handover.md",
        "deployment.yaml",
        "service.toml",
        "schematic.svg",
    ):
        source = sample / name
        plan = sample / (source.stem + ".plan.json")
        prepared = prepare(source, source.read_bytes(), plan.read_bytes())
        assert prepared.before_sha256 != prepared.after_sha256
    eas_plan = json.loads((sample / "eas.plan.json").read_text(encoding="utf-8"))
    project = Project.model_validate_json((ROOT / "examples/stage4/model.json").read_text(encoding="utf-8"))
    assert eas_plan["base"] == {"revision": 0, "semantic_hash": canonical_hash(project)}
