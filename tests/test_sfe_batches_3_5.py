"""Public workflow and independent checks for batches 3, 4 and 5."""

import base64
import io
import json
import sqlite3
import zlib
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree

import ezdxf
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from lxml import etree
from pyarrow import ipc
from typer.testing import CliRunner

from sfe_files.cli import app

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "examples" / "sfe"
RUNNER = CliRunner()
CASES = [
    ("network.drawio", "/pages/2/cells/pump"),
    ("sites.geojson", "/by-id/pump"),
    ("walk.gpx", "/routes/1/points/2"),
    ("plant.kml", "/placemarks/pump"),
    ("equipment.sqlite", "/tables/equipment/rows/P-01"),
    ("inventory.parquet", "/by-key/id/P-01"),
    ("telemetry.arrow", "/by-key/id/P-01"),
    ("layout.dxf", "/entities/32"),
    ("control.kicad_sch", "/symbols/e2b62867-7d5d-4a92-9064-d715d42a6657"),
    ("melody.musicxml", "/parts/P1/measures/1/voices/1/notes/3"),
]


def invoke(*args, code=0):
    result = RUNNER.invoke(app, list(map(str, args)))
    assert result.exit_code == code, (result.stdout, result.exception)
    return json.loads(result.stdout)


@pytest.mark.parametrize(("name", "pointer"), CASES)
def test_public_workflow_and_atomic_failure(tmp_path, name, pointer):
    sample = SAMPLES / name
    plan = sample.with_suffix(".plan.json")
    source = tmp_path / name
    original = sample.read_bytes()
    source.write_bytes(original)
    output = tmp_path / ("edited-" + name)
    before = invoke("status", source)
    invoke("inspect", source, "--pointer", pointer)
    preview = invoke("preview", source, plan)
    assert preview["diff_kind"] == "semantic" and preview["diff"] and not preview["committed"]
    assert source.read_bytes() == original and not output.exists()
    applied = invoke("apply", source, plan, "--output", output)
    after = invoke("status", output)
    invoke("inspect", output, "--pointer", pointer)
    assert preview["before_sha256"] == before["sha256"]
    assert preview["after_sha256"] == applied["after_sha256"] == after["sha256"]
    assert source.read_bytes() == original
    rejected = tmp_path / ("rejected-" + name)
    stale = invoke("apply", output, plan, "--output", rejected, code=2)
    assert stale["code"] == "BASE_CONFLICT" and not rejected.exists()
    invalid = json.loads(plan.read_text(encoding="utf-8"))
    invalid["steps"].append({"op": "unsupported"})
    bad_path = tmp_path / "invalid.json"
    bad_path.write_text(json.dumps(invalid), encoding="utf-8")
    failed = invoke("apply", source, bad_path, "--output", rejected, code=2)
    assert failed["code"] == "INVALID_STEP" and not rejected.exists()
    assert source.read_bytes() == original


def test_drawio_compressed_page_and_unchanged_other_page(tmp_path):
    source = SAMPLES / "network.drawio"
    output = tmp_path / "network.drawio"
    invoke("apply", source, source.with_suffix(".plan.json"), "--output", output)
    old, new = (etree.fromstring(path.read_bytes()) for path in (source, output))
    assert etree.tostring(old.findall("diagram")[0]) == etree.tostring(new.findall("diagram")[0])
    payload = new.findall("diagram")[1].text
    xml = unquote(zlib.decompress(base64.b64decode(payload), -15).decode("utf-8"))
    graph = etree.fromstring(xml.encode("utf-8"))
    cells = {x.get("id"): x for x in graph.findall("root/mxCell")}
    assert cells["pump"].get("value") == "Pump ready"
    assert cells["link"].get("source") == "tank"
    assert cells["pump"].find("mxGeometry").get("width") == "120"


def test_geojson_gpx_kml_preserve_unaddressed_objects(tmp_path):
    for name in ("sites.geojson", "walk.gpx", "plant.kml"):
        source = SAMPLES / name
        invoke("apply", source, source.with_suffix(".plan.json"), "--output", tmp_path / name)
    geo = json.loads((tmp_path / "sites.geojson").read_text(encoding="utf-8"))
    assert geo["features"][0]["geometry"]["coordinates"] == [100.505, 13.705]
    assert geo["features"][1] == json.loads((SAMPLES / "sites.geojson").read_text())["features"][1]
    gpx_ns = {"g": "http://www.topografix.com/GPX/1/1"}
    gpx = ElementTree.parse(tmp_path / "walk.gpx")
    assert gpx.find(".//g:rtept[2]", gpx_ns).get("lat") == "13.715"
    assert gpx.find(".//g:trkpt[1]/g:time", gpx_ns).text == "2026-09-26T08:00:00Z"
    kml_ns = {"k": "http://www.opengis.net/kml/2.2"}
    kml = ElementTree.parse(tmp_path / "plant.kml")
    assert kml.find(".//k:Placemark[@id='pump']/k:Point/k:coordinates", kml_ns).text == "100.505,13.705,0"
    assert kml.find(".//k:Placemark[@id='tank']/k:name", kml_ns).text == "Tank"
    assert kml.find(".//k:Style[@id='ready']", kml_ns) is not None


