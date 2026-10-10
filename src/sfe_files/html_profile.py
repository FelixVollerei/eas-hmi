"""ID-addressed HTML edits that retain source outside the selected text or start tag."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from .plan_ops import FileEditError, _fields, _pointer_parts

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
TEXT_TAGS = {"title", "p", "h1", "h2", "h3", "h4", "h5", "h6", "span", "strong", "em", "b", "i", "u", "s", "small", "code", "pre", "div", "section", "article", "li", "td", "th", "a", "button", "label", "figcaption", "caption", "dt", "dd"}
PARENTS = {"body", "div", "section", "article", "main", "aside", "header", "footer", "nav", "li", "td", "th", "figure", "blockquote"}
NEW_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "div", "section", "span", "strong", "em", "code", "pre", "a"}
ATTRS = {"class", "title", "lang", "dir", "href", "src", "alt", "width", "height", "role"}
OPS = {"set-text", "set-attribute", "remove-attribute", "append-element"}
CONTRACT = {
    "addressing": "Unique HTML id, inspected through /elements/<escaped-id>. Escape / as ~1 and ~ as ~0.",
    "operations": {
        "set-text": {"example": {"op": "set-text", "id": "status", "text": "Ready for review"},
                     "description": "Replace a supported leaf element's text with escaped plain text."},
        "set-attribute": {"example": {"op": "set-attribute", "id": "status", "name": "class", "value": "ready"},
                          "description": "Set an ordinary attribute; the target start tag may normalize quoting."},
        "remove-attribute": {"example": {"op": "remove-attribute", "id": "status", "name": "title"},
                             "description": "Remove one existing supported attribute; id is immutable."},
        "append-element": {"example": {"op": "append-element", "parent_id": "actions", "tag": "p",
                                       "id": "follow-up", "text": "Verify alarms.", "attributes": {"class": "note"}},
                           "description": "Append a plain text element with a new id to a supported container."},
    },
    "limitations": [
        "UTF-8 HTML with explicit balanced non-void end tags; .html/.htm aliases. Optional-end-tag shorthand is unsupported.",
        "Only an HTML5 doctype is supported. The document must contain one explicit html root; fragments/XHTML use other profiles.",
        "Duplicate ids and attributes are rejected. Source outside changed text/start tags or insertion points stays byte-identical.",
        "set-text requires a supported leaf with no child elements or comments; existing attributes are retained.",
        "Scripts, styles, templates, SVG and MathML are preserved but cannot be edited through these operations.",
        "Attributes: class/title/lang/dir/href/src/alt/width/height/role, data-* and aria-*. No id rename.",
        "No script execution, resource fetching, CSS evaluation, browser DOM-state editing or layout guarantee.",
    ],
}


@dataclass
class Element:
    tag: str
    attrs: dict[str, str | None]
    start: int
    open_end: int
    close_start: int = -1
    end: int = -1
    parent: Element | None = None
    children: list[Element] = field(default_factory=list)
    text: list[str] = field(default_factory=list)
    markup: bool = False


class Document(HTMLParser):
    def __init__(self, text: str, bom: bool):
        super().__init__(convert_charrefs=True)
        self.source, self.bom = text, bom
        self.offsets = [0] + [match.end() for match in re.finditer("\n", text)]
        self.stack: list[Element] = []
        self.elements: list[Element] = []
        self.ids: dict[str, Element] = {}

    def source_offset(self):
        line, column = self.getpos()
        return self.offsets[line - 1] + column

    def handle_starttag(self, tag, attrs):
        if len({name for name, _ in attrs}) != len(attrs):
            raise FileEditError("INVALID_HTML", "Duplicate element attribute")
        start = self.source_offset()
        node = Element(tag, dict(attrs), start, start + len(self.get_starttag_text()),
                       parent=self.stack[-1] if self.stack else None)
        if tag in VOID:
            node.close_start = node.end = node.open_end
        if node.parent is not None:
            node.parent.children.append(node)
        self.elements.append(node)
        if "id" in node.attrs:
            ident = node.attrs["id"]
            if not ident or ident in self.ids or re.search(r"[\t\n\f\r ]", ident):
                raise FileEditError("INVALID_HTML", "Element ids must be nonempty, unique and contain no ASCII whitespace")
            self.ids[ident] = node
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        foreign = tag in {"svg", "math"} or any(node.tag in {"svg", "math"} for node in self.stack)
        if tag not in VOID and not foreign:
            raise FileEditError("UNSUPPORTED_HTML", "Self-closing non-void HTML tags are unsupported")
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            node = self.stack.pop()
            node.close_start = node.end = node.open_end

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1].tag != tag:
            raise FileEditError("UNSUPPORTED_HTML", "Use explicit, properly nested end tags for non-void elements")
        node = self.stack.pop()
        node.close_start = self.source_offset()
        node.end = self.source.find(">", node.close_start) + 1

    def handle_data(self, data):
        for node in self.stack:
            node.text.append(data)

    def handle_comment(self, data):
        if self.stack:
            self.stack[-1].markup = True

    def handle_decl(self, decl):
        if not re.fullmatch(r"DOCTYPE\s+html", decl, flags=re.IGNORECASE):
            raise FileEditError("UNSUPPORTED_HTML", "Only an HTML5 doctype is supported")

    def unknown_decl(self, data):
        raise FileEditError("UNSUPPORTED_HTML", "Unknown markup declarations are unsupported")

    def handle_pi(self, data):
        raise FileEditError("UNSUPPORTED_HTML", "Processing instructions and XHTML declarations are unsupported")


def parse(data: bytes) -> Document:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError as exc:
        raise FileEditError("INVALID_HTML", "HTML input must be UTF-8") from exc
    if "\x00" in text:
        raise FileEditError("INVALID_HTML", "NUL is unsupported in HTML source")
    doc = Document(text, data.startswith(b"\xef\xbb\xbf"))
    doc.feed(text)
    doc.close()
    if doc.stack:
        raise FileEditError("UNSUPPORTED_HTML", "Non-void elements require explicit closing tags")
    roots = [node for node in doc.elements if node.parent is None]
    if len(roots) != 1 or roots[0].tag != "html":
        raise FileEditError("UNSUPPORTED_HTML", "Expected a single explicit html root")
    for node in doc.elements:
        if node.tag == "meta":
            declared = node.attrs.get("charset")
            content = node.attrs.get("content", "") or ""
            if declared is None and (node.attrs.get("http-equiv", "") or "").lower() == "content-type":
                match = re.search(r"charset\s*=\s*([^\s;]+)", content, flags=re.IGNORECASE)
                declared = match[1].strip("'\"") if match else None
            if declared and declared.lower() not in {"utf-8", "utf8"}:
                raise FileEditError("UNSUPPORTED_HTML", "HTML charset declarations must agree with UTF-8")
    return doc


def _ordinary(node: Element) -> bool:
    while node is not None:
        if node.tag in {"script", "style", "template", "svg", "math"}:
            return False
        node = node.parent
    return True


def _view(node: Element):
    text = "".join(node.text)
    return {"tag": node.tag, "attributes": node.attrs, "text": text[:20000],
            "text_truncated": len(text) > 20000, "child_count": len(node.children),
            "editable_text": _ordinary(node) and node.tag in TEXT_TAGS and not node.children and not node.markup}


def summary(doc: Document):
    return {"element_count": len(doc.elements), "id_count": len(doc.ids),
            "elements": [{"id": key, "tag": node.tag} for key, node in list(doc.ids.items())[:200]],
            "truncated": len(doc.ids) > 200}


def _target(doc: Document, ident) -> Element:
    if not isinstance(ident, str) or ident not in doc.ids:
        raise FileEditError("TARGET_NOT_FOUND", "Expected one existing element id")
    return doc.ids[ident]


def inspect(doc: Document, pointer: str = ""):
    if not isinstance(pointer, str):
        raise FileEditError("INVALID_POINTER", "pointer must be a string")
    parts = _pointer_parts(pointer)
    if not parts:
        return {"value": summary(doc)}
    if len(parts) != 2 or parts[0] != "elements":
        raise FileEditError("INVALID_POINTER", "Use /elements/<id>")
    return {"pointer": pointer, "value": _view(_target(doc, parts[1]))}


def _text(value):
    if not isinstance(value, str) or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]", value):
        raise FileEditError("INVALID_STEP", "Expected a Unicode string without control characters")
    return value


def _attribute(name):
    if not isinstance(name, str) or not (name in ATTRS or re.fullmatch(r"(?:data|aria)-[a-z0-9-]+", name)):
        raise FileEditError("UNSUPPORTED_ATTRIBUTE", "Use a supported ordinary attribute; id is immutable")


def _tag(tag: str, attrs: dict) -> str:
    return "<" + tag + "".join(" " + name + ("" if value is None else '="' + html.escape(value, quote=True) + '"')
                                 for name, value in attrs.items()) + ">"


def edit(doc: Document, step: dict) -> Document:
    if not isinstance(step, dict) or not isinstance(step.get("op"), str) or step["op"] not in OPS:
        raise FileEditError("INVALID_STEP", "Unsupported HTML operation")
    op = step["op"]
    required = {
        "set-text": {"op", "id", "text"},
        "set-attribute": {"op", "id", "name", "value"},
        "remove-attribute": {"op", "id", "name"},
        "append-element": {"op", "parent_id", "tag", "id", "text"},
    }[op]
    _fields(step, required | ({"attributes"} if op == "append-element" else set()), required)
    node = _target(doc, step["parent_id"] if op == "append-element" else step["id"])
    if not _ordinary(node):
        raise FileEditError("UNSUPPORTED_TARGET", "This element is outside the ordinary HTML editing surface")
    if op == "set-text":
        if not _view(node)["editable_text"]:
            raise FileEditError("COMPLEX_TARGET", "set-text requires a supported leaf without comments or children")
        start, end, replacement = node.open_end, node.close_start, html.escape(_text(step["text"]), quote=False)
    elif op in {"set-attribute", "remove-attribute"}:
        name = step["name"]
        _attribute(name)
        attrs = dict(node.attrs)
        if op == "remove-attribute":
            if name not in attrs:
                raise FileEditError("TARGET_NOT_FOUND", "Attribute does not exist")
            del attrs[name]
        else:
            attrs[name] = _text(step["value"])
        start, end, replacement = node.start, node.open_end, _tag(node.tag, attrs)
    else:
        tag, ident = step["tag"], _text(step["id"])
        if not isinstance(tag, str) or tag not in NEW_TAGS or node.tag not in PARENTS:
            raise FileEditError("UNSUPPORTED_TARGET", "Use a supported child tag and block container")
        if not ident or re.search(r"[\t\n\f\r ]", ident) or ident in doc.ids:
            raise FileEditError("INVALID_STEP", "New id must be nonempty, unique and contain no ASCII whitespace")
        extra = step.get("attributes", {})
        if not isinstance(extra, dict):
            raise FileEditError("INVALID_STEP", "attributes must be an object")
        attrs = {"id": ident}
        for name, value in extra.items():
            _attribute(name)
            attrs[name] = _text(value)
        replacement = _tag(tag, attrs) + html.escape(_text(step["text"]), quote=False) + "</" + tag + ">"
        start = end = node.close_start
    updated = doc.source[:start] + replacement + doc.source[end:]
    return parse((b"\xef\xbb\xbf" if doc.bom else b"") + updated.encode("utf-8"))


def serialize(doc: Document):
    return (b"\xef\xbb\xbf" if doc.bom else b"") + doc.source.encode("utf-8")
