"""Package all authored project files plus successful stage evidence; verify every archive byte."""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", type=int, choices=(4, 5, 6), default=6)
    stage_number = parser.parse_args().stage
    files = set()
    for name in ("src", "tests", "scripts", "docs", "examples", "schemas"):
        files.update(
            path
            for path in (ROOT / name).rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and not any(p.endswith(".egg-info") for p in path.parts)
        )
    files.update(ROOT / name for name in ("README.md", "pyproject.toml", ".gitignore"))
    if (ROOT / "requirements-tested.txt").is_file():
        files.add(ROOT / "requirements-tested.txt")
    evidence = []
    for stage in range(2, stage_number + 1):
        latest = ROOT / f"build/stage{stage}/latest.json"
        pointer = json.loads(latest.read_text(encoding="utf-8"))
        # Evidence pointers retain original machine paths. Resolve by run name so
        # an extracted delivery can be repackaged from its current project root.
        run_name = (
            (pointer.get("run") or pointer["run_directory"]).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        )
        if run_name in ("", ".", ".."):
            raise ValueError("Invalid evidence directory name")
        run = ROOT / "build" / f"stage{stage}" / run_name
        if not run.resolve().is_relative_to(ROOT / "build"):
            raise ValueError("Evidence path is outside project build directory")
        files.add(latest)
        files.update(p for p in run.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
        evidence.append(run.relative_to(ROOT).as_posix())
    inventory = []
    output_dir = ROOT / "deliverables"
    output_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = output_dir / f"eas-hmi-stage{stage_number}-{stamp}.zip"
    with ZipFile(archive, "w", compression=ZIP_DEFLATED, compresslevel=6) as zipped:
        for path in sorted(files):
            data = path.read_bytes()
            relative = path.relative_to(ROOT).as_posix()
            inventory.append(dict(path=relative, bytes=len(data), sha256=sha256(data)))
            zipped.writestr("eas-hmi/" + relative, data)
        manifest = dict(
            stage=stage_number,
            files=inventory,
            evidence=evidence,
            excludes=[
                ".venv",
                "Python/tool caches",
                "editable install egg-info",
                "failed/superseded build runs",
                "previous delivery archives",
            ],
            restore="Extract; Python 3.12+: python -m venv .venv; .venv/Scripts/python.exe -m pip install -e .[dev]. Install Inkscape CLI for raster tests/exports.",
        )
        zipped.writestr("eas-hmi/PACKAGE-MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    with ZipFile(archive) as zipped:
        assert zipped.testzip() is None
        for item in inventory:
            assert sha256(zipped.read("eas-hmi/" + item["path"])) == item["sha256"], item["path"]
        assert len(zipped.namelist()) == len(inventory) + 1
    digest = sha256(archive.read_bytes())
    checksum = archive.with_suffix(".zip.sha256")
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    report = dict(
        archive=str(archive),
        sha256=digest,
        bytes=archive.stat().st_size,
        authored_and_evidence_files=len(inventory),
        archive_entries=len(inventory) + 1,
        zip_integrity="passed",
        per_file_hash_verification="passed",
        evidence=evidence,
    )
    (output_dir / "latest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
