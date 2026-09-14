"""Hash-verified immutable files; manifest replacement is the commit point."""

import hashlib
import json
import os
import uuid
from pathlib import Path

from .errors import require


def encoded(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def atomic(path, data):
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temp.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


class Storage:
    def __init__(self, root):
        raw = Path(root).absolute()
        require(not raw.is_symlink(), "UNSAFE_PATH", "Project root cannot be a symlink")
        self.root = raw.resolve()

    def path(self, name):
        require(
            isinstance(name, str) and not Path(name).is_absolute() and ".." not in Path(name).parts,
            "UNSAFE_PATH",
            "Project references must be relative without ..",
        )
        target = self.root / name
        require(target.resolve().is_relative_to(self.root), "UNSAFE_PATH", "Reference escapes project")
        require(
            not any(p.is_symlink() for p in [target, *target.parents] if p != self.root.parent),
            "UNSAFE_PATH",
            "Symlink reference rejected",
        )
        return target

    def asset(self, folder, data, suffix):
        digest = sha(data)
        relative = f"{folder}/{digest}.{suffix}"
        path = self.path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            require(sha(path.read_bytes()) == digest, "CORRUPT_ASSET", relative)
        else:
            with path.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        return {"path": relative, "sha256": digest}

    def read(self, ref):
        path = self.path(ref["path"])
        require(path.is_file(), "MISSING_ASSET", ref["path"])
        data = path.read_bytes()
        require(sha(data) == ref["sha256"], "CORRUPT_ASSET", ref["path"])
        return data

    def verify_refs(self, value, seen=None):
        seen = set() if seen is None else seen
        if isinstance(value, dict):
            if set(value) == {"path", "sha256"}:
                key = (value["path"], value["sha256"])
                if key not in seen:
                    self.read(value)
                    seen.add(key)
            else:
                for item in value.values():
                    self.verify_refs(item, seen)
        elif isinstance(value, list):
            for item in value:
                self.verify_refs(item, seen)

    def write_manifest(self, manifest):
        manifest = dict(manifest)
        manifest.pop("checksum", None)
        manifest["checksum"] = sha(encoded(manifest))
        atomic(self.path("manifest.json"), encoded(manifest))

    def load(self):
        path = self.path("manifest.json")
        require(path.is_file(), "NOT_A_PROJECT", "manifest.json not found")
        manifest = json.loads(path.read_bytes())
        checksum = manifest.pop("checksum")
        require(checksum == sha(encoded(manifest)), "CORRUPT_MANIFEST", "Manifest checksum mismatch")
        require(manifest["version"] == 1, "UNSUPPORTED_VERSION", "Unsupported project format")
        records, seen = {}, set()
        self.verify_refs(manifest["baseline"], seen)
        for edit_id, ref in manifest["revisions"].items():
            record = json.loads(self.read(ref))
            require(record["edit_id"] == edit_id, "CORRUPT_HISTORY", "History ID mismatch")
            self.verify_refs(record, seen)
            records[edit_id] = record
        require(
            manifest["current_revision"] in records and manifest["genesis"] in records,
            "CORRUPT_HISTORY",
            "Unknown current/genesis revision",
        )
        for edit_id in records:
            visited, cursor = set(), edit_id
            while cursor is not None:
                require(
                    cursor in records and cursor not in visited,
                    "CORRUPT_HISTORY",
                    "Missing parent or history cycle",
                )
                visited.add(cursor)
                cursor = records[cursor]["parent"]
        require(
            records[manifest["genesis"]]["baseline"] == manifest["baseline"],
            "CORRUPT_MANIFEST",
            "Baseline differs from genesis",
        )
        require(all(r in records for r in manifest["redo"]), "CORRUPT_HISTORY", "Invalid redo reference")
        for ref in manifest["requests"].values():
            self.verify_refs(json.loads(self.read(ref)), seen)
        return manifest, records
