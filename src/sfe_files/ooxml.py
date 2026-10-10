"""Small OPC package editor: only explicitly changed parts are serialized."""

from __future__ import annotations

import io
import posixpath
from copy import copy
from urllib.parse import unquote, urlsplit
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile, ZipInfo

from lxml import etree

from .plan_ops import FileEditError

REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
MAIN_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
}


def _xml(data: bytes, name: str):
    try:
        parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True)
        tree = etree.parse(io.BytesIO(data), parser)
        if tree.docinfo.doctype or any(isinstance(node, etree._Entity) for node in tree.iter()):
            raise ValueError("DTD and entity declarations are unsupported")
        return tree.getroot()
    except (etree.XMLSyntaxError, ValueError) as exc:
        raise FileEditError("INVALID_OFFICE_XML", f"{name}: {exc}") from exc


class Package:
    """Preserve payload bytes and ZIP metadata for every untouched package member."""

    def __init__(self):
        self._parts: dict[str, bytes] = {}
        self._infos: dict[str, ZipInfo] = {}
        self._trees: dict[str, etree._Element] = {}
        self._changed: set[str] = set()
        self._original = b""
        self._comment = b""
        self.main_part = ""

    @classmethod
    def open(cls, data: bytes, kind: str) -> Package:
        if data.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
            raise FileEditError(
                "UNSUPPORTED_OFFICE_CONTAINER", "Encrypted OOXML and legacy binary Office are unsupported"
            )
        pkg = cls()
        pkg._original = data
        try:
            with ZipFile(io.BytesIO(data)) as archive:
                pkg._comment = archive.comment
                for info in archive.infolist():
                    name = info.filename
                    if name in pkg._parts:
                        raise ValueError(f"Duplicate package part: {name}")
                    if name.startswith("/") or "\\" in name or ".." in name.split("/"):
                        raise ValueError(f"Invalid package part name: {name}")
                    pkg._parts[name] = archive.read(info)
                    pkg._infos[name] = copy(info)
        except (BadZipFile, RuntimeError, NotImplementedError, ValueError) as exc:
            raise FileEditError("INVALID_OFFICE_PACKAGE", str(exc)) from exc
        if any(name.lower().startswith("_xmlsignatures/") for name in pkg.names):
            raise FileEditError("UNSUPPORTED_OFFICE_PACKAGE", "Digitally signed packages are unsupported")
        types = pkg.xml("[Content_Types].xml")
        if types.tag != f"{{{CT}}}Types":
            raise FileEditError("INVALID_OFFICE_PACKAGE", "Missing OPC content types")
        if any(
            "macroenabled" in item.get("ContentType", "").lower()
            or "vbaproject" in item.get("ContentType", "").lower()
            for item in types
        ):
            raise FileEditError("UNSUPPORTED_OFFICE_PACKAGE", "Macro-enabled packages are unsupported")
        defaults = {
            item.get("Extension", "").lower(): item.get("ContentType")
            for item in types if item.tag == f"{{{CT}}}Default"
        }
        overrides = {
            unquote(item.get("PartName", "")).lstrip("/"): item.get("ContentType")
            for item in types if item.tag == f"{{{CT}}}Override"
        }
        related = pkg.related("")
        candidates = [
            related.get(item.get("Id"))
            for item in pkg.xml("_rels/.rels")
            if item.get("Type", "").endswith("/officeDocument")
        ]
        if len(candidates) != 1 or candidates[0] is None:
            raise FileEditError("INVALID_OFFICE_PACKAGE", f"Expected one {kind} main document part")
        pkg.main_part = candidates[0]
        content_type = overrides.get(pkg.main_part, defaults.get(pkg.main_part.rsplit(".", 1)[-1].lower()))
        if content_type != MAIN_TYPES[kind]:
            raise FileEditError("INVALID_OFFICE_PACKAGE", f"Main document content type does not match {kind}")
        return pkg

    @property
    def names(self) -> list[str]:
        return list(self._parts)

    def has(self, name: str) -> bool:
        return name in self._parts

    def read(self, name: str) -> bytes:
        if name not in self._parts:
            raise FileEditError("INVALID_OFFICE_PACKAGE", f"Missing package part: {name}")
        if name in self._changed and name in self._trees:
            return etree.tostring(self._trees[name], xml_declaration=True, encoding="UTF-8", standalone=True)
        return self._parts[name]

    def xml(self, name: str):
        if name not in self._trees:
            self._trees[name] = _xml(self.read(name), name)
        return self._trees[name]

    def mark_changed(self, name: str):
        if not self.has(name):
            raise FileEditError("INVALID_OFFICE_PACKAGE", f"Missing package part: {name}")
        self._changed.add(name)

    def write(self, name: str, data: bytes):
        self._parts[name] = data
        self._trees.pop(name, None)
        self._changed.add(name)

    def delete(self, name: str):
        if self.has(name):
            del self._parts[name]
            self._trees.pop(name, None)
            self._changed.add(name)

    def related(self, source_part: str) -> dict[str, str]:
        folder, filename = posixpath.split(source_part)
        rels_name = posixpath.join(folder, "_rels", filename + ".rels")
        if not self.has(rels_name):
            return {}
        root = self.xml(rels_name)
        if root.tag != f"{{{REL}}}Relationships":
            raise FileEditError("INVALID_OFFICE_PACKAGE", f"Invalid relationships: {rels_name}")
        result = {}
        seen = set()
        for rel in root:
            if rel.tag != f"{{{REL}}}Relationship":
                continue
            rid = rel.get("Id")
            if not rid or rid in seen:
                raise FileEditError("INVALID_OFFICE_PACKAGE", f"Duplicate or missing relationship ID: {rid}")
            seen.add(rid)
            if rel.get("TargetMode") == "External":
                continue
            target = urlsplit(rel.get("Target", ""))
            path = unquote(target.path)
            if target.scheme or target.netloc or not path or "\\" in path:
                raise FileEditError("INVALID_OFFICE_PACKAGE", f"Invalid internal relationship: {rid}")
            resolved = posixpath.normpath(path.lstrip("/") if path.startswith("/") else posixpath.join(folder, path))
            if resolved == ".." or resolved.startswith("../") or not self.has(resolved):
                raise FileEditError("INVALID_OFFICE_PACKAGE", f"Missing or invalid relationship target: {path}")
            result[rid] = resolved
        return result

    def serialize(self) -> bytes:
        if not self._changed:
            return self._original
        output = io.BytesIO()
        with ZipFile(output, "w") as archive:
            archive.comment = self._comment
            for name in self.names:
                info = copy(self._infos.get(name))
                if info is None:
                    info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = ZIP_DEFLATED
                archive.writestr(info, self.read(name))
        return output.getvalue()


def changed_parts(before: bytes, after: bytes) -> dict[str, list[str]]:
    """Report exact member-payload changes independently of ZIP recompression."""
    with ZipFile(io.BytesIO(before)) as old, ZipFile(io.BytesIO(after)) as new:
        old_names, new_names = set(old.namelist()), set(new.namelist())
        return {
            "modified": sorted(name for name in old_names & new_names if old.read(name) != new.read(name)),
            "added": sorted(new_names - old_names),
            "removed": sorted(old_names - new_names),
        }
