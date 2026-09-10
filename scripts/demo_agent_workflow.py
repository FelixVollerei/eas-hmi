"""Reproducible A-G demonstration using only the installed structured CLI.

Run with the project venv. Every invocation is recorded, including failures. No GUI APIs.
"""

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from lxml import etree
from PIL import Image

from eas_hmi.demo.synthetic import MISSING_FAULT, write_demo
from eas_hmi.model import canonical_hash
from eas_hmi.operations.transaction import Store
from eas_hmi.svg.common import safe_parse
from eas_hmi.svg.synchronizer import frame_of, identity_map

ROOT = Path(__file__).resolve().parents[1]


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_workflow(output):
    run = Path(output).resolve()
    run.mkdir(parents=True, exist_ok=False)
    project_dir = run / "project"
    inputs = run / "inputs"
    source_stats = write_demo(inputs)
    input_hashes = {
        str(p.relative_to(inputs)): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs.rglob("*.json")
    }
    executable = Path(sys.executable).parent / ("eas-hmi.exe" if os.name == "nt" else "eas-hmi")
    if not executable.is_file():
        raise RuntimeError("Install this package into the active venv: python -m pip install -e .")
    env = {**os.environ, "PYTHONUTF8": "1"}
    transcript, tasks = [], {}
    started = time.monotonic()

    def cli(*args, expected=0, target=None, raw=False):
        command = [str(executable), "--project", str(target or project_dir), *map(str, args)]
        tick = time.monotonic()
        result = subprocess.run(
            command, cwd=ROOT, env=env, encoding="utf-8", capture_output=True, timeout=120
        )
        record = dict(
            command=command,
            exit_code=result.returncode,
            expected_exit_code=expected,
            elapsed_seconds=round(time.monotonic() - tick, 4),
            stdout=result.stdout,
            stderr=result.stderr,
        )
        transcript.append(record)
        save(run / "cli-transcript.json", transcript)
        if result.returncode != expected:
            raise AssertionError(record)
        return result.stdout if raw else json.loads(result.stdout)

    def completed(name, evidence):
        tasks[name] = dict(status="passed", **evidence)
        save(run / "progress.json", tasks)
        print(f"Task {name} passed", flush=True)

    cli("--help", raw=True)
    cli("init", "--from-model", inputs / "model.json", "--json")
    initial = cli("status", "--json")
    assert initial["nodes"] >= 500 and initial["points"] >= 300 and initial["pages"] == 3
    initial_validation = cli("validate", "--json")
    assert Counter(i["code"] for i in initial_validation["issues"]) == {"MISSING_REQUIRED_BINDING": 3}
    cli("validate", "--strict", expected=2)
    filters = ["--page", "cooling", "--kind", "equipment_card", "--equipment-type", "pump"]

    # A: all answers come from CLI query/inspect/context, never image recognition.
    pumps = cli("query", *filters, "--json")
    missing = cli("query", *filters, "--missing-binding", "fault", "--json")
    equipment = cli("inspect", "CHWP_07", "--json")
    card = cli("inspect", "CHWP_CARD_07", "--json")
    related = cli("query", "--equipment", "CHWP_07", "--json")
    context = cli("context", "CHWP_CARD_07", "--depth", "2", "--limit", "40", "--json")
    connections = [cli("inspect", id, "--json")["target"] for id in card["references"]]
    connected_ids = sorted(
        {n[k] for n in connections for k in ("source_ref", "target_ref")} - {"CHWP_CARD_07"}
    )
    assert pumps["count"] == 20
    assert [n["id"] for n in missing["nodes"]] == list(MISSING_FAULT)
    assert len(equipment["points"]) == 6 and related["count"] == 6
    assert connected_ids == ["CHWP_CARD_06", "CHWP_CARD_08"]
    assert context["target"]["id"] == "CHWP_CARD_07" and len(context["nodes"]) <= 40
    answers = dict(
        cooling_pump_cards=pumps["count"],
        missing_fault_cards=[n["id"] for n in missing["nodes"]],
        equipment="CHWP_07",
        directly_associated_nodes=[n["id"] for n in related["nodes"]],
        connection_objects=card["references"],
        connected_cards=connected_ids,
        points=[{k: p[k] for k in ("id", "tag", "role", "datatype", "unit")} for p in equipment["points"]],
        context_nodes=len(context["nodes"]),
        context_truncated=context["truncated"],
    )
    save(run / "A/answers.json", answers)
    save(run / "A/context.json", context)
    completed("A", answers)

    # B: choose the unique fault point from each equipment's inspected registry.
    binding_diffs, repairs = [], []
    for node in missing["nodes"]:
        registry = cli("inspect", node["equipment_ref"], "--json")
        matches = [p for p in registry["points"] if p["role"] == "fault"]
        assert len(matches) == 1
        point = matches[0]
        result = cli("--actor", "agent", "bind", node["id"], "fault", point["id"], "--json")
        difference = cli("diff", "--json")
        assert result["committed"] and difference["affected_ids"] == [node["id"]]
        assert any(k.startswith("bindings.fault") for c in difference["changes"] for k in c["changes"])
        binding_diffs.append(difference)
        repairs.append(dict(node=node["id"], point=point["id"], revision=result["revision"]))
    assert cli("query", *filters, "--missing-binding", "fault")["count"] == 0
    assert cli("validate", "--strict")["issues"] == []
    save(run / "B/diffs.json", binding_diffs)
    completed("B", dict(repairs=repairs, remaining_missing_fault=0))

    # C: two structured edits, never per-object JSON edits.
    cli("batch", "set", *filters, "--property", "width", "--value", "164")
    layout_diffs = [cli("diff", "--json")]
    ids = [n["id"] for n in pumps["nodes"]]
    cli("distribute", "--ids", ",".join(ids), "--axis", "x", "--columns", "10", "--gap", "20")
    layout_diffs.append(cli("diff", "--json"))
    arranged = cli("query", *filters)["nodes"]
    for index, node in enumerate(arranged):
        assert node["width"] == 164 and node["height"] == 94
        assert node["x"] == 40 + (index % 10) * 184
        assert node["y"] == (index // 10) * 114
    assert all(c["object_id"] in ids for d in layout_diffs for c in d["changes"])
    assert cli("validate", "--strict")["issues"] == []
    save(run / "C/diffs.json", layout_diffs)
    save(
        run / "C/arrangement.json",
        [{k: n[k] for k in ("id", "x", "y", "width", "height", "parent_id")} for n in arranged],
    )
    completed("C", dict(cards=len(arranged), columns=10, rows=2, width=164, height=94, gap_x=20, gap_y=20))

    # Failures must preserve the exact committed HEAD, revision and semantic hash.
    before_errors = cli("status")
    head = (project_dir / "HEAD.json").read_bytes()
    cli("batch", "set", *filters, "--property", "width", "--value", "-1", expected=2)
    cli("--expected-revision", "0", "batch", "move", *filters, "--dx", "30", expected=3)
    assert (project_dir / "HEAD.json").read_bytes() == head and cli("status") == before_errors

    # D: render all pages, prove no-op sync, then modify only one tagged frame and text.
    rendered = cli("render", "--output-dir", run / "D/before")
    page_files = {safe_parse(Path(p).read_bytes()).get("data-eas-page"): Path(p) for p in rendered["paths"]}
    pre_human = cli("status")
    for path in page_files.values():
        assert not cli("sync-from-svg", path)["committed"]
    assert cli("status") == pre_human
    before_node = cli("inspect", "CHWP_CARD_07")["target"]
    before_title = cli("inspect", "TITLE_COOLING")["target"]
    root = safe_parse(page_files["cooling"].read_bytes())
    tagged = identity_map(root)
    tagged["CHWP_CARD_07"].set("transform", "translate(12 8)")
    frame_of(tagged["TITLE_COOLING"])[0].text = "COOLING / SIMULATED HUMAN EDIT"
    edited = run / "D/human-edited.svg"
    edited.write_bytes(etree.tostring(root, encoding="utf-8", xml_declaration=True))
    preview = cli("sync-from-svg", edited, "--dry-run")
    assert {c["object_id"] for c in preview["changes"]} == {"CHWP_CARD_07", "TITLE_COOLING"}
    assert cli("status") == pre_human
    cli("--actor", "human", "sync-from-svg", edited)
    human_diff = cli("diff", "--json")
    after_node = cli("inspect", "CHWP_CARD_07")["target"]
    after_title = cli("inspect", "TITLE_COOLING")["target"]
    assert after_node["x"] == before_node["x"] + 12 and after_node["y"] == before_node["y"] + 8
    assert after_title["text"] == "COOLING / SIMULATED HUMAN EDIT"
    assert human_diff["actor"] == "human" and len(human_diff["changes"]) == 2
    save(run / "D/diff.json", human_diff)
    completed(
        "D", dict(actor="human", moved="CHWP_CARD_07", dx=12, dy=8, text="TITLE_COOLING", no_op_pages=3)
    )

    # E: retain B/C/D operations individually, including readable semantic diff output.
    all_diffs = {"B": binding_diffs, "C": layout_diffs, "D": [human_diff]}
    save(run / "E/semantic-diffs.json", all_diffs)
    for task, differences in all_diffs.items():
        for index, difference in enumerate(differences, 1):
            readable = cli("diff", "--revision", difference["revision_after"], raw=True)
            (run / f"E/{task}-{index}.txt").write_text(readable, encoding="utf-8")
    completed(
        "E",
        dict(
            binding_transactions=len(binding_diffs),
            layout_transactions=len(layout_diffs),
            human_transactions=1,
        ),
    )

    # F: undo only the latest human change, preserving B and C and retaining the audit.
    cli("undo")
    restored = cli("status")
    assert restored["canonical_hash"] == pre_human["canonical_hash"]
    assert restored["revision"] == pre_human["revision"] + 2
    assert cli("inspect", "CHWP_CARD_07")["target"] == before_node
    assert cli("inspect", "TITLE_COOLING")["target"] == before_title
    save(run / "F/undo.json", dict(before_human=pre_human, after_undo=restored, semantic_hash_restored=True))
    completed(
        "F",
        dict(
            hash_before=pre_human["canonical_hash"],
            hash_after=restored["canonical_hash"],
            revision=restored["revision"],
        ),
    )

    # G: inspect the real exported files, not just their return codes.
    final_render = cli("render", "--output-dir", run / "G/semantic")
    for path in final_render["paths"]:
        svg = safe_parse(Path(path).read_bytes())
        assert len(identity_map(svg)) == source_stats["pages"][svg.get("data-eas-page")]
    manifest = cli("export-manifest", "--output-dir", run / "G/bindings")
    rows = json.loads(Path(manifest["files"]["bindings.json"]).read_text(encoding="utf-8"))
    with Path(manifest["files"]["bindings.csv"]).open(encoding="utf-8-sig", newline="") as stream:
        assert list(csv.DictReader(stream)) == rows
    assert len(rows) == 660 and len(rows[0]) == 10
    assets = cli(
        "export-assets",
        "--id",
        "CHWP_CARD_07",
        "--formats",
        "svg,png,bmp",
        "--width",
        "328",
        "--height",
        "188",
        "--output-dir",
        run / "G/assets",
    )
    for asset in assets["assets"]:
        assert Path(asset["path"]).is_file()
        if asset["format"] != "svg":
            with Image.open(asset["path"]) as image:
                image.load()
                assert image.size == (328, 188) and image.mode == (
                    "RGBA" if asset["format"] == "png" else "RGB"
                )
    previews = cli(
        "export-assets",
        "--formats",
        "png",
        "--width",
        "1920",
        "--height",
        "1080",
        "--output-dir",
        run / "G/page-previews",
    )
    for asset in previews["assets"]:
        with Image.open(asset["path"]) as image:
            image.load()
            assert image.size == (1920, 1080)
    save(
        run / "G/exports.json",
        dict(semantic=final_render, manifest=manifest, assets=assets, page_previews=previews),
    )
    completed(
        "G",
        dict(
            semantic_pages=3,
            bindings=len(rows),
            asset_formats=[a["format"] for a in assets["assets"]],
            page_pngs=3,
        ),
    )

    # Independently validate every deliberately broken input, and reject invalid initialization.
    fixture_results = {}
    fixture_index = json.loads((inputs / "broken/index.json").read_text(encoding="utf-8"))
    for name, expected in fixture_index.items():
        path = inputs / "broken" / (name + ".json")
        validation = cli("validate", "--file", path, "--json", expected=expected["exit_code"])
        counts = dict(Counter(i["code"] for i in validation["issues"]))
        assert counts == expected["expected_issue_counts"]
        if expected["exit_code"]:
            rejected = run / "rejected-projects" / name
            cli("init", "--from-model", path, target=rejected, expected=2)
            assert not (rejected / "HEAD.json").exists()
        fixture_results[name] = dict(expected=expected, actual=validation)
    assert cli("status") == restored
    assert cli("validate", "--strict")["issues"] == []
    save(run / "fixture-validation.json", fixture_results)
    operations = cli("history", "--limit", "1000")["operations"]
    events = cli("events", "--since-revision", "0")["events"]
    assert len(operations) == restored["revision"] + 1
    assert len({e["transaction_id"] for e in events}) == restored["revision"]
    assert [o["revision_after"] for o in operations] == list(range(restored["revision"] + 1))
    save(run / "events.json", events)
    assert all(
        hashlib.sha256((inputs / name).read_bytes()).hexdigest() == digest
        for name, digest in input_hashes.items()
    )
    # A delivery snapshot is read after all CLI-driven work; it is never an edit channel.
    final_model = Store(project_dir).load()
    (run / "final-model.json").write_text(final_model.model_dump_json(indent=2), encoding="utf-8")
    assert canonical_hash(final_model) == restored["canonical_hash"]
    report = dict(
        status="passed",
        tasks=tasks,
        source=source_stats,
        final=restored,
        cli_calls=len(transcript),
        audit_commits=len(operations),
        event_count=len(events),
        fixture_cases=len(fixture_results),
        elapsed_seconds=round(time.monotonic() - started, 3),
        input_hashes=input_hashes,
        inputs_unchanged=True,
        run_directory=str(run),
        interaction=dict(
            screenshots=0,
            ocr_calls=0,
            mouse_actions=0,
            keyboard_gui_actions=0,
            method="subprocess installed CLI; lxml edits only for Task D; no GUI library",
        ),
    )
    save(run / "report.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT
        / "build/demo"
        / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]),
    )
    options = parser.parse_args()
    try:
        result = run_workflow(options.output)
        print(json.dumps({k: v for k, v in result.items() if k != "tasks" and k != "input_hashes"}, indent=2))
    except Exception as exc:
        print(f"Demo failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
