"""Read and edit metadata and identified leaf text in reflowable EPUB 3 books."""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import unquote, urlsplit

from lxml import etree

from .plan_ops import FileEditError, _fields, _pointer_parts
from .zip_document import ZipDocument

OCF = "urn:oasis:names:tc:opendocument:xmlns:container"
OPF = "http://www.idpf.org/2007/opf"
DC = "http://purl.org/dc/elements/1.1/"
XHTML = "http://www.w3.org/1999/xhtml"
NS = {"ocf": OCF, "opf": OPF, "dc": DC, "h": XHTML}
PACKAGE = True
OPS = {"set-metadata", "set-text", "set-modified"}
METADATA_NAMES = {"title", "creator", "language", "description"}
TEXT_TAGS = {
    "p",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "span",
    "em",
    "strong",
    "b",
    "i",
    "u",
    "small",
    "sub",
    "sup",
    "li",
    "dt",
    "dd",
    "blockquote",
    "pre",
    "code",
    "title",
    "a",
    "figcaption",
    "caption",
    "td",
    "th",
    "cite",
    "q",
    "abbr",
    "s",
    "mark",
    "time",
}
CONTRACT = {
    "addressing": (
        "Inspect /metadata, /chapters/<manifest-id> or /chapters/<manifest-id>/elements/<element-id>. "
        "Chapter order follows the OPF spine. Pointer components use ~0 and ~1 escapes."
    ),
    "operations": {
        "set-metadata": {
            "example": {"op": "set-metadata", "name": "title", "value": "Commissioning handbook"},
            "description": "Replace one existing Dublin Core title, creator, language or description element.",
        },
        "set-text": {
            "example": {"op": "set-text", "chapter": "review", "id": "status", "text": "Ready for review."},
            "description": "Replace the text of a unique identified XHTML leaf text element in a spine chapter.",
        },
        "set-modified": {
            "example": {"op": "set-modified", "value": "2026-09-26T00:00:00Z"},
            "description": "Explicitly set the existing publication dcterms:modified UTC timestamp.",
        },
    },
    "limitations": [
        "Supports one EPUB 3 package with reflowable XHTML spine content; EPUB 2 and fixed layouts are rejected.",
        "Encrypted, font-obfuscated and digitally signed books are unsupported.",
        "No chapter insertion/deletion or identifier, manifest, spine, navigation, stylesheet, image or link changes.",
        "Metadata edits require exactly one existing plain-text element; repeated authors/titles are ambiguous.",
        "set-text rejects child elements, navigation documents, scripts, styles and non-text element types.",
        "Existing attributes and tails are retained; text is escaped as XML, never interpreted as XHTML markup.",
        "Untouched ZIP member payloads and metadata are preserved. Changed XML may normalize XML formatting.",
        "Plain HTML5 XHTML doctype is preserved; entities and other DTD declarations are unsupported.",
        "No network resources are fetched; manifest URLs with query/fragment or encoded path separators are unsupported.",
        "Include set-modified with the intended UTC timestamp when publishing edits; other operations retain its value.",
        "No pagination or reading-system rendering check. Full EPUB conformance requires a validator such as EPUBCheck.",
    ],
    "references": ["https://www.w3.org/TR/epub-33/"],
}


@dataclass
class Item:
    id: str
    href: str
    media_type: str
    properties: list[str]
    part: str | None


@dataclass
class Document:
    package: ZipDocument
    opf_part: str
    manifest: dict[str, Item]
    spine: list[str]


def _error(message, code="INVALID_EPUB"):
    raise FileEditError(code, message)


def _resolve(package, source, href, *, external=False):
    if not isinstance(href, str) or not href or re.search(r"%(?![0-9A-Fa-f]{2})", href):
        _error("Resource href must be a valid nonempty URI")
    url = urlsplit(href)
    if url.scheme or url.netloc:
        if external and url.scheme in {"http", "https"}:
            return None
        _error("Package/chapter references must be internal")
    if (
        url.query
        or url.fragment
        or url.path.startswith("/")
        or re.search(r"%(?:2f|5c)", url.path, re.IGNORECASE)
    ):
        _error("Only relative resource paths without query, fragment or encoded separators are supported")
    try:
        path = unquote(url.path, encoding="utf-8", errors="strict")
    except UnicodeError as exc:
        raise FileEditError("INVALID_EPUB", "Resource href contains invalid UTF-8") from exc
    if not path or "\\" in path:
        _error("Invalid resource path")
    part = posixpath.normpath(posixpath.join(posixpath.dirname(source), path))
    if part == ".." or part.startswith("../") or not package.has(part):
        _error(f"Resource does not exist inside the EPUB: {href}")
    return part


