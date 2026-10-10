import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sfe_files.cli import app

ROOT = Path(__file__).resolve().parents[1]
runner = CliRunner()


def invoke(*args):
    result = runner.invoke(app, list(map(str, args)))
    assert result.exit_code == 0, (result.stdout, result.exception)
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    ("name", "selector", "expected"),
    [
        ("deployment.yaml", ["--pointer", "/spec/replicas"], 3),
        ("service.toml", ["--pointer", "/server/port"], 8081),
        ("schematic.svg", ["--id", "pump-label"], "Pump ready"),
    ],
)
def test_extended_profiles_through_common_cli(tmp_path, name, selector, expected):
    sample = ROOT / "examples/sfe" / name
    plan = sample.with_name(sample.stem + ".plan.json")
    source = tmp_path / name
    source.write_bytes(sample.read_bytes())
    output = tmp_path / ("edited-" + name)
    original = source.read_bytes()
    before = invoke("status", source)
    preview = invoke("preview", source, plan)
    assert not output.exists() and source.read_bytes() == original
    applied = invoke("apply", source, plan, "--output", output)
    after = invoke("status", output)
    selected = invoke("inspect", output, *selector)
    assert (selected["target"]["text"] if name.endswith(".svg") else selected["value"]) == expected
    assert preview["before_sha256"] == before["sha256"]
    assert preview["after_sha256"] == applied["after_sha256"] == after["sha256"]
    assert source.read_bytes() == original


def test_contract_exposes_new_instruction_arguments():
    profiles = invoke("contract")["formats"]
    assert set(profiles) == {
        "json", "csv", "markdown", "yaml", "toml", "svg", "xlsx", "docx", "pptx",
        "srt", "vtt", "ics", "vcf", "jsonl",
        "odt", "ods", "odp", "html", "epub",
        "drawio", "geojson", "gpx", "kml", "sqlite", "parquet", "arrow", "dxf", "kicad_sch", "musicxml",
        "minecraft-java-world",
    }
    for fmt in profiles:
        instructions = profiles[fmt]["instruction_set"]
        assert set(profiles[fmt]["operations"]) == set(instructions["operations"])
        assert all(
            operation["example"]["op"] == name for name, operation in instructions["operations"].items()
        )
        assert instructions["addressing"] and instructions["limitations"]
