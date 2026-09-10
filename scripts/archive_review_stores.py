"""Archive completed review stores, verify bytes, and move originals outside source."""

import hashlib
import json
import shutil
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]


def main():
    source_root = (ROOT / "build/review").resolve()
    destination_root = (ROOT.parent / "eas-hmi-local-artifacts/review-stores").resolve()
    for name in ("history", "history-followup"):
        run = source_root / name
        archive = run / "store-evidence.zip"
        if archive.exists():
            raise FileExistsError(archive)
        sources = [(run / part).resolve() for part in ("project", "baseline-source")]
        if not all(p.is_relative_to(source_root) and p.is_dir() for p in sources):
            raise ValueError("Missing source or source outside review root")
        destinations = [(destination_root / name / p.name).resolve() for p in sources]
        if not all(p.is_relative_to(destination_root) and not p.exists() for p in destinations):
            raise ValueError("Destination exists or is outside archive root")
        inventory = []
        with ZipFile(archive, "w", compression=ZIP_DEFLATED) as zipped:
            for source in sources:
                for path in sorted(source.rglob("*")):
                    if path.is_file() and "__pycache__" not in path.parts:
                        data = path.read_bytes()
                        relative = path.relative_to(run).as_posix()
                        inventory.append({"path": relative, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
                        zipped.writestr(relative, data)
            zipped.writestr("manifest.json", json.dumps(inventory, indent=2))
        with ZipFile(archive) as zipped:
            assert zipped.testzip() is None
            for item in inventory:
                assert hashlib.sha256(zipped.read(item["path"])).hexdigest() == item["sha256"]
        # Paths were resolved and checked against both intended roots above.
        for source, destination in zip(sources, destinations, strict=True):
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
        record = {"archive": str(archive), "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                  "bytes": archive.stat().st_size, "uncompressed_bytes": sum(i["bytes"] for i in inventory),
                  "verified_files": len(inventory), "originals_moved_to": list(map(str, destinations)),
                  "restore": "Extract this ZIP into its performance.json directory to restore project/ and baseline-source/."}
        (run / "archive.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        print(json.dumps(record))


if __name__ == "__main__":
    main()
