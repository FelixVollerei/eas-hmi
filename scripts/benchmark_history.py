"""Controlled before/after history benchmark; never touches an existing project.

Uses the recorded Git delivery baseline and current source against identical
synthetic stores. This is a CLI performance test, not the future agent trial.
"""

import argparse
import hashlib
import json
import math
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

from eas_hmi.model import Project
from eas_hmi.operations.transaction import Store

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--revisions", type=int, nargs="+", default=[7, 42, 106])
    args = parser.parse_args()
    if args.samples < 2:
        parser.error("at least two samples required")
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    baseline = out / "baseline-source"
    paths = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", args.baseline, "src"], cwd=ROOT, text=True
    ).splitlines()
    for name in paths:
        data = subprocess.check_output(["git", "show", f"{args.baseline}:{name}"], cwd=ROOT)
        path = baseline / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    project = Project.model_validate_json((ROOT / "examples/datacenter/model.json").read_bytes())
    state = Store(out / "project")
    state.initialize(project)
    report = dict(
        baseline=args.baseline,
        nodes=len(project.all_nodes()),
        samples=args.samples,
        method="fresh python -m CLI process, wall time includes startup; 1 warmup per command/version/level; alternating versions; nearest-rank p95",
        measurements=[],
        failures=[],
    )

    def save():
        (out / "performance.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    for revision in sorted(set(args.revisions)):
        while state.load().revision < revision:
            state.transaction(
                "history benchmark", lambda p: p.metadata.update(benchmark_counter=p.revision + 1)
            )
        commands = {
            "query": ["query", "--page", "cooling", "--kind", "equipment_card", "--equipment-type", "pump"],
            "validate": ["validate"],
            "diff": ["diff", "--json"],
            "history": ["history", "--limit", "10"],
            "events_delta": ["events", "--since-revision", str(revision - 1)],
            "watch_empty": ["watch", "--once"],
        }
        for name, command in commands.items():
            records = {"before": [], "after": []}
            output_digest = None
            for sample in range(-1, args.samples):
                outputs = {}
                versions = ("before", "after") if sample % 2 else ("after", "before")
                for version in versions:
                    env = dict(os.environ, PYTHONUTF8="1", PYTHONNOUSERSITE="1")
                    env["PYTHONPATH"] = str((baseline if version == "before" else ROOT) / "src")
                    started = time.perf_counter()
                    result = subprocess.run(
                        [
                            sys.executable,
                            "-X",
                            "faulthandler",
                            "-m",
                            "eas_hmi.cli",
                            "--project",
                            str(state.root),
                            *command,
                        ],
                        env=env,
                        cwd=out,
                        capture_output=True,
                        timeout=30,
                        check=False,
                    )
                    elapsed = (time.perf_counter() - started) * 1000
                    if result.returncode:
                        report["failures"].append(
                            dict(
                                command=command,
                                version=version,
                                sample=sample,
                                exit=result.returncode,
                                stderr=result.stderr.decode(errors="replace"),
                            )
                        )
                        save()
                        # All measured commands are read-only. Retain this failed
                        # scheduled attempt; do not add a replacement sample.
                        continue
                    outputs[version] = hashlib.sha256(result.stdout).hexdigest()
                    if name != "watch_empty":
                        value = json.loads(result.stdout)
                        if name == "query":
                            assert value["count"] == 20
                        elif name == "validate":
                            assert value["ok"]
                        elif name == "diff":
                            assert value["revision_after"] == revision
                        elif name == "history":
                            assert len(value["operations"]) == min(10, revision + 1)
                        else:
                            assert value["events"] and all(e["revision"] == revision for e in value["events"])
                    else:
                        assert result.stdout == b""
                    if sample >= 0:
                        records[version].append(elapsed)
                if len(outputs) == 2:
                    assert outputs["before"] == outputs["after"], (revision, name)
                    output_digest = outputs["after"]
            row = dict(
                revision=revision,
                commits=revision + 1,
                command=name,
                argv=command,
                commit_bytes=sum(p.stat().st_size for p in state.commits.glob("*.json")),
                projection_bytes=sum(
                    (state.root / "history" / n).stat().st_size for n in ("operations.jsonl", "events.jsonl")
                ),
                identical_output_sha256=output_digest,
            )
            for version, times in records.items():
                row[version] = dict(
                    samples_ms=times,
                    successful_samples=len(times),
                    scheduled_samples=args.samples,
                    p50_ms=statistics.median(times) if times else None,
                    p95_ms=sorted(times)[math.ceil(len(times) * 0.95) - 1] if times else None,
                    max_ms=max(times) if times else None,
                )
            report["measurements"].append(row)
            save()
            print(
                f"r{revision} {name}: before p95={row['before']['p95_ms']}ms after={row['after']['p95_ms']}ms",
                flush=True,
            )
    assert state.load().revision == max(args.revisions)
    report["status"] = "completed_with_failures" if report["failures"] else "passed"
    save()


if __name__ == "__main__":
    main()