def _metadata_root(document):
    return document.package.xml(document.opf_part).find("opf:metadata", NS)


def _modified_nodes(document):
    return _metadata_root(document).xpath(
        "opf:meta[@property='dcterms:modified' and not(@refines)]", namespaces=NS
    )


def _ids(root):
    result = {}
    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        identifier = node.get("id")
        if identifier is None:
            continue
        if not identifier or identifier in result:
            _error("Chapter element IDs must be nonempty and unique")
        result[identifier] = node
    return result


def parse(data: bytes) -> Document:
    package = ZipDocument.open(data, "application/epub+zip")
    if package.has("META-INF/encryption.xml") or package.has("META-INF/signatures.xml"):
        _error("Encrypted, font-obfuscated and signed EPUBs are unsupported", "UNSUPPORTED_EPUB")
    container = package.xml("META-INF/container.xml")
    if container.tag != f"{{{OCF}}}container":
        _error("Invalid EPUB container document")
    roots = container.findall("ocf:rootfiles/ocf:rootfile", NS)
    if len(roots) != 1 or roots[0].get("media-type") != "application/oebps-package+xml":
        _error("Expected one EPUB package rootfile", "UNSUPPORTED_EPUB")
    opf_part = _resolve(package, "", roots[0].get("full-path"))
    root = package.xml(opf_part)
    if root.tag != f"{{{OPF}}}package" or root.get("version") != "3.0":
        _error("Only EPUB 3 package documents are supported", "UNSUPPORTED_EPUB")
    for name in ("metadata", "manifest", "spine"):
        if len(root.findall(f"opf:{name}", NS)) != 1:
            _error(f"Expected exactly one {name} element")
    if root.xpath(".//@xml:base", namespaces={"xml": "http://www.w3.org/XML/1998/namespace"}):
        _error("xml:base overrides in package documents are unsupported", "UNSUPPORTED_EPUB")
    if root.xpath(
        "opf:metadata/opf:meta[@property='rendition:layout' and normalize-space(.)!='reflowable']",
        namespaces=NS,
    ):
        _error("Fixed-layout EPUB is unsupported", "UNSUPPORTED_EPUB")
    manifest = {}
    for node in root.findall("opf:manifest/opf:item", NS):
        identifier, href, media_type = node.get("id"), node.get("href"), node.get("media-type")
        if not identifier or identifier in manifest or not media_type:
            _error("Manifest items require unique IDs, href and media type")
        manifest[identifier] = Item(
            identifier,
            href,
            media_type,
            node.get("properties", "").split(),
            _resolve(package, opf_part, href, external=True),
        )
    if len([item for item in manifest.values() if "nav" in item.properties]) != 1:
        _error("EPUB 3 requires exactly one navigation resource")
    spine = []
    for node in root.findall("opf:spine/opf:itemref", NS):
        identifier = node.get("idref")
        if identifier not in manifest or identifier in spine:
            _error("Spine references must select distinct manifest items")
        item = manifest[identifier]
        if item.media_type != "application/xhtml+xml" or item.part is None:
            _error("Only internal XHTML spine chapters are supported", "UNSUPPORTED_EPUB")
        if "rendition:layout-pre-paginated" in node.get("properties", "").split():
            _error("Fixed-layout spine item is unsupported", "UNSUPPORTED_EPUB")
        chapter = package.xml(item.part)
        if chapter.tag != f"{{{XHTML}}}html" or chapter.find("h:body", NS) is None:
            _error("Spine chapter must be XHTML with a body")
        _ids(chapter)
        spine.append(identifier)
    if not spine:
        _error("EPUB must have at least one spine chapter")
    document = Document(package, opf_part, manifest, spine)
    if len(_modified_nodes(document)) != 1:
        _error("Expected exactly one publication dcterms:modified value")
    return document


def _metadata(document):
    result = {}
    for node in _metadata_root(document):
        if isinstance(node.tag, str) and etree.QName(node).namespace == DC:
            result.setdefault(etree.QName(node).localname, []).append("".join(node.itertext()))
    result["modified"] = ["".join(node.itertext()) for node in _modified_nodes(document)]
    return result


def _chapter(document, identifier):
    if not isinstance(identifier, str) or identifier not in document.spine:
        _error(f"Spine chapter not found: {identifier}", "TARGET_NOT_FOUND")
    item = document.manifest[identifier]
    return item, document.package.xml(item.part)


def _editable(node):
    return node.tag in {f"{{{XHTML}}}{name}" for name in TEXT_TAGS} and len(node) == 0


