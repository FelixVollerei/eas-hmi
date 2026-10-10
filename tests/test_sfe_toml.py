"""TOML adapter contracts checked with Python's independent TOML parser."""

import json
import math
import tomllib
from datetime import date, datetime, time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sfe_files import toml_profile as toml
from sfe_files.cli import app
from sfe_files.core import FileEditError, sha256

SOURCE = b'''# Site settings
commissioned = 2026-09-26 # Keep the date.
timestamp = 2026-09-26T07:30:00Z
local_time = 07:30:00

[server] # Service config
port = 8_080 # HTTP port
peers = ["primary", "backup"]
inline = { name = "edge", enabled = true }

[[sensors]]
id = "temp"
enabled = false
'''


def apply(source, *steps):
    document = toml.parse(source)
    for step in steps:
        document = toml.edit(document, step)
    return toml.serialize(document)


def test_noop_is_byte_identical():
    assert toml.serialize(toml.parse(SOURCE)) == SOURCE


def test_set_preserves_comments_and_untouched_dates():
    result = apply(SOURCE, {"op": "set", "pointer": "/server/port", "value": 8081})
    assert b"# Site settings" in result
    assert b"[server] # Service config" in result
    assert b"port = 8081 # HTTP port" in result
    assert b"commissioned = 2026-09-26 # Keep the date." in result
    before, after = (tomllib.loads(data.decode()) for data in (SOURCE, result))
    before["server"]["port"] = 8081
    assert before == after
    assert isinstance(after["commissioned"], date)
    assert isinstance(after["timestamp"], datetime)
    assert isinstance(after["local_time"], time)


def test_inspection_is_json_safe_and_dates_are_explicit():
    document = toml.parse(SOURCE + b'\n[measurements]\nmissing = nan\nlimit = +inf\n')
    value = toml.inspect(document)["value"]
    json.dumps(value, allow_nan=False)
    assert value["commissioned"] == {"$type": "date", "value": "2026-09-26"}
    assert value["timestamp"] == {"$type": "datetime", "value": "2026-09-26T07:30:00+00:00"}
    assert value["local_time"] == {"$type": "time", "value": "07:30:00"}
    assert value["measurements"]["missing"] == {"$type": "float", "value": "nan"}
    assert toml.inspect(document, "/server/inline/enabled")["value"] is True


def test_array_insert_append_set_remove():
    result = apply(
        SOURCE,
        {"op": "add", "pointer": "/server/peers/1", "value": "middle"},
        {"op": "append", "pointer": "/server/peers", "value": "last"},
        {"op": "set", "pointer": "/server/peers/0", "value": "first"},
        {"op": "remove", "pointer": "/server/peers/2"},
        {"op": "add", "pointer": "/server/peers/-", "value": "end"},
    )
    assert tomllib.loads(result.decode())["server"]["peers"] == ["first", "middle", "last", "end"]


def test_array_of_tables_and_inline_tables():
    result = apply(
        SOURCE,
        {"op": "append", "pointer": "/sensors", "value": {"id": "pressure", "enabled": True}},
        {"op": "set", "pointer": "/sensors/0/enabled", "value": True},
        {"op": "add", "pointer": "/server/inline/region", "value": "east"},
        {"op": "remove", "pointer": "/server/inline/name"},
        {"op": "add", "pointer": "/logging", "value": {"level": "debug"}},
    )
    parsed = tomllib.loads(result.decode())
    assert parsed["sensors"] == [{"id": "temp", "enabled": True}, {"id": "pressure", "enabled": True}]
    assert parsed["server"]["inline"] == {"enabled": True, "region": "east"}
    assert parsed["logging"] == {"level": "debug"}


def test_pointer_escapes_and_empty_key():
    source = b'"a/b" = { "~key" = 1 }\n"" = "blank"\n'
    result = apply(source, {"op": "set", "pointer": "/a~1b/~0key", "value": 2})
    document = toml.parse(result)
    assert toml.inspect(document, "/a~1b/~0key")["value"] == 2
    assert toml.inspect(document, "/")["value"] == "blank"


