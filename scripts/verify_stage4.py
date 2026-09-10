"""Stage 4 acceptance: full regressions plus the installed CLI, with evidence files."""

import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from lxml import etree

from eas_hmi.operations.transaction import Store
from eas_hmi.svg.common import safe_parse
from eas_hmi.svg.synchronizer import identity_map

ROOT = Path(__file__).resolve().parents[1]


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    run = (
        ROOT
        / "build/stage4"
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
        "--cov-fail-under=85",
        f"--cov-report=json:{run / 'coverage.json'}",
        f"--cov-report=xml:{run / 'coverage.xml'}",
        "--cov-report=term-missing",
    ]
    result = subprocess.run(test_args, cwd=ROOT, env=env, capture_output=True, encoding="utf-8", timeout=600)
    (run / "tests.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode:
        print(result.stdout + result.stderr)
        raise SystemExit(result.returncode)
    executable = Path(sys.executable).parent / ("eas-hmi.exe" if os.name == "nt" else "eas-hmi")
    project = run / "project"
    transcript = []

    def cli(*args, expected=0, target=None, jsonl=False, raw=False):
        command = [str(executable), "--project", str(target or project), *map(str, args)]
        completed = subprocess.run(
            command, cwd=ROOT, env=env, capture_output=True, encoding="utf-8", timeout=90
        )
        record = dict(
            command=command, exit_code=completed.returncode, stdout=completed.stdout, stderr=completed.stderr
        )
        transcript.append(record)
        save(run / "cli-transcript.json", transcript)
        assert completed.returncode == expected, record
        if raw:
            return completed.stdout
        return (
            [json.loads(s) for s in completed.stdout.splitlines()] if jsonl else json.loads(completed.stdout)
        )

    cli("--help", raw=True)
    cli("init", "--from-model", ROOT / "examples/stage4/model.json")
    assert cli("status")["nodes"] == 11
    assert (
        cli(
            "query",
            "--kind",
            "equipment_card",
            "--equipment-type",
            "pump",
            "--template",
            "PUMP",
            "--missing-binding",
            "fault",
        )["count"]
        == 1
    )
    save(run / "context.json", cli("context", "CARD_A", "--depth", "2", "--json"))
    cli("context", "CARD_A", "--depth", "4", expected=2)
    cli("validate", "--strict", expected=2)
    cli("bind", "CARD_A", "fault", "P01_FLT")
    assert cli("validate", "--strict")["ok"]
    baseline = cli("status")["canonical_hash"]
    cli("batch", "move", "--kind", "equipment_card", "--equipment-type", "pump", "--dx", "40")
    save(run / "batch-diff.json", cli("diff", "--json"))
    cli("undo")
    assert cli("status")["canonical_hash"] == baseline
    cli("batch", "set", "--kind", "data_slot", "--property", "width", "--value", "140")
    cli("align", "--ids", "CARD_A,CARD_B", "--mode", "top")
    cli("distribute", "--ids", "CARD_A,CARD_B", "--axis", "x", "--gap", "24")
    snapshot = cli("status")
    cli("batch", "set", "--kind", "equipment_card", "--property", "width", "--value", "-1", expected=2)
    cli("--expected-revision", "0", "batch", "move", "--dx", "20", expected=3)
    assert cli("status") == snapshot
    semantic = run / "semantic.svg"
    cli("render", "--page", "components", "--output", semantic)
    assert not cli("sync-from-svg", semantic)["committed"]
    root = safe_parse(semantic.read_bytes())
    identity_map(root)["VALUE"].find('.//*[@data-eas-part="caption"]').text = "-- design kPa"
    edited = run / "edited.svg"
    edited.write_bytes(etree.tostring(root, encoding="utf-8", xml_declaration=True))
    preview = cli("sync-from-svg", edited, "--dry-run")
    assert len(preview["changes"]) == 1
    cli("sync-from-svg", edited)
    save(run / "human-diff.json", cli("diff", "--json"))
    assert cli("inspect", "VALUE")["target"]["text"] == "-- design kPa"
    cli("render", "--output-dir", run / "rendered")
    manifest = cli("export-manifest", "--output-dir", run / "bindings")
    assert manifest["rows"] == 5
    assets = cli(
        "export-assets",
        "--template",
        "PUMP",
        "--width",
        "180",
        "--height",
        "100",
        "--output-dir",
        run / "assets",
    )
    assert len(assets["assets"]) == 6
    cli("export-assets", "--page", "spare", "--formats", "svg", "--output-dir", run / "page-assets")
    cli(
        "export-assets",
        "--id",
        "IMAGE",
        "--formats",
        "png",
        "--width",
        "32",
        "--height",
        "32",
        "--output-dir",
        run / "image-assets",
    )
    cli("export-assets", "--formats", "jpg", expected=2)
    cli("export-manifest", "--output-dir", project / "history", expected=2)
    events = cli("events", "--since-revision", "0")
    save(run / "events.json", events)
    assert cli("watch", "--since-revision", "0", "--once", "--json", jsonl=True) == events["events"]
    imported = run / "imported-project"
    cli("import-svg", semantic, target=imported)
    cli("import-svg", semantic, target=imported, expected=2)
    imported_status = cli("status", target=imported)
    ordinary = run / "ordinary.svg"
    ordinary.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100"><path id="synthetic-icon" d="M10 10h80v70h-80z" fill="#14b8a6"/></svg>',
        encoding="utf-8",
    )
    original = ordinary.read_bytes()
    cli("import-svg", ordinary, target=imported)
    cli("undo", target=imported)
    assert cli("status", target=imported)["canonical_hash"] == imported_status["canonical_hash"]
    assert ordinary.read_bytes() == original
    assert cli("validate", "--strict")["ok"]
    final = cli("status")
    (run / "final-model.json").write_text(Store(project).load().model_dump_json(indent=2), encoding="utf-8")
    operations = cli("history", "--limit", "1000")["operations"]
    junit = etree.parse(str(run / "tests.xml")).getroot()[0]
    coverage = json.loads((run / "coverage.json").read_text(encoding="utf-8"))
    report = dict(
        stage=4,
        status="passed",
        project_directory=str(project),
        run_directory=str(run),
        tests=dict(junit.attrib),
        coverage_percent=coverage["totals"]["percent_covered"],
        cli_calls=len(transcript),
        final=final,
        audit_commits=len(operations),
        assets=len(assets["assets"]),
        manifest_rows=manifest["rows"],
        gui_calls=0,
        scope="Stage 4 functionality; large synthetic DC demo and final clean install remain stages 5–6",
    )
    save(run / "report.json", report)
    save(ROOT / "build/stage4/latest.json", {"run": str(run), "report": str(run / "report.json")})
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
