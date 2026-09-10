"""Reproduce the bounded stage 2 tests and evidence. No GUI or full MVP CLI.

Run with the project's .venv Python. Output is confined to a fresh run folder.
The 85% full-package coverage gate remains a stage 6 requirement; here it is
measured and reported honestly, without pretending the unfinished package passed.
"""

import argparse
import hashlib
import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from lxml import etree
from PIL import Image, ImageChops

from eas_hmi.errors import EngineeringError
from eas_hmi.export.assets import InkscapeBackend, export_asset
from eas_hmi.model import Project, canonical_hash
from eas_hmi.operations.transaction import Store
from eas_hmi.svg.common import xml_id
from eas_hmi.svg.renderer import render
from eas_hmi.svg.synchronizer import frame_of, plan_sync

ROOT = Path(__file__).resolve().parents[1]


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def get_node(project, id):
    return next(n for n in project.all_nodes() if n.id == id)


def get_element(root, id):
    return root.xpath("//*[@data-eas-id=$id]", id=id)[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "build/stage2")
    args = parser.parse_args()
    run = args.output.resolve() / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    )
    run.mkdir(parents=True)
    test_cmd = [
        sys.executable,
        "-m",
        "pytest",
        "tests",
        "-q",
        "--tb=short",
        f"--junitxml={run / 'tests.xml'}",
        "--cov=eas_hmi",
        "--cov-fail-under=0",
        f"--cov-report=xml:{run / 'coverage.xml'}",
        f"--cov-report=json:{run / 'coverage.json'}",
        "--cov-report=term-missing",
    ]
    tests = subprocess.run(
        test_cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )
    (run / "tests.log").write_text(tests.stdout + tests.stderr, encoding="utf-8")
    if tests.returncode:
        print(tests.stdout + tests.stderr)
        raise SystemExit(tests.returncode)
    project = Project.model_validate_json((ROOT / "examples/stage2/model.json").read_text(encoding="utf-8"))
    baseline_hash = canonical_hash(project)
    baseline = render(project, "probe")
    assert baseline == render(project, "probe")
    (run / "semantic.svg").write_bytes(baseline)
    store = Store(run / "state-no-op-probe")
    store.initialize(project)
    head_before = store.head.read_bytes()
    history_before = (store.root / "history/operations.jsonl").read_bytes()
    events_before = (store.root / "history/events.jsonl").read_bytes()
    no_op = plan_sync(project, baseline)
    result = store.transaction("stage2 no-op sync probe", no_op.apply, actor="human", expected_revision=0)
    assert not result["committed"]
    assert store.head.read_bytes() == head_before
    assert (store.root / "history/operations.jsonl").read_bytes() == history_before
    assert (store.root / "history/events.jsonl").read_bytes() == events_before
    backend = InkscapeBackend()
    version = backend.version()
    edited_root = etree.fromstring(baseline)
    get_element(edited_root, "BOX_A").set("transform", "translate(40 10) rotate(25 30 30)")
    frame_of(get_element(edited_root, "BOX_A")).set("width", "150")
    frame_of(get_element(edited_root, "LABEL"))[0].text = "CHWP-07 — human edit simulation"
    get_element(edited_root, "ASSEMBLY").set("display", "none")
    edited = etree.tostring(edited_root, encoding="utf-8", xml_declaration=True)
    (run / "human-edited.svg").write_bytes(edited)
    plan = plan_sync(project, edited)
    save_json(run / "semantic-diff.json", plan.changes)
    # A candidate artifact, not a committed project: successful write CLI belongs to stage 3.
    (run / "candidate-model.json").write_text(plan.candidate.model_dump_json(indent=2), encoding="utf-8")
    rerendered = render(plan.candidate, "probe")
    (run / "candidate-render.svg").write_bytes(rerendered)
    assert canonical_hash(project) == baseline_hash
    assert get_node(plan.candidate, "BOX_A").width == 150
    assert get_node(plan.candidate, "ASSEMBLY").visible is False
    assert plan_sync(plan.candidate, rerendered).changes == []
    first, _ = backend.png(edited, 800, 480)
    second, _ = backend.png(rerendered, 800, 480)
    visual_error = max(hi for lo, hi in ImageChops.difference(first, second).getextrema())
    assert visual_error <= 2
    rejections = []
    for name, transform in [("skew", "skewX(10)"), ("reflection", "scale(-1 1)"), ("singular", "scale(0)")]:
        root = etree.fromstring(baseline)
        get_element(root, "BOX_A").set("transform", transform)
        try:
            plan_sync(project, etree.tostring(root))
            raise AssertionError(f"{name} should be rejected")
        except EngineeringError as error:
            rejections.append({"case": name, **error.as_dict()})
    save_json(run / "rejections.json", rejections)
    save_result = run / "inkscape-saved.svg"
    backend.run([run / "semantic.svg", "--export-type=svg", f"--export-filename={save_result}"])
    assert not plan_sync(project, save_result.read_bytes()).changes
    actions = []
    for name, action in [
        ("move", "transform-translate:40,10"),
        ("rotate", "transform-rotate:30"),
        ("scale", "transform-scale:1.25"),
    ]:
        destination = run / f"inkscape-{name}.svg"
        command = [
            run / "semantic.svg",
            "--export-type=svg",
            f"--actions=select-by-id:{xml_id('BOX_A')};{action};export-filename:{destination};export-do",
        ]
        backend.run(command)
        actual = plan_sync(project, destination.read_bytes())
        assert {c["object_id"] for c in actual.changes} == {"BOX_A"}
        actions.append({"action": action, "arguments": list(map(str, command)), "changes": actual.changes})
    save_json(run / "inkscape-actions.json", actions)
    exports = [export_asset(project, "probe", run / "page.png", width=800, height=480, backend=backend)]
    for suffix in ("svg", "png", "bmp"):
        exports.append(
            export_asset(
                project,
                "probe",
                run / f"opaque-asset.{suffix}",
                node_id="OPAQUE_A",
                width=240,
                height=180,
                background="#f1e2d3",
                backend=backend,
            )
        )
    png, bmp = Image.open(run / "opaque-asset.png"), Image.open(run / "opaque-asset.bmp")
    png.load()
    bmp.load()
    assert png.size == bmp.size == (240, 180)
    assert png.getextrema()[3] == (0, 255)
    assert bmp.getpixel((0, 0)) == (241, 226, 211)
    coverage = json.loads((run / "coverage.json").read_text(encoding="utf-8"))
    scoped = [
        v["summary"]
        for k, v in coverage["files"].items()
        if "/svg/" in k.replace("\\", "/") or "/export/" in k.replace("\\", "/")
    ]
    statements = sum(s["num_statements"] for s in scoped)
    covered = sum(s["covered_lines"] for s in scoped)
    suite = etree.parse(str(run / "tests.xml")).getroot()[0]
    report = {
        "stage": 2,
        "status": "passed",
        "run_directory": str(run),
        "python": sys.version.split()[0],
        "backend": version,
        "test_command": test_cmd,
        "test_exit_code": tests.returncode,
        "tests": int(suite.get("tests")),
        "failures": int(suite.get("failures")),
        "errors": int(suite.get("errors")),
        "skipped": int(suite.get("skipped")),
        "test_seconds": float(suite.get("time")),
        "whole_package_coverage_percent": coverage["totals"]["percent_covered"],
        "stage2_svg_export_line_coverage_percent": covered / statements * 100,
        "full_package_85_percent_gate": "stage 6; measured only at this intermediate stage",
        "sample": {
            "pages": len(project.pages),
            "nodes": len(project.all_nodes()),
            "points": len(project.points),
        },
        "canonical_hash": baseline_hash,
        "svg_sha256": hashlib.sha256(baseline).hexdigest(),
        "render_byte_identical": True,
        "no_op_hash_head_history_events_unchanged": True,
        "actual_inkscape_save_no_op": True,
        "actual_inkscape_transforms": [a["action"] for a in actions],
        "edited_vs_rerender_max_channel_error": visual_error,
        "exports": exports,
        "gui_actions": {"screenshots": 0, "ocr": 0, "mouse": 0, "keyboard_gui": 0},
        "gui_evidence": "This script invokes only Python APIs and non-GUI Inkscape subprocess actions; tests follow the same path.",
        "limitations": [
            "Stage 3 CLI and durable successful-edit workflow are not implemented/accepted here.",
            "Renderer supports group, shape, text and opaque_svg; remaining kinds belong to stage 4.",
            "CSS at-rules, pseudo/attribute selectors and external resources are explicitly rejected.",
            "Rich/multiline text, reparenting, semantic node insertion/deletion, skew/reflection are rejected.",
            "Actual Inkscape CLI save/transform evidence does not certify every interactive GUI workflow.",
        ],
    }
    save_json(run / "report.json", report)
    save_json(
        args.output.resolve() / "latest.json", {"run_directory": str(run), "report": str(run / "report.json")}
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
