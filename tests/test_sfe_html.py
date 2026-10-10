import json
from pathlib import Path

import pytest
from lxml import html

from sfe_files import html_profile as profile
from sfe_files.core import FileEditError, prepare, sha256


def source(body):
    return ('<!doctype html><html><head><meta charset="utf-8"><title>Test</title></head><body>' + body + '</body></html>').encode()


def test_text_edits_escape_html_and_preserve_unselected_source():
    data = source('<p id="text">Old &amp; value</p>\r\n<!--note--><script>const a = "<x>";</script>')
    doc = profile.parse(data)
    assert profile.inspect(doc, "/elements/text")["value"]["text"] == "Old & value"
    changed = profile.edit(doc, {"op": "set-text", "id": "text", "text": "新文字 <b> & text"})
    output = profile.serialize(changed)
    assert output == data.replace(b"Old &amp; value", "新文字 &lt;b&gt; &amp; text".encode())
    assert html.fromstring(output).get_element_by_id("text").text == "新文字 <b> & text"
    assert profile.serialize(doc) == data


def test_attributes_insertion_and_later_id_lookup():
    doc = profile.parse(source('<div id="list"><p id="status" CLASS=old title="Remove me">Pending</p></div>'))
    doc = profile.edit(doc, {"op": "set-attribute", "id": "status", "name": "class", "value": "ready"})
    doc = profile.edit(doc, {"op": "remove-attribute", "id": "status", "name": "title"})
    doc = profile.edit(doc, {"op": "append-element", "parent_id": "list", "tag": "p", "id": "new/item~1",
                             "text": "Added", "attributes": {"data-label": '"quoted"'}})
    doc = profile.edit(doc, {"op": "set-text", "id": "new/item~1", "text": "Review complete"})
    node = profile.inspect(doc, "/elements/new~1item~01")["value"]
    assert node["text"] == "Review complete" and node["attributes"]["data-label"] == '"quoted"'
    result = html.fromstring(profile.serialize(doc))
    assert result.get_element_by_id("status").get("class") == "ready"
    assert result.get_element_by_id("status").get("title") is None


@pytest.mark.parametrize("body", [
    '<p id="x">A<span>B</span></p>', '<p id="x">A<!--comment-->B</p>',
    '<script id="x">alert(1)</script>', '<style id="x">p { color:red; }</style>',
    '<svg><text id="x">Label</text><path d="M0 0"/></svg>',
    '<template><p id="x">Deferred</p></template>',
])
def test_complex_text_and_inert_or_foreign_targets_are_preserved(body):
    data = source(body)
    doc = profile.parse(data)
    with pytest.raises(FileEditError):
        profile.edit(doc, {"op": "set-text", "id": "x", "text": "Lost content"})
    assert profile.serialize(doc) == data


@pytest.mark.parametrize("body", [
    '<p id="x">A</p><p id="x">B</p>', '<p id=x ID=y>A</p>',
    '<p id=x>A<p>B', '<div/>', '<p id=" ">Invalid ID</p>',
])
def test_ambiguous_or_unsupported_markup_rejected(body):
    with pytest.raises(FileEditError):
        profile.parse(source(body))


@pytest.mark.parametrize("step", [
    {"op": "set-attribute", "id": "x", "name": "id", "value": "new"},
    {"op": "set-attribute", "id": "x", "name": "onclick", "value": "x()"},
    {"op": "set-text", "id": "x", "text": "\ud800"},
    {"op": "set-text", "id": "missing", "text": "x"},
    {"op": "append-element", "parent_id": "x", "tag": "p", "id": "x", "text": "duplicate"},
    {"op": "append-element", "parent_id": "x", "tag": "script", "id": "new", "text": "x()"},
])
def test_invalid_operations_leave_source_unchanged(step):
    data = source('<div id="x">Existing</div>')
    doc = profile.parse(data)
    with pytest.raises(FileEditError):
        profile.edit(doc, step)
    assert profile.serialize(doc) == data


def test_bom_newlines_and_declared_encoding():
    data = b"\xef\xbb\xbf" + source('<p id="x">One\r\nTwo</p>')
    assert profile.serialize(profile.parse(data)) == data
    with pytest.raises(FileEditError):
        profile.parse(data.replace(b'charset="utf-8"', b'charset="windows-1252"'))


def test_invalid_batch_returns_no_prepared_output():
    data = source('<p id="x">Existing</p>')
    plan = {"protocol": "sfe/0.1", "format": "html", "base": {"sha256": sha256(data)},
            "steps": [{"op": "set-text", "id": "x", "text": "First change"},
                      {"op": "set-text", "id": "missing", "text": "Fail"}]}
    with pytest.raises(FileEditError):
        prepare(Path("sample.html"), data, json.dumps(plan).encode())
