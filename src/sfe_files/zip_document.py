"""Preserve unrelated payloads while editing ODF and EPUB ZIP document parts."""

from __future__ import annotations

import io
from copy import copy
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile, ZipInfo

from lxml import etree

from .plan_ops import FileEditError

OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
MANIFEST = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
ODF_MIMES = {
    "odt": "application/vnd.oasis.opendocument.text",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "odp": "application/vnd.oasis.opendocument.presentation",
}


class ZipDocument:
    def __init__(self):
        self._parts: dict[str, bytes] = {}
        self._infos: dict[str, ZipInfo] = {}
        self._trees: dict[str, etree._Element] = {}
        self._changed: set[str] = set()
        self._original = b""
        self._comment = b""

    @classmethod
    def open(cls, data: bytes, mimetype: str) -> ZipDocument:
        doc = cls()
        doc._original = data
        try:
            with ZipFile(io.BytesIO(data)) as archive:
                infos = archive.infolist()
                if (not infos or infos[0].filename != "mimetype"
                        or infos[0].compress_type != ZIP_STORED or infos[0].extra):
                    raise ValueError("mimetype must be the first, uncompressed member without extra fields")
                doc._comment = archive.comment
                for info in infos:
                    name = info.filename
                    if name in doc._parts:
                        raise ValueError(f"Duplicate ZIP member: {name}")
                    if name.startswith("/") or "\\" in name or ".." in name.split("/"):
                        raise ValueError(f"Invalid ZIP member name: {name}")
                    if info.flag_bits & 1:
                        raise ValueError("Encrypted ZIP members are unsupported")
                    doc._parts[name] = archive.read(info)
                    doc._infos[name] = copy(info)
        except (BadZipFile, ValueError, RuntimeError, NotImplementedError) as exc:
            raise FileEditError("INVALID_DOCUMENT_PACKAGE", str(exc)) from exc
        if doc.read("mimetype") != mimetype.encode("ascii"):
            raise FileEditError("INVALID_DOCUMENT_PACKAGE", "Package mimetype does not match the profile")
        return doc

    @property
    def names(self):
        return list(self._parts)

    def has(self, name: str) -> bool:
        return name in self._parts

    def read(self, name: str) -> bytes:
        if name not in self._parts:
            raise FileEditError("INVALID_DOCUMENT_PACKAGE", f"Missing package part: {name}")
        if name in self._changed and name in self._trees:
            return etree.tostring(self._trees[name].getroottree(), xml_declaration=True, encoding="UTF-8")
        return self._parts[name]

    def xml(self, name: str):
        if name in self._trees:
            return self._trees[name]
        try:
            parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True)
            tree = etree.parse(io.BytesIO(self.read(name)), parser)
            info = tree.docinfo
            # EPUB XHTML may use the plain HTML5 doctype. External DTDs and entity declarations are excluded.
            if info.doctype:
                dtd = info.internalDTD
                if (info.root_name != "html" or info.system_url or info.public_id
                        or dtd is None or list(dtd.iterentities()) or list(dtd.iterelements())):
                    raise ValueError("Only a plain HTML5 doctype is supported; external DTDs and entities are not")
            if any(isinstance(node, etree._Entity) for node in tree.iter()):
                raise ValueError("Entity references are unsupported")
        except (etree.XMLSyntaxError, ValueError) as exc:
            raise FileEditError("INVALID_DOCUMENT_XML", f"{name}: {exc}") from exc
        self._trees[name] = tree.getroot()
        return self._trees[name]

    def mark_changed(self, name: str):
        if not self.has(name):
            raise FileEditError("INVALID_DOCUMENT_PACKAGE", f"Missing package part: {name}")
        self._changed.add(name)

    def write(self, name: str, data: bytes):
        if name == "mimetype":
            raise FileEditError("INVALID_DOCUMENT_PACKAGE", "mimetype is immutable")
        self._parts[name] = data
        self._trees.pop(name, None)
        self._changed.add(name)

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


def open_odf(data: bytes, kind: str) -> ZipDocument:
    doc = ZipDocument.open(data, ODF_MIMES[kind])
    if any(name.lower().startswith("meta-inf/") and "signature" in name.lower() for name in doc.names):
        raise FileEditError("UNSUPPORTED_ODF", "Signed ODF packages are unsupported")
    manifest = doc.xml("META-INF/manifest.xml")
    if manifest.tag != f"{{{MANIFEST}}}manifest":
        raise FileEditError("INVALID_ODF", "Missing ODF manifest root")
    if manifest.find(f".//{{{MANIFEST}}}encryption-data") is not None:
        raise FileEditError("UNSUPPORTED_ODF", "Encrypted ODF packages are unsupported")
    entries = {}
    for entry in manifest.findall(f"{{{MANIFEST}}}file-entry"):
        name = entry.get(f"{{{MANIFEST}}}full-path")
        if not name or name in entries:
            raise FileEditError("INVALID_ODF", "Missing or duplicate manifest path")
        entries[name] = entry.get(f"{{{MANIFEST}}}media-type")
    if entries.get("/") != ODF_MIMES[kind] or entries.get("content.xml") != "text/xml":
        raise FileEditError("INVALID_ODF", "Manifest does not declare the document and content.xml correctly")
    root = doc.xml("content.xml")
    body_kind = {"odt": "text", "ods": "spreadsheet", "odp": "presentation"}[kind]
    if (root.tag != f"{{{OFFICE}}}document-content"
            or root.get(f"{{{OFFICE}}}version") not in {"1.2", "1.3"}
            or root.find(f"{{{OFFICE}}}body/{{{OFFICE}}}{body_kind}") is None):
        raise FileEditError("UNSUPPORTED_ODF", "Expected packaged ODF 1.2/1.3 content for this profile")
    return doc
