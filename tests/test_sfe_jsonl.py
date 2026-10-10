import json
from pathlib import Path

import pytest

from sfe_files import jsonl_profile as profile
from sfe_files.core import FileEditError, prepare, sha256


def plan(data, steps):
    return json.dumps({"protocol": "sfe/0.1", "format": "jsonl",
                       "base": {"sha256": sha256(data)}, "steps": steps}).encode()


def test_keyed_edit_preserves_other_record_bytes_and_nested_unicode():
    first = b'{ "id": "A", "nested": {"label":"old"} }\r\n'
    untouched = '{ "id" : "B", "label":"不变\\n第二行" }\n'.encode()
    source = first + untouched
    steps = [{"op": "set-field", "key_column": "id", "key": "A", "pointer": "/nested/label", "value": "已审阅"}]
    result = prepare(Path("a.jsonl"), source, plan(source, steps))
    assert result.output.endswith(untouched)
    assert b"\r\n" in result.output
    value = profile.parse(result.output)
    assert profile.inspect(value, "/by-key/id/A/nested/label")["value"] == "已审阅"
    assert profile.inspect(value, "/records/2/label")["value"] == "不变\n第二行"


def test_append_replace_delete_and_no_terminal_newline():
    source = b'{ "id" : "A", "value": 1 }'
    steps = [
        {"op": "append-record", "key_column": "id", "record": {"id": "B", "value": 2}},
        {"op": "replace-record", "key_column": "id", "key": "A", "record": {"id": "C", "value": 3}},
        {"op": "delete-record", "key_column": "id", "key": "B"},
    ]
    output = prepare(Path("a.jsonl"), source, plan(source, steps)).output
    assert output == b'{"id":"C","value":3}\n'
    fresh = profile.edit(profile.parse(b""), {"op": "append-record", "key_column": "id", "record": {"id": "A"}})
    assert profile.serialize(fresh) == b'{"id":"A"}\n'


def test_unchanged_round_trip_and_all_json_value_inspection():
    source = 'null\ntrue\n[1,2]\n"hello\u2028world"\n {"a":1} '.encode()
    doc = profile.parse(source)
    assert profile.serialize(doc) == source
    assert profile.inspect(doc, "/records/3/1")["value"] == 2
    assert profile.inspect(doc, "/records/4")["value"] == "hello\u2028world"


@pytest.mark.parametrize("source", [
    b'\xef\xbb\xbf{}\n', b'{}\n\n', b'\n', b'{"id":1,"id":2}\n',
    b'{"x":NaN}\n', b'{"x":Infinity}\n', b'{}\r{}', b'"\\ud800"\n', b'\xff', b'{}\n\xef\xbb\xbf{}\n',
])
def test_invalid_records_are_rejected(source):
    with pytest.raises(FileEditError) as exc:
        profile.parse(source)
    assert exc.value.code == "INVALID_JSONL"


@pytest.mark.parametrize("source", [b'{"id":"A"}\n{"id":"A"}\n', b'{"id":1}\n', b'null\n', b'{}\n'])
def test_edit_requires_unambiguous_string_keys(source):
    with pytest.raises(FileEditError):
        profile.edit(profile.parse(source), {"op": "delete-record", "key_column": "id", "key": "A"})


@pytest.mark.parametrize("step", [
    {"op": "set-field", "key_column": "id", "key": "A", "pointer": "/id", "value": "B"},
    {"op": "append-record", "key_column": "id", "record": {"id": "B"}},
    {"op": "replace-record", "key_column": "id", "key": "A", "record": {"id": "B"}},
    {"op": "replace-record", "key_column": "id", "key": "A", "record": 4},
    {"op": "set-field", "key_column": "id", "key": "A", "pointer": "/missing", "value": 1},
    {"op": "delete-record", "key_column": "id", "key": "absent"},
    {"op": "delete-record", "key_column": "id", "key": "A", "typo": 1},
])
def test_invalid_edit_does_not_mutate_document(step):
    source = b'{"id":"A"}\n{"id":"B"}\n'
    doc = profile.parse(source)
    with pytest.raises(FileEditError):
        profile.edit(doc, step)
    assert profile.serialize(doc) == source


def test_pointer_escaping_keys_and_nested_fields():
    doc = profile.parse(b'{"business/key":"A/B~C", "a/b":{"~x":42}}\n')
    assert profile.inspect(doc, "/by-key/business~1key/A~1B~0C/a~1b/~0x")["value"] == 42


@pytest.mark.parametrize("pointer", [
    "/records/0", "/records/01", "/records/3", "/records/-1", "/by-key/id/C", "/records/٠", "/records/²",
])
def test_bad_read_address(pointer):
    with pytest.raises(FileEditError):
        profile.inspect(profile.parse(b'{"id":"A"}\n'), pointer)
