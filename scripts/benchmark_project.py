"""Measure complete CLI process latency, with correctness checks and raw samples."""

import argparse
import hashlib
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def benchmark(project, output, samples=20, warmups=3, threshold_ms=1000):
    if samples < 20 or warmups < 0 or threshold_ms <= 0:
        raise ValueError("Use at least 20 samples, nonnegative warmups and a positive threshold")
    project, output = Path(project).resolve(), Path(output).resolve()
    executable = Path(sys.executable).parent / ("eas-hmi.exe" if os.name == "nt" else "eas-hmi")
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONNOUSERSITE": "1"}

    def call(arguments):
        command = [str(executable), "--project", str(project), *arguments]
        start = time.perf_counter()
        result = subprocess.run(
            command, env=env, cwd=output.parent, capture_output=True, encoding="utf-8", timeout=30
        )
        elapsed = (time.perf_counter() - start) * 1000
        if result.returncode:
            raise RuntimeError(f"CLI failed: {result.returncode}: {result.stdout} {result.stderr}")
        return json.loads(result.stdout), dict(
            command=command,
            milliseconds=elapsed,
            exit_code=result.returncode,
            stderr=result.stderr,
            stdout_bytes=len(result.stdout.encode("utf-8")),
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    baseline, _ = call(["status", "--json"])
    assert baseline["nodes"] >= 500 and baseline["points"] >= 300
    protected = [project / name for name in ("HEAD.json", "history/operations.jsonl", "history/events.jsonl")]
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    commands = {
        "query": [
            "query",
            "--page",
            "cooling",
            "--kind",
            "equipment_card",
            "--equipment-type",
            "pump",
            "--json",
        ],
        "validate": ["validate", "--strict", "--json"],
    }
    data = {name: dict(warmups=[], samples=[]) for name in commands}
    # Alternate commands to avoid putting all measurements of one command at one time.
    for i in range(warmups + samples):
        for name, arguments in commands.items():
            result, sample = call(arguments)
            if name == "query":
                assert result["count"] == 20 and len(result["nodes"]) == 20
            else:
                assert result == {"ok": True, "issues": []}
            sample["index"] = i - warmups + 1 if i >= warmups else i + 1
            data[name]["samples" if i >= warmups else "warmups"].append(sample)
    for name, measurements in data.items():
        values = [s["milliseconds"] for s in measurements["samples"]]
        p95 = sorted(values)[math.ceil(0.95 * len(values)) - 1]
        measurements["summary"] = dict(
            count=len(values),
            p50_ms=statistics.median(values),
            p95_ms=p95,
            min_ms=min(values),
            max_ms=max(values),
            mean_ms=statistics.mean(values),
            threshold_ms=threshold_ms,
            passed=p95 <= threshold_ms,
        )
    after, _ = call(["status", "--json"])
    assert baseline == after
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == h for p, h in before.items())
    report = dict(
        status="passed" if all(v["summary"]["passed"] for v in data.values()) else "failed",
        measured_at_utc=datetime.now(timezone.utc).isoformat(),
        project=baseline,
        environment=dict(
            platform=platform.platform(),
            processor=platform.processor(),
            logical_cpus=os.cpu_count(),
            python=sys.version,
            executable=sys.executable,
        ),
        methodology="Wall time per new installed CLI process including startup, committed project/history read, operation and JSON capture; three warmups per command excluded; alternating query/validate; nearest-rank p95; no rasterization or LLM",
        samples_per_command=samples,
        warmups_per_command=warmups,
        threshold_ms=threshold_ms,
        data=data,
        committed_files_unchanged=True,
        file_hashes=before,
    )
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--warmups", type=int, default=3)
    parser.add_argument("--threshold-ms", type=float, default=1000)
    args = parser.parse_args()
    result = benchmark(args.project, args.output, args.samples, args.warmups, args.threshold_ms)
    print(
        json.dumps(
            {"status": result["status"], "commands": {k: v["summary"] for k, v in result["data"].items()}},
            indent=2,
        )
    )
    raise SystemExit(0 if result["status"] == "passed" else 1)
