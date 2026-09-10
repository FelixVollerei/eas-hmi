"""Stage 5 acceptance: full tests and coverage followed by a fresh installed-CLI A-G workflow."""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from demo_agent_workflow import run_workflow, save
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]


def main():
    run = (
        ROOT
        / "build/stage5"
        / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8])
    )
    run.mkdir(parents=True)
    command = [
        sys.executable,
        "-m",
        "pytest",
        "tests",
        "-q",
        "--tb=short",
        "--cov=eas_hmi",
        "--cov-fail-under=85",
        f"--junitxml={run / 'tests.xml'}",
        f"--cov-report=json:{run / 'coverage.json'}",
        f"--cov-report=xml:{run / 'coverage.xml'}",
        "--cov-report=term-missing",
    ]
    result = subprocess.run(
        command,
        cwd=ROOT,
        env={**os.environ, "PYTHONUTF8": "1"},
        capture_output=True,
        encoding="utf-8",
        timeout=600,
    )
    (run / "tests.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode:
        print(result.stdout + result.stderr)
        raise SystemExit(result.returncode)
    print("Full pytest and coverage gate passed", flush=True)
    workflow = run_workflow(run / "workflow")
    junit = etree.parse(str(run / "tests.xml")).getroot()[0]
    coverage = json.loads((run / "coverage.json").read_text(encoding="utf-8"))
    report = dict(
        stage=5,
        status="passed",
        tests=dict(junit.attrib),
        coverage_percent=coverage["totals"]["percent_covered"],
        cli_calls=workflow["cli_calls"],
        source=workflow["source"],
        final=workflow["final"],
        tasks={task: result["status"] for task, result in workflow["tasks"].items()},
        fixture_cases=workflow["fixture_cases"],
        audit_commits=workflow["audit_commits"],
        event_count=workflow["event_count"],
        interaction=workflow["interaction"],
        workflow_seconds=workflow["elapsed_seconds"],
        run_directory=str(run),
        remaining="Stage 6: clean install, final repeatability and performance acceptance; no stage 6 work claimed here",
    )
    save(run / "report.json", report)
    save(ROOT / "build/stage5/latest.json", dict(run_directory=str(run), report=str(run / "report.json")))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