def test_sqlite_parquet_arrow_independent_readers(tmp_path):
    for name in ("equipment.sqlite", "inventory.parquet", "telemetry.arrow"):
        source = SAMPLES / name
        invoke("apply", source, source.with_suffix(".plan.json"), "--output", tmp_path / name)
    with sqlite3.connect(tmp_path / "equipment.sqlite") as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT status FROM equipment WHERE id='P-01'").fetchone()[0] == "ready"
        assert connection.execute("SELECT detail FROM history WHERE id='H-01'").fetchone()[0] == "Original review record"
    old_parquet = pq.ParquetFile(SAMPLES / "inventory.parquet")
    new_parquet = pq.ParquetFile(tmp_path / "inventory.parquet")
    assert new_parquet.num_row_groups == old_parquet.num_row_groups + 1
    assert new_parquet.schema_arrow.metadata[b"source"] == b"EAFS sample"
    assert new_parquet.read().to_pylist()[0]["status"] == "ready"
    old_arrow = ipc.open_file(pa.memory_map(str(SAMPLES / "telemetry.arrow")))
    new_arrow = ipc.open_file(pa.memory_map(str(tmp_path / "telemetry.arrow")))
    assert new_arrow.num_record_batches == old_arrow.num_record_batches + 1
    assert new_arrow.schema.metadata[b"source"] == b"EAFS sample"
    assert new_arrow.read_all().to_pylist()[0]["approved"] is True


def test_dxf_kicad_musicxml_independent_values(tmp_path):
    for name in ("layout.dxf", "control.kicad_sch", "melody.musicxml"):
        source = SAMPLES / name
        invoke("apply", source, source.with_suffix(".plan.json"), "--output", tmp_path / name)
    drawing = ezdxf.readfile(tmp_path / "layout.dxf")
    entities = {entity.dxf.handle: entity for entity in drawing.modelspace()}
    assert entities["32"].dxf.text == "Pump ready"
    assert tuple(entities["31"].dxf.start)[:2] == (10, 5)
    assert entities["33"].dxf.layer == "REVIEWED"
    schematic = (tmp_path / "control.kicad_sch").read_text(encoding="utf-8")
    assert '(property "Value" "12k"' in schematic
    assert '(at 110 80 0)' in schematic
    assert '(reference "R1")' in schematic
    music = ElementTree.parse(tmp_path / "melody.musicxml")
    notes = music.findall(".//part[@id='P1']/measure[@number='1']/note")
    assert [int(note.findtext("duration")) for note in notes] == [4, 8, 4]
    assert notes[2].findtext("pitch/step") == "F" and notes[2].get("dynamics") == "78"
    assert music.find(".//part[@id='P1']/measure[@number='2']/note/pitch/step").text == "G"


def test_domain_rejections_do_not_write(tmp_path):
    source = SAMPLES / "sites.geojson"
    invalid = json.loads(source.with_suffix(".plan.json").read_text())
    invalid["steps"] = [{"op": "set-point", "id": "pump", "longitude": 1000, "latitude": 13}]
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(invalid))
    out = tmp_path / "bad.geojson"
    assert invoke("apply", source, path, "--output", out, code=2)["code"] == "INVALID_STEP"
    assert not out.exists()
    stream = io.BytesIO()
    with ipc.new_stream(stream, pa.schema([("id", pa.string())])) as writer:
        writer.write_batch(pa.record_batch([["P-01"]], names=["id"]))
    path = tmp_path / "stream.arrow"
    path.write_bytes(stream.getvalue())
    assert invoke("status", path, code=2)["code"] == "INVALID_ARROW"
    sqlite_bytes = bytearray((SAMPLES / "equipment.sqlite").read_bytes())
    sqlite_bytes[18:20] = b"\x02\x02"
    path = tmp_path / "wal.sqlite"
    path.write_bytes(sqlite_bytes)
    assert invoke("status", path, code=2)["code"] == "UNSUPPORTED_SQLITE"
    source = SAMPLES / "melody.musicxml"
    invalid = json.loads(source.with_suffix(".plan.json").read_text())
    invalid["steps"] = invalid["steps"][:1]
    path = tmp_path / "bad-music-plan.json"
    path.write_text(json.dumps(invalid))
    out = tmp_path / "bad.musicxml"
    assert invoke("apply", source, path, "--output", out, code=2)["code"] == "INVALID_MUSICXML"
    assert not out.exists()