def _element_view(node, *, navigation=False):
    return {
        "id": node.get("id"),
        "tag": etree.QName(node).localname,
        "attributes": dict(node.attrib),
        "text": "".join(node.itertext()),
        "children": [
            {"id": child.get("id"), "tag": etree.QName(child).localname}
            for child in node
            if isinstance(child.tag, str)
        ],
        "editable_text": not navigation and _editable(node),
    }


def _chapter_view(document, identifier):
    item, root = _chapter(document, identifier)
    return {
        "id": item.id,
        "href": item.href,
        "part": item.part,
        "title": "".join(root.find("h:head/h:title", NS).itertext())
        if root.find("h:head/h:title", NS) is not None
        else "",
        "text": "".join(root.find("h:body", NS).itertext()),
        "elements": [_element_view(node, navigation="nav" in item.properties) for node in _ids(root).values()],
    }


def projection(document):
    return {
        "metadata": _metadata(document),
        "spine": document.spine,
        "chapters": [_chapter_view(document, identifier) for identifier in document.spine],
    }


def summary(document):
    chapters = []
    for identifier in document.spine[:100]:
        view = _chapter_view(document, identifier)
        chapters.append(
            {"id": identifier, "title": view["title"][:200], "element_count": len(view["elements"])}
        )
    return {
        "metadata": {
            key: [value[:200] for value in values[:20]] for key, values in _metadata(document).items()
        },
        "chapter_count": len(document.spine),
        "chapters": chapters,
        "truncated": len(document.spine) > 100,
    }


def inspect(document, pointer=""):
    if not isinstance(pointer, str):
        _error("pointer must be a string", "INVALID_POINTER")
    parts = _pointer_parts(pointer)
    if not parts:
        return {"summary": summary(document)}
    if parts == ["metadata"]:
        return {"pointer": pointer, "value": _metadata(document)}
    if len(parts) in {2, 4} and parts[0] == "chapters":
        item, root = _chapter(document, parts[1])
        if len(parts) == 2:
            return {"pointer": pointer, "value": _chapter_view(document, item.id)}
        if parts[2] == "elements":
            node = _ids(root).get(parts[3])
            if node is None:
                _error("Element ID not found in selected chapter", "TARGET_NOT_FOUND")
            return {"pointer": pointer, "value": _element_view(node, navigation="nav" in item.properties)}
    _error("Use /metadata, /chapters/<id> or /chapters/<id>/elements/<id>", "INVALID_POINTER")


def _text(value):
    if not isinstance(value, str):
        _error("value/text must be a string", "INVALID_STEP")
    try:
        etree.Element("text").text = value
    except (ValueError, UnicodeError) as exc:
        raise FileEditError("INVALID_STEP", "Text contains characters that are invalid in XML") from exc
    return value


def edit(document, step):
    if not isinstance(step, dict) or not isinstance(step.get("op"), str) or step["op"] not in OPS:
        _error("Unsupported EPUB operation", "INVALID_STEP")
    op = step["op"]
    fields = {
        "set-metadata": {"op", "name", "value"},
        "set-text": {"op", "chapter", "id", "text"},
        "set-modified": {"op", "value"},
    }[op]
    _fields(step, fields, fields)
    if op == "set-text":
        item, root = _chapter(document, step["chapter"])
        if not isinstance(step["id"], str) or not step["id"]:
            _error("Element id must be a nonempty string", "INVALID_STEP")
        node = _ids(root).get(step["id"])
        if node is None:
            _error("Element ID not found in selected chapter", "TARGET_NOT_FOUND")
        if "nav" in item.properties or not _editable(node):
            _error("set-text requires a non-navigation XHTML leaf text element", "UNSUPPORTED_TARGET")
        value, part = _text(step["text"]), item.part
    elif op == "set-metadata":
        name = step["name"]
        if not isinstance(name, str) or name not in METADATA_NAMES:
            _error("Editable metadata: title, creator, language, description", "INVALID_STEP")
        matches = _metadata_root(document).findall(f"dc:{name}", NS)
        if len(matches) != 1 or len(matches[0]):
            _error("Metadata target must be one existing plain-text element", "TARGET_NOT_UNIQUE")
        node, value, part = matches[0], _text(step["value"]), document.opf_part
        if name in {"title", "creator", "language"} and not value.strip():
            _error("Title, creator and language cannot be empty", "INVALID_STEP")
    else:
        value = _text(step["value"])
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value):
            _error("modified requires YYYY-MM-DDTHH:MM:SSZ in UTC", "INVALID_STEP")
        try:
            datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")
        except ValueError as exc:
            raise FileEditError("INVALID_STEP", "modified is not a valid UTC calendar timestamp") from exc
        node, part = _modified_nodes(document)[0], document.opf_part
    node.text = value
    document.package.mark_changed(part)
    return document


def serialize(document):
    return document.package.serialize()
