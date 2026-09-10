"""Scorer unit tests with generated files, not an Agent or GUI experiment."""

import importlib.util
import json
from pathlib import Path

from eas_hmi.model import Project
from eas_hmi.svg.renderer import render

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("agent_trial", ROOT / "scripts/agent_trial.py")
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)


def test_score_expected_result_and_collateral_damage(tmp_path):
    trial.prepare(tmp_path / "trial")
    seed_path = tmp_path / "trial/seed.json"
    model = Project.model_validate_json(seed_path.read_bytes())
    nodes = {n.id: n for n in model.all_nodes()}
    for node_id in trial.TARGETS:
        nodes[node_id].x += 12
        nodes[node_id].y += 8
    nodes["CHWP_CARD_07"].width = 164
    nodes["TITLE_COOLING"].text = trial.TITLE
    svg, output = tmp_path / "result.svg", tmp_path / "score.json"
    svg.write_bytes(render(model, "cooling"))
    trial.score(seed_path, svg, output)
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["score"] == 100 and result["complete_success"]
    nodes["CHWP_CARD_01"].height += 2
    svg.write_bytes(render(model, "cooling"))
    trial.score(seed_path, svg, output)
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["score"] == 95 and not result["complete_success"]
    assert len(result["collateral_changes"]) == 1


def test_incompatible_svg_is_not_fabricated_visual_score(tmp_path):
    seed = ROOT / "examples/datacenter/model.json"
    svg, output = tmp_path / "invalid.svg", tmp_path / "score.json"
    svg.write_text("<not-svg/>")
    trial.score(seed, svg, output)
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["status"] == "integration_failure" and result["score"] is None
    trial.score(seed, tmp_path / "missing.svg", output)
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["status"] == "missing_output" and result["score"] == 0 and not result["complete_success"]
