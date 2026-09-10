"""Capture one CLI invocation and HEAD evidence. Never retries a write."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def snapshot(project):
    head = project / "HEAD.json"
    if not head.exists():
        return {"exists": False}
    raw = head.read_bytes()
    data = {"exists": True, "head_sha256": hashlib.sha256(raw).hexdigest()}
    try:
        data["head"] = json.loads(raw)
    except ValueError:
        data["parse_error"] = True
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("provide one CLI command after --")
    args.output.mkdir(parents=True, exist_ok=False)
    argv = [
        sys.executable,
        "-X",
        "faulthandler",
        "-m",
        "eas_hmi.cli",
        "--project",
        str(args.project.resolve()),
        *command,
    ]
    report = {
        "argv": argv,
        "start_utc": datetime.now(timezone.utc).isoformat(),
        "before": snapshot(args.project),
        "python": sys.version,
        "automatic_retry": False,
    }
    report_path = args.output / "diagnostic.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    started = time.perf_counter()
    with (args.output / "stdout.log").open("wb") as stdout, (args.output / "stderr.log").open("wb") as stderr:
        try:
            result = subprocess.run(
                argv,
                stdout=stdout,
                stderr=stderr,
                timeout=args.timeout,
                env=dict(os.environ, PYTHONUTF8="1"),
                check=False,
            )
            report.update(exit_code=result.returncode, exit_hex=f"0x{result.returncode & 0xFFFFFFFF:08X}")
        except subprocess.TimeoutExpired:
            report.update(timeout=True, exit_code=None)
    report.update(
        wall_seconds=time.perf_counter() - started,
        after=snapshot(args.project),
        end_utc=datetime.now(timezone.utc).isoformat(),
    )
    report["head_changed"] = report["before"] != report["after"]
    report["interpretation"] = (
        "HEAD comparison is evidence, not proof that retry is safe. Inspect status/history before any manual retry."
    )
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(report_path.resolve()))
    return 0 if report.get("exit_code") == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
