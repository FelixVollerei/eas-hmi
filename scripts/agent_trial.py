"""Prepare/score a future paired agent experiment. Never runs an agent."""

import argparse
import json
from pathlib import Path

from eas_hmi.model import Project, canonical_hash
from eas_hmi.operations.history import semantic_diff
from eas_hmi.operations.transaction import Store
from eas_hmi.svg.renderer import render
from eas_hmi.svg.synchronizer import plan_sync

ROOT = Path(__file__).resolve().parents[1]
TARGETS = [f"CHWP_CARD_{i:02d}" for i in range(1, 21)]
TITLE = "COOLING / REVIEWED"


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def prepare(output):
    output.mkdir(parents=True, exist_ok=False)
    seed = Project.model_validate_json((ROOT / "examples/datacenter/model.json").read_bytes())
    # Same initial content, identity and revision for both arms.
    Store(output / "semantic-project").initialize(seed)
    (output / "seed.json").write_text(seed.model_dump_json(indent=2), encoding="utf-8")
    (output / "visual-input.svg").write_bytes(render(seed, "cooling"))
    save(
        output / "setup.json",
        {
            "seed_hash": canonical_hash(seed),
            "nodes": len(seed.all_nodes()),
            "targets": TARGETS,
            "title": TITLE,
            "dx": 12,
            "dy": 8,
            "width_target": "CHWP_CARD_07",
            "width": 164,
            "status": "prepared_not_run",
        },
    )


def score(seed_path, svg, output):
    seed = Project.model_validate_json(seed_path.read_bytes())
    if not svg.is_file():
        save(output, {"status": "missing_output", "score": 0, "complete_success": False})
        return
    try:
        candidate = plan_sync(seed, svg.read_bytes()).apply(seed)
    except Exception as exc:
        save(
            output,
            {
                "status": "integration_failure",
                "score": None,
                "complete_success": False,
                "reason": str(exc),
                "note": "Do not infer visual task quality from unsupported SVG. Keep the failed run in the trial ledger.",
            },
        )
        return
    before = {n.id: n for n in seed.all_nodes()}
    after = {n.id: n for n in candidate.all_nodes()}
    tolerance = 0.5
    moves = {
        node_id: abs(after[node_id].x - before[node_id].x - 12) <= tolerance
        and abs(after[node_id].y - before[node_id].y - 8) <= tolerance
        for node_id in TARGETS
    }
    title_ok = after["TITLE_COOLING"].text == TITLE
    width_ok = abs(after["CHWP_CARD_07"].width - 164) <= tolerance
    expected = seed.model_copy(deep=True)
    expected_nodes = {n.id: n for n in expected.all_nodes()}
    for node_id in TARGETS:
        expected_nodes[node_id].x += 12
        expected_nodes[node_id].y += 8
    expected_nodes["TITLE_COOLING"].text = TITLE
    expected_nodes["CHWP_CARD_07"].width = 164
    # Remove allowed differences before measuring collateral changes. Parent
    # movement is deliberately not equivalent: connections must remain fixed.
    neutral = candidate.model_copy(deep=True)
    neutral_nodes = {n.id: n for n in neutral.all_nodes()}
    for node_id in TARGETS:
        neutral_nodes[node_id].x = before[node_id].x
        neutral_nodes[node_id].y = before[node_id].y
    neutral_nodes["TITLE_COOLING"].text = before["TITLE_COOLING"].text
    neutral_nodes["CHWP_CARD_07"].width = before["CHWP_CARD_07"].width
    collateral = semantic_diff(seed, neutral)
    result = {
        "status": "scored",
        "score": 2 * sum(moves.values())
        + 10 * title_ok
        + 10 * width_ok
        + max(0, 30 - 5 * len(collateral))
        + 10,
        "move_points_out_of_40": 2 * sum(moves.values()),
        "moves": moves,
        "title_points_out_of_10": 10 * title_ok,
        "width_points_out_of_10": 10 * width_ok,
        "preservation_points_out_of_30": max(0, 30 - 5 * len(collateral)),
        "integration_points_out_of_10": 10,
        "collateral_changes": collateral,
        "complete_success": all(moves.values()) and title_ok and width_ok and not collateral,
        "seed_hash": canonical_hash(seed),
        "result_hash": canonical_hash(candidate),
        "exact_expected_hash": canonical_hash(expected),
        "tolerance_model_units": tolerance,
        "note": "Total is engineering correctness, not visual beauty; token/time metrics are external.",
    }
    save(output, result)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--output", type=Path, required=True)
    grade = sub.add_parser("score")
    grade.add_argument("--seed", type=Path, required=True)
    grade.add_argument("--svg", type=Path, required=True)
    grade.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.output)
    else:
        score(args.seed, args.svg, args.output)


if __name__ == "__main__":
    main()