@pytest.mark.parametrize("value", [None, {"nested": None}, [1, None], math.inf, math.nan, 2**63, {1: "bad"}])
def test_values_without_supported_toml_representation_are_rejected(value):
    document = toml.parse(SOURCE)
    with pytest.raises(FileEditError) as exc:
        toml.edit(document, {"op": "set", "pointer": "/server/port", "value": value})
    assert exc.value.code == "INVALID_VALUE"
    assert toml.serialize(document) == SOURCE


@pytest.mark.parametrize(
    "step,code",
    [
        ({"op": "set", "pointer": "/missing", "value": 1}, "TARGET_NOT_FOUND"),
        ({"op": "add", "pointer": "/missing/key", "value": 1}, "TARGET_NOT_FOUND"),
        ({"op": "add", "pointer": "/server/peers/9", "value": "x"}, "TARGET_NOT_FOUND"),
        ({"op": "set", "pointer": "/server/peers/-", "value": "x"}, "INVALID_POINTER"),
        ({"op": "set", "pointer": "/server/peers/01", "value": "x"}, "INVALID_POINTER"),
        ({"op": "set", "pointer": "/server/~2", "value": "x"}, "INVALID_POINTER"),
        ({"op": "append", "pointer": "/server/port", "value": 1}, "INVALID_TARGET"),
        ({"op": "append", "pointer": "/sensors", "value": 1}, "INVALID_VALUE"),
        ({"op": "remove", "pointer": ""}, "INVALID_TARGET"),
        ({"op": "set", "pointer": "", "value": []}, "INVALID_TARGET"),
        ({"op": "unknown", "pointer": ""}, "INVALID_STEP"),
        ({"op": "remove", "pointer": "/server", "extra": 1}, "INVALID_STEP"),
    ],
)
def test_rejected_operation_leaves_document_intact(step, code):
    document = toml.parse(SOURCE)
    with pytest.raises(FileEditError) as exc:
        toml.edit(document, step)
    assert exc.value.code == code
    assert toml.serialize(document) == SOURCE


def test_root_replacement_and_summary():
    result = apply(SOURCE, {"op": "set", "pointer": "", "value": {"new": {"active": True}}})
    document = toml.parse(result)
    assert tomllib.loads(result.decode()) == {"new": {"active": True}}
    assert toml.summary(document) == {"root_type": "table", "keys": ["new"], "length": 1, "truncated": False}


@pytest.mark.parametrize("source", [b"x = 1\nx = 2\n", b"x = null\n", b"\xff"])
def test_invalid_input_is_rejected(source):
    with pytest.raises(FileEditError) as exc:
        toml.parse(source)
    assert exc.value.code == "INVALID_TOML"


def test_example_plan_matches_exact_source_and_executes():
    examples = Path(__file__).parents[1] / "examples" / "sfe"
    source = (examples / "service.toml").read_bytes()
    plan = json.loads((examples / "service.plan.json").read_bytes())
    assert plan["format"] == "toml"
    assert plan["base"]["sha256"] == sha256(source)
    result = apply(source, *plan["steps"])
    parsed = tomllib.loads(result.decode())
    assert parsed["server"]["port"] == 8081
    assert parsed["server"]["peers"] == ["primary", "backup"]
    assert parsed["server"]["enabled"] is True
    assert b"# Local HTTP port." in result


def test_failed_plan_writes_no_output_and_preserves_source(tmp_path):
    source = tmp_path / "source.toml"
    source.write_bytes(SOURCE)
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "protocol": "sfe/0.1",
                "format": "toml",
                "base": {"sha256": sha256(SOURCE)},
                "steps": [
                    {"op": "set", "pointer": "/server/port", "value": 9090},
                    {"op": "append", "pointer": "/server/peers", "value": None},
                ],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "edited.toml"
    result = CliRunner().invoke(app, ["apply", str(source), str(plan), "--output", str(output)])
    assert result.exit_code == 2, result.output
    assert json.loads(result.output)["code"] == "INVALID_VALUE"
    assert not output.exists()
    assert source.read_bytes() == SOURCE
