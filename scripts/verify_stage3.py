"""Stage 3 acceptance: pytest + actual installed CLI workflow, no GUI.

Creates a fresh run directory; stops after stage 3 evidence has been written.
"""

import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from lxml import etree

from eas_hmi.svg.synchronizer import frame_of

ROOT = Path(__file__).resolve().parents[1]


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    run = (
        ROOT
        / "build/stage3"
        / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
    )
    run.mkdir(parents=True)
    env = {**os.environ, "PYTHONUTF8": "1"}
    test_args = [
        sys.executable,
        "-m",
        "pytest",
        "tests",
        "-q",
        "--tb=short",
        f"--junitxml={run / 'tests.xml'}",
        "--cov=eas_hmi",
        "--cov-fail-under=0",
        f"--cov-report=json:{run / 'coverage.json'}",
        f"--cov-report=xml:{run / 'coverage.xml'}",
        "--cov-report=term-missing",
    ]
    tests = subprocess.run(test_args, cwd=ROOT, env=env, capture_output=True, encoding="utf-8", check=False)
    (run / "tests.log").write_text(tests.stdout + tests.stderr, encoding="utf-8")
    if tests.returncode:
        print(tests.stdout + tests.stderr)
        raise SystemExit(tests.returncode)
    executable = Path(sys.executable).parent / ("eas-hmi.exe" if os.name == "nt" else "eas-hmi")
    project_dir, transcript = run / "project", []

    def cli(*args, expected=0, parse=True):
        command = [str(executable), "--project", str(project_dir), *map(str, args)]
        result = subprocess.run(
            command, cwd=ROOT, env=env, capture_output=True, encoding="utf-8", check=False, timeout=30
        )
        record = {
            "command": command,
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
        transcript.append(record)
        save(run / "cli-transcript.json", transcript)
        assert result.returncode == expected, record
        return json.loads(result.stdout) if parse else result.stdout

    cli("--help", parse=False)
    init = cli("init", "--from-model", ROOT / "examples/stage3/model.json", "--json")
    assert init["revision"] == 0
    baseline = cli("status", "--json")
    missing = cli("query", "--equipment", "PUMP_07", "--missing-binding", "fault", "--json")
    assert missing["count"] == 1
    assert cli("validate", "--strict", "--json", expected=2)["ok"] is False
    bound = cli("--actor", "agent", "--expected-revision", "0", "bind", "BOX_A", "fault", "P07_FLT", "--json")
    assert bound["revision"] == 1
    assert cli("validate", "--strict", "--json")["ok"]
    save(run / "binding-diff.json", cli("diff", "--json"))
    before_move = cli("status", "--json")
    move = cli(
        "--auto-render", "--expected-revision", "1", "move", "BOX_A", "--dx", "40", "--dy", "10", "--json"
    )
    assert move["revision"] == 2 and move["rendered_revision"] == 2
    assert cli("inspect", "BOX_A", "--json")["target"]["x"] == 70
    save(run / "move-diff.json", cli("diff", "--json"))
    after_move = cli("status", "--json")
    assert (
        cli("--expected-revision", "1", "move", "BOX_A", "--dx", "5", "--json", expected=3)["code"]
        == "REVISION_CONFLICT"
    )
    assert cli("resize", "BOX_A", "--width", "-5", "--json", expected=2)["code"] == "VALIDATION_FAILED"
    assert cli("status", "--json") == after_move
    cli("undo", "--json")
    assert cli("status", "--json")["canonical_hash"] == before_move["canonical_hash"]
    semantic = run / "semantic-before-human.svg"
    cli("render", "--output", semantic, "--json")
    pre_human = cli("status", "--json")
    assert cli("sync-from-svg", semantic, "--json")["committed"] is False
    root = etree.fromstring(semantic.read_bytes())
    box = root.xpath('//*[@data-eas-id="BOX_A"]')[0]
    box.set("transform", "translate(15 5)")
    text = frame_of(root.xpath('//*[@data-eas-id="LABEL"]')[0])[0]
    text.text = "CHWP-07 — simulated human edit"
    human_svg = run / "human-edited.svg"
    human_svg.write_bytes(etree.tostring(root, encoding="utf-8", xml_declaration=True))
    preview = cli("sync-from-svg", human_svg, "--dry-run", "--json")
    assert preview["changes"] and not preview["committed"]
    assert cli("status", "--json") == pre_human
    synced = cli("sync-from-svg", human_svg, "--json")
    assert synced["committed"]
    human_diff = cli("diff", "--json")
    assert human_diff["actor"] == "human" and human_diff["affected_ids"] == ["BOX_A", "LABEL"]
    save(run / "human-diff.json", human_diff)
    (run / "human-diff.txt").write_text(cli("diff", parse=False), encoding="utf-8")
    cli("render", "--output", run / "semantic-after-human.svg", "--json")
    cli("sync-from-svg", human_svg, "--json", expected=3)
    cli("undo", "--json")
    final = cli("status", "--json")
    assert final["canonical_hash"] == pre_human["canonical_hash"]
    assert final["revision"] == 5
    # Damage only rebuildable projections, not the authoritative project.
    operations = project_dir / "history/operations.jsonl"
    events = project_dir / "history/events.jsonl"
    original_ops, original_events = operations.read_bytes(), events.read_bytes()
    operations.write_bytes(b"{truncated")
    events.unlink()
    assert cli("status", "--json") == final
    assert operations.read_bytes() == original_ops and events.read_bytes() == original_events
    history = cli("history", "--json")
    assert len(history["operations"]) == 6
    cli("validate", "--strict", "--json")
    cli("render", "--output", run / "final.svg", "--json")
    coverage = json.loads((run / "coverage.json").read_text())
    scoped = [
        v["summary"]
        for k, v in coverage["files"].items()
        if k.replace("\\", "/").endswith(("operations/transaction.py", "operations/history.py", "cli.py"))
    ]
    suite = etree.parse(str(run / "tests.xml")).getroot()[0]
    report = {
        "stage": 3,
        "status": "passed",
        "run_directory": str(run),
        "cli_executable": str(executable),
        "test_command": test_args,
        "test_exit_code": tests.returncode,
        "tests": int(suite.get("tests")),
        "failures": int(suite.get("failures")),
        "errors": int(suite.get("errors")),
        "skipped": int(suite.get("skipped")),
        "test_seconds": float(suite.get("time")),
        "whole_package_coverage_percent": coverage["totals"]["percent_covered"],
        "transaction_history_cli_coverage_percent": sum(s["covered_lines"] for s in scoped)
        / sum(s["num_statements"] for s in scoped)
        * 100,
        "cli_calls": len(transcript),
        "initial_revision": baseline["revision"],
        "final_revision": final["revision"],
        "committed_records": len(history["operations"]),
        "sample": {k: baseline[k] for k in ("pages", "nodes", "points")},
        "before_human_hash": pre_human["canonical_hash"],
        "after_undo_hash": final["canonical_hash"],
        "undo_hash_restored": True,
        "no_op_without_commit": True,
        "strict_validation_passed": True,
        "projection_corruption_recovered_exactly": True,
        "real_process_crash_cases": [
            "before-commit",
            "after-commit",
            "before-HEAD.json",
            "head-temp",
            "after-HEAD.json",
            "after-operations.jsonl",
            "after-events.jsonl",
        ],
        "concurrency": "real CLI processes: one winner with same expected revision; no lost update without expected revision",
        "gui_actions": {"screenshots": 0, "ocr": 0, "mouse": 0, "keyboard_gui": 0},
        "scope": "Stage 3 only; batch/context/full importer/additional node kinds/manifests remain stage 4; DC demo remains stage 5",
    }
    save(run / "report.json", report)
    save(ROOT / "build/stage3/latest.json", {"run_directory": str(run), "report": str(run / "report.json")})
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
