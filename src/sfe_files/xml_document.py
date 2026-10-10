"""Small secure XML holder for format profiles that edit selected nodes."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from lxml import etree

from .plan_ops import FileEditError


@dataclass
class XmlDocument:
    raw: bytes
    tree: etree._ElementTree
    dirty: bool = False

    @property
    def root(self):
        return self.tree.getroot()

    def serialize(self) -> bytes:
        if not self.dirty:
            return self.raw
        return etree.tostring(self.tree, encoding="UTF-8", xml_declaration=True)


def read_xml(data: bytes, *, code: str) -> XmlDocument:
    try:
        parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)
        tree = etree.parse(BytesIO(data), parser)
    except (etree.XMLSyntaxError, ValueError) as exc:
        raise FileEditError(code, f"Invalid XML: {exc}") from exc
    if tree.docinfo.doctype or tree.docinfo.internalDTD or tree.docinfo.externalDTD:
        raise FileEditError(code, "DTD and entity declarations are unsupported")
    return XmlDocument(data, tree)
