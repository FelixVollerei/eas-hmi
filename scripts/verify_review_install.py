"""Build 0.1.1 and exercise a fresh wheel environment with all CLI help and A-G."""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    out = ROOT / "build/review/install"
    out.mkdir(parents=True, exist_ok=False)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    environment = ROOT.parent / "eas-hmi-local-artifacts" / "review-environments" / stamp
    python = environment / "Scripts/python.exe"
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")}
    env.update(PYTHONUTF8="1", PYTHONNOUSERSITE="1", PIP_DISABLE_PIP_VERSION_CHECK="1")
    steps = []

    def run(name, argv):
        result = subprocess.run(list(map(str, argv)), cwd=ROOT, env=env, capture_output=True,
                                encoding="utf-8", timeout=600, check=False)
        (out / f"{name}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        steps.append({"name": name, "argv": list(map(str, argv)), "exit_code": result.returncode})
        (out / "steps.json").write_text(json.dumps(steps, indent=2), encoding="utf-8")
        if result.returncode:
            raise RuntimeError(f"{name}: exit {result.returncode}; no automatic retry")
        print(name + ": passed", flush=True)
        return result.stdout

    run("build-wheel", [sys.executable, "-m", "pip", "wheel", "--no-deps", ".", "--wheel-dir", ROOT / "dist"])
    run("create-env", [sys.executable, "-m", "venv", environment])
    run("install-wheel", [python, "-m", "pip", "install", ROOT / "dist/eas_hmi-0.1.1-py3-none-any.whl"])
    run("pip-check", [python, "-m", "pip", "check"])
    identity = json.loads(run("identity", [python, "-I", "-c",
        "import eas_hmi,sys,json;print(json.dumps(dict(version=eas_hmi.__version__,package=eas_hmi.__file__,python=sys.executable)))"]))
    assert identity["version"] == "0.1.1" and Path(identity["package"]).is_relative_to(environment)
    cli = environment / "Scripts/eas-hmi.exe"
    commands = ("init", "status", "inspect", "query", "context", "set", "move", "resize", "bind", "unbind",
                "align", "distribute", "batch", "validate", "history", "diff", "undo", "render", "import-svg",
                "sync-from-svg", "export-manifest", "export-assets", "events", "watch")
    run("help", [cli, "--help"])
    for command in commands:
        run("help-" + command, [cli, command, "--help"])
    run("demo", [python, ROOT / "scripts/demo_agent_workflow.py", "--output", ROOT / "build/review/wheel-demo"])
    (out / "result.json").write_text(json.dumps({"status": "passed", "identity": identity,
                                                 "subcommands": len(commands), "steps": len(steps)}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
