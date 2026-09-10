"""Final acceptance in independent editable and wheel environments.

The environments/checkouts live outside the evidence directory and are not deliverable source files.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    if sys.flags.optimize:
        raise RuntimeError("Acceptance must run without -O: assertions are required")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    run = ROOT / "build/stage6" / run_id
    checkout = ROOT / "build/clean-checkouts" / run_id
    environments = ROOT / "build/environments" / run_id
    run.mkdir(parents=True)
    checkout.mkdir(parents=True)
    source_hashes = {}
    for name in ("src", "tests", "scripts", "examples", "schemas", "docs"):
        for path in (ROOT / name).rglob("*"):
            if (
                not path.is_file()
                or "__pycache__" in path.parts
                or any(part.endswith(".egg-info") for part in path.parts)
            ):
                continue
            relative = path.relative_to(ROOT)
            target = checkout / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            source_hashes[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name in ("pyproject.toml", "README.md", ".gitignore"):
        shutil.copyfile(ROOT / name, checkout / name)
        source_hashes[name] = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
    save(run / "source-hashes.json", source_hashes)
    environment = {
        k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")
    }
    environment.update(PYTHONUTF8="1", PYTHONNOUSERSITE="1", PIP_DISABLE_PIP_VERSION_CHECK="1")
    steps = []

    def execute(name, command, cwd=None, timeout=600):
        result = subprocess.run(
            list(map(str, command)),
            cwd=cwd or checkout,
            env=environment,
            capture_output=True,
            encoding="utf-8",
            timeout=timeout,
        )
        log = run / "logs" / (name + ".log")
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(result.stdout + result.stderr, encoding="utf-8")
        steps.append(
            dict(
                name=name,
                command=list(map(str, command)),
                cwd=str(cwd or checkout),
                exit_code=result.returncode,
                log=str(log),
            )
        )
        save(run / "steps.json", steps)
        if result.returncode:
            print(result.stdout[-12000:] + result.stderr[-3000:])
            raise RuntimeError(f"{name} failed with exit {result.returncode}; see {log}")
        print(f"{name}: passed", flush=True)
        return result.stdout

    def python_in(directory):
        return directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    editable_env, wheel_env = environments / "editable", environments / "wheel"
    editable, wheel_python = python_in(editable_env), python_in(wheel_env)
    execute("create-editable-env", [sys.executable, "-m", "venv", editable_env])
    execute("install-editable", [editable, "-m", "pip", "install", "-e", ".[dev]"])
    execute("editable-pip-check", [editable, "-m", "pip", "check"])
    freeze = execute("editable-freeze", [editable, "-m", "pip", "freeze", "--exclude-editable"])
    (run / "requirements-tested.txt").write_text(freeze, encoding="utf-8")
    identity_code = (
        "import json,sys,site,eas_hmi; from pathlib import Path; "
        "from eas_hmi.export.assets import InkscapeBackend; "
        "print(json.dumps(dict(python=sys.version,executable=sys.executable,prefix=sys.prefix,base_prefix=sys.base_prefix,"
        "package_path=eas_hmi.__file__,user_site=site.ENABLE_USER_SITE,inkscape=InkscapeBackend().version())))"
    )
    editable_identity = json.loads(
        execute("editable-identity", [editable, "-I", "-c", identity_code], cwd=run)
    )
    assert Path(editable_identity["package_path"]).is_relative_to(checkout / "src")
    assert (
        editable_identity["prefix"] != editable_identity["base_prefix"] and not editable_identity["user_site"]
    )
    editable_cli = editable.parent / ("eas-hmi.exe" if os.name == "nt" else "eas-hmi")
    execute("editable-help", [editable_cli, "--help"], cwd=run)
    required = (
        "init",
        "inspect",
        "query",
        "context",
        "set",
        "move",
        "resize",
        "bind",
        "unbind",
        "align",
        "distribute",
        "batch",
        "validate",
        "history",
        "diff",
        "undo",
        "render",
        "import-svg",
        "sync-from-svg",
        "export-manifest",
        "export-assets",
        "events",
        "watch",
    )
    for command in required:
        execute("help-" + command, [editable_cli, command, "--help"], cwd=run)
    execute(
        "pytest",
        [
            editable,
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
        ],
    )
    execute("editable-demo", [editable, "scripts/demo_agent_workflow.py", "--output", run / "editable-demo"])
    execute("build-wheel", [editable, "-m", "pip", "wheel", "--no-deps", "--wheel-dir", run / "dist", "."])
    wheels = list((run / "dist").glob("eas_hmi-*.whl"))
    assert len(wheels) == 1
    execute("create-wheel-env", [sys.executable, "-m", "venv", wheel_env])
    execute("install-wheel", [wheel_python, "-m", "pip", "install", wheels[0]], cwd=run)
    execute("wheel-pip-check", [wheel_python, "-m", "pip", "check"], cwd=run)
    execute("wheel-freeze", [wheel_python, "-m", "pip", "freeze"], cwd=run)
    wheel_identity = json.loads(execute("wheel-identity", [wheel_python, "-I", "-c", identity_code], cwd=run))
    assert Path(wheel_identity["package_path"]).is_relative_to(wheel_env)
    assert wheel_identity["prefix"] != wheel_identity["base_prefix"] and not wheel_identity["user_site"]
    wheel_cli = wheel_python.parent / ("eas-hmi.exe" if os.name == "nt" else "eas-hmi")
    execute("wheel-help", [wheel_cli, "--help"], cwd=run)
    execute(
        "wheel-demo",
        [wheel_python, checkout / "scripts/demo_agent_workflow.py", "--output", run / "wheel-demo"],
        cwd=run,
    )
    execute(
        "benchmark",
        [
            wheel_python,
            checkout / "scripts/benchmark_project.py",
            "--project",
            run / "wheel-demo/project",
            "--output",
            run / "performance.json",
            "--samples",
            "20",
            "--warmups",
            "3",
        ],
        cwd=run,
    )
    from xml.etree import ElementTree

    tests = ElementTree.parse(run / "tests.xml").getroot()[0].attrib
    coverage = json.loads((run / "coverage.json").read_text(encoding="utf-8"))
    workflows = {
        label: json.loads((run / f"{label}-demo/report.json").read_text(encoding="utf-8"))
        for label in ("editable", "wheel")
    }
    assert workflows["editable"]["final"] == workflows["wheel"]["final"]
    assert workflows["editable"]["source"] == workflows["wheel"]["source"]
    performance = json.loads((run / "performance.json").read_text(encoding="utf-8"))
    wheel_hash = hashlib.sha256(wheels[0].read_bytes()).hexdigest()
    report = dict(
        stage=6,
        status="passed",
        tests=tests,
        coverage_percent=coverage["totals"]["percent_covered"],
        clean_install=dict(editable=editable_identity, wheel=wheel_identity, all_steps_passed=True),
        wheel=dict(path=str(wheels[0]), sha256=wheel_hash),
        source=workflows["wheel"]["source"],
        final=workflows["wheel"]["final"],
        workflow_calls={k: v["cli_calls"] for k, v in workflows.items()},
        tasks={k: {t: item["status"] for t, item in v["tasks"].items()} for k, v in workflows.items()},
        reproducible_semantic_result=True,
        performance={k: v["summary"] for k, v in performance["data"].items()},
        fixture_cases=workflows["wheel"]["fixture_cases"],
        run_directory=str(run),
        checkout=str(checkout),
        environment_directory=str(environments),
    )
    save(run / "report.json", report)
    save(ROOT / "build/stage6/latest.json", dict(run_directory=str(run), report=str(run / "report.json")))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
