from __future__ import annotations

import json
from pathlib import Path

import pytest

from sfe_files import yaml_profile as yaml
from sfe_files.core import FileEditError


def test_pointer_operations_keep_comments_and_quotes():
    source = b'# deploy\nname: "hmi"\nreplicas: 1 # keep\nports: [80]\nold: true\n'
    document = yaml.parse(source)
    for step in [
        {"op": "set", "pointer": "/replicas", "value": 3},
        {"op": "set", "pointer": "/name", "value": "eas-hmi"},
        {"op": "add", "pointer": "/a~1b", "value": {"~key": True}},
        {"op": "append", "pointer": "/ports", "value": 443},
        {"op": "add", "pointer": "/ports/1", "value": 8080},
        {"op": "remove", "pointer": "/old"},
    ]:
        document = yaml.edit(document, step)
    result = yaml.serialize(document)
    assert b"# deploy" in result and b"# keep" in result
    assert b'name: "eas-hmi"' in result
    assert yaml.inspect(yaml.parse(result), "/ports")["value"] == [80, 8080, 443]
    assert yaml.inspect(document, "/a~1b/~0key")["value"] is True
    assert "old" not in yaml.inspect(document)["value"]
    json.dumps(yaml.inspect(document), allow_nan=False)


@pytest.mark.parametrize(
    "source",
    [
        "name: a\nname: b\n",
        "---\na: 1\n---\nb: 2\n",
        "a: &value 1\n",
        "a: &value [1]\nb: *value\n",
        "a: {<<: {name: inherited}, name: direct}\n",
        "a: !custom value\n",
        "a: !!python/object:builtins.str {}\n",
        "a: 2026-09-26\n",
        "a: 2026-09-26T12:00:00Z\n",
        "1: value\n",
        "? [a, b]\n: value\n",
        "a: .nan\n",
        "a: .inf\n",
        "a: !!binary YQ==\n",
        "a: !!set {one: null}\n",
        "%YAML 1.1\n---\na: yes\n",
        "",
        "# only a comment\n",
    ],
)
def test_rejects_ambiguous_or_non_json_yaml(source):
    with pytest.raises(FileEditError):
        yaml.parse(source.encode())


def test_utf8_and_json_safe_scalars():
    document = yaml.parse('name: 车间\ndate: "2026-09-26"\ntruth: yes\nnull_value: null\n'.encode())
    assert yaml.inspect(document)["value"] == {
        "name": "车间", "date": "2026-09-26", "truth": "yes", "null_value": None
    }
    assert yaml.summary(document)["root_type"] == "dict"
    assert "车间" in yaml.serialize(document).decode()


def test_preserves_bom_crlf_and_document_markers():
    document = yaml.parse(b'\xef\xbb\xbf%YAML 1.2\r\n---\r\na: "x"\r\n...\r\n')
    yaml.edit(document, {"op": "set", "pointer": "/a", "value": "y"})
    result = yaml.serialize(document)
    assert result.startswith(b'\xef\xbb\xbf%YAML 1.2\r\n---\r\n')
    assert result.endswith(b'...\r\n')
    assert b'\n' not in result.replace(b'\r\n', b'')


@pytest.mark.parametrize(
    "step,code",
    [
        ({"op": "set", "pointer": "/missing", "value": 2}, "TARGET_NOT_FOUND"),
        ({"op": "set", "pointer": "/ports/01", "value": 2}, "TARGET_NOT_FOUND"),
        ({"op": "add", "pointer": "/ports/5", "value": 2}, "TARGET_NOT_FOUND"),
        ({"op": "append", "pointer": "/name", "value": 2}, "INVALID_TARGET"),
        ({"op": "remove", "pointer": ""}, "INVALID_TARGET"),
        ({"op": "set", "pointer": "/name", "value": 2, "unknown": True}, "INVALID_STEP"),
    ],
)
def test_invalid_edit_does_not_change_document(step, code):
    document = yaml.parse(b"name: hmi\nports: [80]\n")
    before = yaml.inspect(document)
    with pytest.raises(FileEditError) as exc:
        yaml.edit(document, step)
    assert exc.value.code == code
    assert yaml.inspect(document) == before


def test_root_replacement_and_list_deletion():
    document = yaml.parse(b"a: 1\n")
    yaml.edit(document, {"op": "set", "pointer": "", "value": [1, 2]})
    yaml.edit(document, {"op": "remove", "pointer": "/0"})
    yaml.edit(document, {"op": "add", "pointer": "/-", "value": 3})
    assert yaml.inspect(yaml.parse(yaml.serialize(document)))["value"] == [2, 3]


def test_example_plan_hash_and_result():
    import hashlib

    directory = Path(__file__).resolve().parents[1] / "examples" / "sfe"
    source = (directory / "deployment.yaml").read_bytes()
    plan = json.loads((directory / "deployment.plan.json").read_text(encoding="utf-8"))
    assert plan["base"]["sha256"] == hashlib.sha256(source).hexdigest()
    document = yaml.parse(source)
    for step in plan["steps"]:
        yaml.edit(document, step)
    output = yaml.serialize(document)
    assert yaml.inspect(yaml.parse(output), "/spec/replicas")["value"] == 3
    assert yaml.inspect(yaml.parse(output), "/spec/ports")["value"] == [8080, 8443]
    assert "保留运维注释" in output.decode()


def test_failed_plan_writes_no_output_and_preserves_input(tmp_path):
    import hashlib

    from typer.testing import CliRunner

    from sfe_files.cli import app

    source = b"# preserve\nreplicas: 1\nports: [80]\n"
    file = tmp_path / "deployment.yaml"
    file.write_bytes(source)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({
        "protocol": "sfe/0.1", "format": "yaml",
        "base": {"sha256": hashlib.sha256(source).hexdigest()},
        "steps": [
            {"op": "set", "pointer": "/replicas", "value": 3},
            {"op": "set", "pointer": "/missing", "value": 0},
        ],
    }), encoding="utf-8")
    output = tmp_path / "edited.yaml"
    result = CliRunner().invoke(app, ["apply", str(file), str(plan), "--output", str(output)])
    assert result.exit_code == 2
    assert json.loads(result.stdout)["code"] == "TARGET_NOT_FOUND"
    assert not output.exists()
    assert file.read_bytes() == source
