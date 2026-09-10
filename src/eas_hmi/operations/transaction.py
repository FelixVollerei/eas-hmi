from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from filelock import FileLock
from pydantic import ValidationError

from ..errors import EngineeringError
from ..model import Project, canonical_hash
from ..model.core import canonical_json
from ..validation import validate
from .history import objects, semantic_diff


def digest(data: bytes):
    return hashlib.sha256(data).hexdigest()


def atomic_write(path: Path, data: str | bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("wb") as f:
            f.write(data.encode("utf-8") if isinstance(data, str) else data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def checked(project):
    try:
        project = Project.model_validate(project.model_dump())
        canonical_json(project)  # Also reject non-JSON/nonfinite values in free metadata.
    except (ValidationError, ValueError, TypeError) as exc:
        raise EngineeringError(
            "VALIDATION_FAILED", "Candidate schema or JSON representation is invalid", str(exc)
        ) from exc
    issues = validate(project)
    if any(i["severity"] == "ERROR" for i in issues):
        raise EngineeringError("VALIDATION_FAILED", "Transaction rejected; no changes committed", issues)
    return project, issues


def check_actor(actor):
    if actor not in ("agent", "human", "system"):
        raise EngineeringError("INVALID_ACTOR", f"Unknown actor: {actor}")


class Store:
    """Immutable commit records; atomic HEAD is the only commit boundary.

    JSONL history/events are checked, reconstructable projections. OS-backed
    locks serialize readers/recovery/writers and release when a process dies.
    Checksums detect corruption; they are not a defense against a privileged editor.
    """

    def __init__(self, root, lock_timeout=10):
        self.root = Path(root).resolve()
        self.head = self.root / "HEAD.json"
        self.commits = self.root / ".eas" / "commits"
        self.lock_timeout = lock_timeout

    def lock(self):
        if not self.root.exists():
            raise EngineeringError("PROJECT_NOT_FOUND", f"Project directory does not exist: {self.root}")
        return FileLock(str(self.root / ".eas.lock"), timeout=self.lock_timeout)

    def _read_head(self):
        if not self.head.exists():
            raise EngineeringError("PROJECT_NOT_FOUND", f"No HEAD.json in {self.root}")
        try:
            head = json.loads(self.head.read_text(encoding="utf-8"))
            current = self._commit(head["commit"], head.get("sha256"))
            if head["revision"] != current["model"]["revision"]:
                raise ValueError("HEAD revision does not match its commit")
            return current
        except (KeyError, ValueError, TypeError, OSError) as exc:
            raise EngineeringError("STORE_CORRUPT", f"Cannot load HEAD: {exc}") from exc

    def _commit(self, transaction_id, expected_digest=None):
        try:
            name = str(uuid.UUID(transaction_id))
            raw = (self.commits / f"{name}.json").read_bytes()
            if expected_digest and digest(raw) != expected_digest:
                raise ValueError("Commit checksum mismatch")
            commit = json.loads(raw)
            model = Project.model_validate(commit["model"])
            op = commit["operation"]
            if commit["transaction_id"] != name or op["transaction_id"] != name:
                raise ValueError("Commit identity mismatch")
            if model.revision != op["revision_after"] or canonical_hash(model) != op["hash_after"]:
                raise ValueError("Commit revision or semantic hash mismatch")
            return commit
        except (KeyError, ValueError, TypeError, OSError) as exc:
            raise EngineeringError("STORE_CORRUPT", f"Cannot load committed record: {exc}") from exc

    def _chain(self, current):
        chain, seen = [current], {current["transaction_id"]}
        while chain[-1]["parent"]:
            child = chain[-1]
            if child["parent"] in seen:
                raise EngineeringError("STORE_CORRUPT", "Commit ancestry contains a cycle")
            parent = self._commit(child["parent"], child.get("parent_sha256"))
            if (
                parent["model"]["revision"] + 1 != child["model"]["revision"]
                or parent["operation"]["hash_after"] != child["operation"]["hash_before"]
            ):
                raise EngineeringError("STORE_CORRUPT", "Commit ancestry revision or hash mismatch")
            seen.add(parent["transaction_id"])
            chain.append(parent)
        return list(reversed(chain))

    def _recover(self, current):
        marker = self.root / "history" / "revision.json"
        names = ("operations.jsonl", "events.jsonl")
        try:
            state = json.loads(marker.read_text(encoding="utf-8"))
            if state.get("commit") == current["transaction_id"] and all(
                digest((self.root / "history" / name).read_bytes()) == state["sha256"][name] for name in names
            ):
                return
        except (OSError, ValueError, KeyError, TypeError):
            pass  # Projection is absent/stale/corrupt. Recover from authoritative records.
        chain = self._chain(current)
        payloads = {
            "operations.jsonl": "".join(canonical_json(c["operation"]) + "\n" for c in chain),
            "events.jsonl": "".join(canonical_json(e) + "\n" for c in chain for e in c["events"]),
        }
        for name, data in payloads.items():
            atomic_write(self.root / "history" / name, data)
        atomic_write(
            marker,
            canonical_json(
                {
                    "commit": current["transaction_id"],
                    "sha256": {name: digest(data.encode("utf-8")) for name, data in payloads.items()},
                }
            ),
        )

    def load(self):
        with self.lock():
            current = self._read_head()
            self._recover(current)
            return Project.model_validate(current["model"])

    def initialize(self, project, actor="system", command="init"):
        check_actor(actor)
        project, _ = checked(project)
        self.root.mkdir(parents=True, exist_ok=True)
        with self.lock():
            if self.head.exists():
                raise EngineeringError("PROJECT_EXISTS", f"Project already exists: {self.root}")
            return self._publish(None, None, project, actor, command)

    def transaction(self, command, mutate, actor="agent", expected_revision=None):
        check_actor(actor)
        with self.lock():
            current = self._read_head()
            self._recover(current)
            before = Project.model_validate(current["model"])
            if expected_revision is not None and before.revision != expected_revision:
                raise EngineeringError(
                    "REVISION_CONFLICT", f"Expected revision {expected_revision}; found {before.revision}"
                )
            candidate = before.model_copy(deep=True)
            try:
                replacement = mutate(candidate)
            except ValidationError as exc:
                raise EngineeringError(
                    "VALIDATION_FAILED", "Candidate schema validation failed", str(exc)
                ) from exc
            if isinstance(replacement, Project):
                candidate = replacement
            if candidate.id != before.id or candidate.revision != before.revision:
                raise EngineeringError(
                    "IDENTITY_NOT_EDITABLE", "Project identity and audit revision cannot be directly edited"
                )
            candidate, _ = checked(candidate)
            if canonical_hash(before) == canonical_hash(candidate):
                return {"ok": True, "committed": False, "revision": before.revision, "changes": []}
            return self._publish(current, before, candidate, actor, command)

    def _publish(self, current, before, candidate, actor, command):
        candidate, issues = checked(candidate)
        candidate.revision = (before.revision + 1) if before else 0
        transaction_id = str(uuid.uuid4())
        changes = semantic_diff(before, candidate)
        left, right = objects(before), objects(candidate)
        keys = [f"{c['object_type']}:{c['object_id']}" for c in changes]
        operation = {
            "transaction_id": transaction_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "actor": actor,
            "command": command,
            "affected_ids": sorted({c["object_id"] for c in changes}),
            "before": {k: left.get(k) for k in keys},
            "after": {k: right.get(k) for k in keys},
            "validation": issues,
            "revision_before": before.revision if before else None,
            "revision_after": candidate.revision,
            "changes": changes,
            "hash_before": canonical_hash(before) if before else None,
            "hash_after": canonical_hash(candidate),
        }
        events = [
            {
                "event": c["object_type"] + ".changed",
                "transaction_id": transaction_id,
                "revision": candidate.revision,
                **c,
            }
            for c in changes
        ]
        parent_id = current["transaction_id"] if current else None
        commit = {
            "transaction_id": transaction_id,
            "parent": parent_id,
            "parent_sha256": digest((self.commits / f"{parent_id}.json").read_bytes()) if parent_id else None,
            "model": candidate.model_dump(mode="json"),
            "operation": operation,
            "events": events,
        }
        raw = canonical_json(commit)
        atomic_write(self.commits / f"{transaction_id}.json", raw)
        atomic_write(
            self.head,
            canonical_json(
                {
                    "commit": transaction_id,
                    "revision": candidate.revision,
                    "sha256": digest(raw.encode("utf-8")),
                }
            ),
        )
        warnings = []
        try:
            self._recover(commit)
        except OSError as exc:
            warnings.append(f"Committed; history projection will recover on next open: {exc}")
        return {
            "ok": True,
            "committed": True,
            "revision": candidate.revision,
            "transaction_id": transaction_id,
            "changes": changes,
            "warnings": warnings,
        }

    def history(self):
        with self.lock():
            current = self._read_head()
            self._recover(current)
            return [c["operation"] for c in self._chain(current)]

    def events_since(self, revision):
        with self.lock():
            current = self._read_head()
            self._recover(current)
            return [e for c in self._chain(current) for e in c["events"] if e["revision"] > revision]

    def undo(self, actor="agent", expected_revision=None):
        check_actor(actor)
        with self.lock():
            current = self._read_head()
            self._recover(current)
            before = Project.model_validate(current["model"])
            if expected_revision is not None and before.revision != expected_revision:
                raise EngineeringError("REVISION_CONFLICT", "Revision changed before undo")
            if current["parent"] is None:
                raise EngineeringError("NOTHING_TO_UNDO", "Initialization cannot be undone")
            parent = self._commit(current["parent"], current.get("parent_sha256"))
            restored = Project.model_validate(parent["model"])
            return self._publish(current, before, restored, actor, f"undo {current['transaction_id']}")
