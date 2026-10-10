"""Run thirty file-edit profiles through real CLI processes and save evidence."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tomllib
import zlib
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree
from zipfile import ZipFile

import ezdxf
import pyarrow as pa
import pyarrow.parquet as pq
from pyarrow import ipc
from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = (args.output or ROOT / "build" / f"sfe-trial-{datetime.now(UTC):%Y%m%d-%H%M%S}").resolve()
    output.mkdir(parents=True, exist_ok=False)
    for name in ("sources", "plans", "edited", "logs"):
        (output / name).mkdir()
    calls = []
    checks = {}

    def run(label, module, *arguments, exit_code=0):
        command = [sys.executable, "-m", module, *map(str, arguments)]
        result = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            check=False,
        )
        record = {
            "label": label,
            "command": command,
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
        calls.append(record)
        write_json(output / "logs" / f"{len(calls):02d}-{label}.json", record)
        if result.returncode != exit_code:
            raise RuntimeError(f"{label}: expected exit {exit_code}, got {result.returncode}; see logs")
        value = json.loads(result.stdout)
        if exit_code == 0 and value.get("ok") is False:
            raise RuntimeError(f"{label}: command rejected: {value}")
        return value

    files = "sfe_files.cli"
    samples = ROOT / "examples" / "sfe"
    for name, inspect_args in (
        ("settings.json", ["--pointer", "/enabled"]),
        ("assets.csv", ["--key-column", "id", "--key", "P-01"]),
        ("handover.md", ["--heading", "Findings"]),
        ("deployment.yaml", ["--pointer", "/spec/replicas"]),
        ("service.toml", ["--pointer", "/server/port"]),
        ("schematic.svg", ["--id", "pump"]),
        ("budget.xlsx", ["--pointer", "/sheets/Budget/B5"]),
        ("meeting.docx", ["--pointer", "/paragraphs/1"]),
        ("project.pptx", ["--pointer", "/slides/1"]),
        ("captions.srt", ["--pointer", "/cues/2"]),
        ("subtitles.vtt", ["--pointer", "/cues/2"]),
        ("schedule.ics", ["--pointer", "/events/review-1@example.test"]),
        ("contacts.vcf", ["--pointer", "/records/1"]),
        ("records.jsonl", ["--pointer", "/by-key/id/P-01/status"]),
        ("notes.odt", ["--pointer", "/paragraphs/2"]),
        ("costs.ods", ["--pointer", "/sheets/Costs/B3"]),
        ("briefing.odp", ["--pointer", "/slides/1/objects/status"]),
        ("page.html", ["--pointer", "/elements/status"]),
        ("handbook.epub", ["--pointer", "/chapters/review/elements/status"]),
        ("network.drawio", ["--pointer", "/pages/2/cells/pump"]),
        ("sites.geojson", ["--pointer", "/by-id/pump"]),
        ("walk.gpx", ["--pointer", "/routes/1/points/2"]),
        ("plant.kml", ["--pointer", "/placemarks/pump"]),
        ("equipment.sqlite", ["--pointer", "/tables/equipment/rows/P-01"]),
        ("inventory.parquet", ["--pointer", "/by-key/id/P-01"]),
        ("telemetry.arrow", ["--pointer", "/by-key/id/P-01"]),
        ("layout.dxf", ["--pointer", "/entities/32"]),
        ("control.kicad_sch", ["--pointer", "/symbols/e2b62867-7d5d-4a92-9064-d715d42a6657"]),
        ("melody.musicxml", ["--pointer", "/parts/P1/measures/1/voices/1/notes/3"]),
    ):
        stem = Path(name).stem
        source, plan, edited = (
            output / "sources" / name,
            output / "plans" / f"{stem}.plan.json",
            output / "edited" / name,
        )
        shutil.copyfile(samples / name, source)
        shutil.copyfile(samples / plan.name, plan)
        before_hash = digest(source)
        run(f"{stem}-status-before", files, "status", source)
        run(f"{stem}-inspect-before", files, "inspect", source, *inspect_args)
        preview = run(f"{stem}-preview", files, "preview", source, plan)
        assert digest(source) == before_hash and not edited.exists()
        applied = run(f"{stem}-apply", files, "apply", source, plan, "--output", edited)
        run(f"{stem}-inspect-after", files, "inspect", edited, *inspect_args)
        checks[f"{stem}_source_unchanged"] = digest(source) == before_hash
        checks[f"{stem}_preview_matches_output"] = (
            preview["after_sha256"] == digest(edited) == applied["after_sha256"]
        )
        if source.suffix in (".drawio", ".geojson", ".gpx", ".kml", ".sqlite", ".parquet", ".arrow", ".dxf", ".kicad_sch", ".musicxml"):
            checks[f"{stem}_semantic_preview"] = preview["diff_kind"] == "semantic" and bool(preview["diff"])
        if source.suffix in (".xlsx", ".docx", ".pptx", ".odt", ".ods", ".odp", ".epub"):
            changed = preview["package_parts"]
            checks[f"{stem}_semantic_preview"] = preview["diff_kind"] == "semantic" and bool(preview["diff"])
            with ZipFile(source) as old, ZipFile(edited) as new:
                untouched = set(old.namelist()) - set(changed["modified"]) - set(changed["removed"])
                checks[f"{stem}_untouched_parts_preserved"] = bool(untouched) and all(
                    old.read(part) == new.read(part) for part in untouched
                )
                for part in new.namelist():
                    if part.endswith((".xml", ".rels", ".xhtml")):
                        ElementTree.fromstring(new.read(part))
                checks[f"{stem}_xml_parts_parse"] = True
        if source.suffix in (".yaml", ".toml", ".svg", ".xlsx", ".docx", ".pptx", ".srt", ".vtt", ".ics", ".vcf", ".jsonl", ".odt", ".ods", ".odp", ".html", ".epub", ".drawio", ".geojson", ".gpx", ".kml", ".sqlite", ".parquet", ".arrow", ".dxf", ".kicad_sch", ".musicxml"):
            rejected_output = output / "edited" / f"{stem}-rejected{source.suffix}"
            rejection = run(
                f"{stem}-stale-plan",
                files,
                "apply",
                edited,
                plan,
                "--output",
                rejected_output,
                exit_code=2,
            )
            checks[f"{stem}_stale_plan_rejected"] = (
                rejection["code"] == "BASE_CONFLICT" and not rejected_output.exists()
            )
            bad_plan = json.loads(plan.read_text(encoding="utf-8"))
            bad_plan["steps"].append({"op": "unsupported"})
            bad_path = output / "plans" / f"{stem}-invalid.plan.json"
            write_json(bad_path, bad_plan)
            rejection = run(
                f"{stem}-failed-batch",
                files,
                "apply",
                source,
                bad_path,
                "--output",
                rejected_output,
                exit_code=2,
            )
            checks[f"{stem}_failed_batch_no_output"] = (
                rejection["code"] == "INVALID_STEP"
                and not rejected_output.exists()
                and digest(source) == before_hash
            )

    settings = json.loads((output / "edited/settings.json").read_text(encoding="utf-8"))
    checks["json_values"] = settings == {
        "site": "North Plant",
        "enabled": True,
        "alarms": ["high-temperature", "low-pressure"],
        "reviewed_by": "agent",
    }
    with (output / "edited/assets.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    checks["csv_values"] = rows == [
        {"id": "P-01", "name": "Feed pump", "status": "ready"},
        {"id": "V-02", "name": "Isolation valve", "status": "ready"},
        {"id": "T-03", "name": "Tank", "status": "inspection"},
    ]
    markdown = (output / "edited/handover.md").read_text(encoding="utf-8")
    checks["markdown_values"] = (
        "Check the two equipment records." in markdown
        and "P-01 is ready. V-02 remains ready." in markdown
        and "## Next action" in markdown
        and "Pending review." not in markdown
    )
    yaml_text = (output / "edited/deployment.yaml").read_text(encoding="utf-8")
    deployment = YAML(typ="safe", pure=True).load(yaml_text)
    checks["yaml_values"] = (
        deployment["spec"]["replicas"] == 3
        and deployment["spec"]["ports"] == [8080, 8443]
        and deployment["metadata"]["labels"]["reviewed"] == "agent"
    )
    checks["yaml_comments_preserved"] = (
        "# 车间 HMI 服务：保留运维注释" in yaml_text and "# 调整实例数量" in yaml_text
    )
    toml_text = (output / "edited/service.toml").read_text(encoding="utf-8")
    service = tomllib.loads(toml_text)
    checks["toml_values"] = (
        service["server"]["port"] == 8081
        and service["server"]["peers"] == ["primary", "backup"]
        and service["server"]["enabled"] is True
    )
    checks["toml_date_and_comment_preserved"] = (
        service["commissioned"] == date(2026, 9, 26) and "# Local HTTP port." in toml_text
    )
    svg_before = ElementTree.parse(output / "sources/schematic.svg")
    svg_after = ElementTree.parse(output / "edited/schematic.svg")
    svg_nodes = {node.get("id"): node for node in svg_after.iter() if node.get("id")}
    checks["svg_values"] = (
        svg_nodes["pump"].get("transform") == "translate(24 12) translate(48 64)"
        and svg_nodes["pump-body"].get("fill") == "#16a34a"
        and svg_nodes["pump-label"].text == "Pump ready"
    )
    checks["svg_other_objects_preserved"] = all(
        ElementTree.tostring(svg_before.find(f".//*[@id='{node_id}']"))
        == ElementTree.tostring(svg_nodes[node_id])
        for node_id in ("tank", "pipe")
    )

    subtitle_values = {}
    for name in ("captions.srt", "subtitles.vtt"):
        original = (output / "sources" / name).read_bytes()
        edited = (output / "edited" / name).read_bytes()
        text = edited.decode("utf-8-sig")
        comma = "," if name.endswith(".srt") else "."
        subtitle_values[name] = (
            f"00:04{comma}250 --> 00:06{comma}750" in text
            if name.endswith(".vtt") else f"00:00:04{comma}250 --> 00:00:06{comma}750" in text
        )
        checks[f"{Path(name).stem}_values"] = subtitle_values[name] and "ready for commissioning" in text
        checks[f"{Path(name).stem}_bom_preserved"] = original.startswith(b"\xef\xbb\xbf") == edited.startswith(b"\xef\xbb\xbf")
    vtt_source = (output / "sources/subtitles.vtt").read_text(encoding="utf-8-sig")
    vtt_after = (output / "edited/subtitles.vtt").read_text(encoding="utf-8-sig")
    checks["vtt_header_styles_notes_preserved"] = (
        vtt_source.split("intro\n")[0] == vtt_after.split("intro\n")[0]
        and "region:status align:start" in vtt_after
    )
    ics_before = (output / "sources/schedule.ics").read_bytes()
    ics_after = (output / "edited/schedule.ics").read_bytes()
    checks["ics_values"] = all(value in ics_after for value in (
        b"LOCATION:Room B", b"DTSTART:20261001T030000Z", b"DTEND:20261001T040000Z",
    ))
    checks["ics_timezone_alarms_preserved"] = all(
        ics_before.split(b"BEGIN:" + component, 1)[1].split(b"END:" + component, 1)[0]
        == ics_after.split(b"BEGIN:" + component, 1)[1].split(b"END:" + component, 1)[0]
        for component in (b"VTIMEZONE", b"VALARM")
    )
    vcf_before = (output / "sources/contacts.vcf").read_bytes()
    vcf_after = (output / "edited/contacts.vcf").read_bytes()
    checks["vcf_values"] = all(value in vcf_after for value in (
        b"li.support@example.com", b"ORG:EAS;Field Service", b"tel:+66-2-555-0100",
    ))
    checks["vcf_other_contact_preserved"] = vcf_before.split(b"END:VCARD", 1)[1] == vcf_after.split(b"END:VCARD", 1)[1]
    jsonl_before = (output / "sources/records.jsonl").read_bytes().splitlines(keepends=True)
    jsonl_after = (output / "edited/records.jsonl").read_bytes().splitlines(keepends=True)
    records = [json.loads(line) for line in jsonl_after]
    checks["jsonl_values"] = (
        len(records) == 3 and records[0]["status"] == "ready"
        and records[0]["inspection"]["approved"] is True and records[2]["id"] == "T-03"
    )
    checks["jsonl_untouched_record_preserved"] = jsonl_before[1] == jsonl_after[1]

    # Read package XML independently of the adapters to check actual persisted values.
    with ZipFile(output / "edited/budget.xlsx") as archive:
        sheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        sns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        checks["xlsx_values"] = (
            sheet.find(".//s:c[@r='B5']/s:v", sns).text == "1800"
            and sheet.find(".//s:c[@r='C7']/s:f", sns).text == "SUM(C5:C6)"
            and sheet.find(".//s:c[@r='A5']/s:is/s:t", sns).text == "HMI controls"
        )
        checks["xlsx_formula_cache_cleared"] = all(
            cell.find("s:v", sns) is None for cell in sheet.findall(".//s:c", sns)
            if cell.find("s:f", sns) is not None
        )
    with ZipFile(output / "edited/meeting.docx") as archive:
        document = ElementTree.fromstring(archive.read("word/document.xml"))
        wns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        paragraphs = document.findall("w:body/w:p", wns)
        cell = document.findall("w:body/w:tbl/w:tr", wns)[1].findall("w:tc", wns)[2]
        checks["docx_values"] = (
            len(paragraphs) == 6
            and "Approved for staging." in "".join(paragraphs[1].itertext())
            and "".join(node.text or "" for node in cell.findall(".//w:t", wns)) == "Complete"
            and "Follow-up: verify alarms" in "".join(paragraphs[-1].itertext())
        )
    with ZipFile(output / "edited/project.pptx") as archive:
        slide = ElementTree.fromstring(archive.read("ppt/slides/slide1.xml"))
        pns = {
            "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
            "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
        }
        shape = next(
            item for item in slide.findall("p:cSld/p:spTree/p:sp", pns)
            if item.find("p:nvSpPr/p:cNvPr", pns).get("id") == "2"
        )
        checks["pptx_values"] = (
            "".join(node.text or "" for node in shape.findall(".//a:t", pns)) == "Status: Ready for review"
            and shape.find("p:spPr/a:xfrm/a:off", pns).attrib == {"x": "914400", "y": "2286000"}
            and shape.find("p:spPr/a:xfrm/a:ext", pns).attrib == {"cx": "8229600", "cy": "1371600"}
        )

    odf_ns = {
        "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
        "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
        "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
        "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
        "svg": "urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0",
    }
    def odf_text(node):
        result = node.text or ""
        for child in node:
            if child.tag == "{" + odf_ns["text"] + "}s":
                result += " " * int(child.get("{" + odf_ns["text"] + "}c", "1"))
            elif child.tag == "{" + odf_ns["text"] + "}tab":
                result += "\t"
            else:
                result += odf_text(child)
            result += child.tail or ""
        return result

    with ZipFile(output / "edited/notes.odt") as archive:
        root = ElementTree.fromstring(archive.read("content.xml"))
        body = root.find("office:body/office:text", odf_ns)
        paragraphs = [node for node in body if node.tag in {
            "{" + odf_ns["text"] + "}p", "{" + odf_ns["text"] + "}h",
        }]
        table = body.find("table:table[@table:name='Actions']", odf_ns)
        rows = table.findall("table:table-row", odf_ns)
        cell = rows[1].findall("table:table-cell", odf_ns)[1]
        checks["odt_values"] = (
            odf_text(paragraphs[1]) == "Approved for staging."
            and odf_text(cell) == "Complete"
            and odf_text(paragraphs[-1]) == "Verify alarms after deployment."
        )
    with ZipFile(output / "edited/costs.ods") as archive:
        root = ElementTree.fromstring(archive.read("content.xml"))
        table = root.find("office:body/office:spreadsheet/table:table[@table:name='Costs']", odf_ns)
        rows = table.findall("table:table-row", odf_ns)
        cells = [row.findall("table:table-cell", odf_ns) for row in rows]
        checks["ods_values"] = (
            cells[2][1].get("{" + odf_ns["office"] + "}value") == "75"
            and "".join(cells[3][0].itertext()) == "现场调试"
            and cells[4][2].get("{" + odf_ns["table"] + "}formula") == "of:=SUM([.B2:.B4])"
        )
        checks["ods_formula_cache_cleared"] = (
            "{" + odf_ns["office"] + "}value" not in cells[4][2].attrib
        )
    with ZipFile(output / "edited/briefing.odp") as archive:
        root = ElementTree.fromstring(archive.read("content.xml"))
        slide = root.find("office:body/office:presentation/draw:page", odf_ns)
        shape = slide.find("draw:frame[@draw:name='status']", odf_ns)
        checks["odp_values"] = (
            "Approved for staging" in odf_text(shape)
            and shape.get("{" + odf_ns["svg"] + "}x") == "2.5cm"
            and shape.get("{" + odf_ns["svg"] + "}y") == "5cm"
            and shape.get("{" + odf_ns["svg"] + "}width") == "21cm"
            and shape.get("{" + odf_ns["svg"] + "}height") == "2.5cm"
        )
    html_before = (output / "sources/page.html").read_text(encoding="utf-8")
    html_after = (output / "edited/page.html").read_text(encoding="utf-8")
    checks["html_values"] = (
        '<p id="status" class="ready">Ready for review</p>' in html_after
        and '<p id="follow-up" class="note">Verify alarms after staging.</p>' in html_after
    )
    checks["html_untouched_content"] = (
        '<script type="application/json" id="metadata">{"site":"North Plant","revision":1}</script>'
        in html_before and html_before.split("  <!-- Keep this source comment", 1)[1]
        == html_after.split("  <!-- Keep this source comment", 1)[1]
    )
    with ZipFile(output / "edited/handbook.epub") as archive:
        opf = ElementTree.fromstring(archive.read("EPUB/package.opf"))
        chapter = ElementTree.fromstring(archive.read("EPUB/Text/review.xhtml"))
        opf_ns = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}
        chapter_ns = {"h": "http://www.w3.org/1999/xhtml"}
        checks["epub_values"] = (
            opf.find("opf:metadata/dc:title", opf_ns).text == "Commissioning handbook: reviewed"
            and opf.find("opf:metadata/opf:meta[@property='dcterms:modified']", opf_ns).text
            == "2026-09-26T00:00:00Z"
            and chapter.find(".//h:p[@id='status']", chapter_ns).text == "Ready for review."
        )
        with ZipFile(output / "sources/handbook.epub") as original:
            checks["epub_spine_navigation_preserved"] = (
                archive.read("EPUB/nav.xhtml") == original.read("EPUB/nav.xhtml")
            )

    drawio_before = ElementTree.parse(output / "sources/network.drawio").getroot()
    drawio_after = ElementTree.parse(output / "edited/network.drawio").getroot()
    diagrams_before, diagrams_after = drawio_before.findall("diagram"), drawio_after.findall("diagram")
    checks["drawio_untouched_page"] = (
        ElementTree.tostring(diagrams_before[0]) == ElementTree.tostring(diagrams_after[0])
    )
    graph_xml = unquote(zlib.decompress(base64.b64decode(diagrams_after[1].text), -15).decode("utf-8"))
    graph = ElementTree.fromstring(graph_xml)
    cells = {cell.get("id"): cell for cell in graph.findall("root/mxCell")}
    checks["drawio_values"] = (
        cells["pump"].get("value") == "Pump ready"
        and cells["pump"].find("mxGeometry").get("width") == "120"
        and cells["link"].get("source") == "tank"
    )
    geo_before = json.loads((output / "sources/sites.geojson").read_text(encoding="utf-8"))
    geo_after = json.loads((output / "edited/sites.geojson").read_text(encoding="utf-8"))
    checks["geojson_values"] = (
        geo_after["features"][0]["properties"]["status"] == "ready"
        and geo_after["features"][0]["geometry"]["coordinates"] == [100.505, 13.705]
        and geo_after["features"][2]["properties"]["reviewed"] is True
    )
    checks["geojson_untouched_feature"] = geo_before["features"][1] == geo_after["features"][1]
    gpx_ns = {"g": "http://www.topografix.com/GPX/1/1"}
    gpx = ElementTree.parse(output / "edited/walk.gpx")
    checks["gpx_values"] = (
        gpx.find("g:wpt/g:name", gpx_ns).text == "Reviewed pump entrance"
        and gpx.find(".//g:rtept[2]", gpx_ns).get("lat") == "13.715"
        and gpx.find(".//g:trkpt[2]/g:time", gpx_ns).text == "2026-09-27T09:05:00Z"
    )
    checks["gpx_segment_preserved"] = (
        len(gpx.findall(".//g:trkseg", gpx_ns)) == 1
        and gpx.find(".//g:trkpt[1]/g:time", gpx_ns).text == "2026-09-26T08:00:00Z"
    )
    kml_ns = {"k": "http://www.opengis.net/kml/2.2"}
    kml = ElementTree.parse(output / "edited/plant.kml")
    checks["kml_values"] = (
        kml.find(".//k:Placemark[@id='pump']/k:name", kml_ns).text == "Feed pump reviewed"
        and kml.find(".//k:Placemark[@id='pump']/k:Point/k:coordinates", kml_ns).text
        == "100.505,13.705,0"
    )
    checks["kml_style_other_mark_preserved"] = (
        kml.find(".//k:Style[@id='ready']", kml_ns) is not None
        and kml.find(".//k:Placemark[@id='tank']/k:name", kml_ns).text == "Tank"
    )
    with sqlite3.connect(output / "edited/equipment.sqlite") as connection:
        checks["sqlite_values"] = (
            connection.execute("SELECT status FROM equipment WHERE id='P-01'").fetchone()[0] == "ready"
            and connection.execute("SELECT capacity FROM equipment WHERE id='T-03'").fetchone()[0] == 80
        )
        checks["sqlite_integrity_other_table"] = (
            connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            and connection.execute("SELECT detail FROM history WHERE id='H-01'").fetchone()[0]
            == "Original review record"
        )
    parquet = pq.ParquetFile(output / "edited/inventory.parquet")
    checks["parquet_values"] = (
        parquet.read().to_pylist()[0]["status"] == "ready"
        and parquet.read().to_pylist()[-1]["id"] == "S-04"
    )
    checks["parquet_schema_groups"] = (
        parquet.schema_arrow.metadata[b"source"] == b"EAFS sample"
        and parquet.num_row_groups == 3
    )
    arrow = ipc.open_file(pa.memory_map(str(output / "edited/telemetry.arrow")))
    checks["arrow_values"] = (
        arrow.read_all().to_pylist()[0]["approved"] is True
        and arrow.read_all().to_pylist()[-1]["id"] == "S-04"
    )
    checks["arrow_schema_batches"] = (
        arrow.schema.metadata[b"source"] == b"EAFS sample"
        and arrow.num_record_batches == 3
    )
    drawing = ezdxf.readfile(output / "edited/layout.dxf")
    entities = {entity.dxf.handle: entity for entity in drawing.modelspace()}
    checks["dxf_values"] = (
        entities["32"].dxf.text == "Pump ready"
        and tuple(entities["31"].dxf.start)[:2] == (10, 5)
        and entities["33"].dxf.layer == "REVIEWED"
    )
    schematic = (output / "edited/control.kicad_sch").read_text(encoding="utf-8")
    checks["kicad_values"] = (
        '(property "Value" "12k"' in schematic
        and '(at 110 80 0)' in schematic
        and '(reference "R1")' in schematic
    )
    music = ElementTree.parse(output / "edited/melody.musicxml")
    notes = music.findall(".//part[@id='P1']/measure[@number='1']/note")
    checks["musicxml_values"] = (
        [int(note.findtext("duration")) for note in notes] == [4, 8, 4]
        and notes[2].findtext("pitch/step") == "F"
        and notes[2].get("dynamics") == "78"
    )
    checks["musicxml_measure_duration"] = sum(int(note.findtext("duration")) for note in notes) == 16

    stale = output / "sources/stale.json"
    stale.write_bytes((output / "sources/settings.json").read_bytes() + b"\n")
    rejected_output = output / "edited/rejected.json"
    rejection = run(
        "json-stale-plan",
        files,
        "apply",
        stale,
        output / "plans/settings.plan.json",
        "--output",
        rejected_output,
        exit_code=2,
    )
    checks["json_stale_plan_rejected"] = rejection["code"] == "BASE_CONFLICT" and not rejected_output.exists()

    eas = "eas_hmi.cli"
    project = output / "eas-project"
    eas_plan = output / "plans/eas.plan.json"
    shutil.copyfile(samples / "eas.plan.json", eas_plan)
    run("eas-init", eas, "--project", project, "init", "--from-model", ROOT / "examples/stage4/model.json")
    before = run("eas-status-before", eas, "--project", project, "status")
    before_node = run("eas-inspect-before", eas, "--project", project, "inspect", "CARD_A")
    write_json(output / "eas-before.json", before_node)
    head_before = digest(project / "HEAD.json")
    preview = run("eas-preview", eas, "--project", project, "apply-plan", eas_plan, "--dry-run")
    checks["eas_preview_did_not_commit"] = (
        digest(project / "HEAD.json") == head_before and not preview["committed"]
    )
    applied = run("eas-apply", eas, "--project", project, "apply-plan", eas_plan)
    after = run("eas-status-after", eas, "--project", project, "status")
    after_node = run("eas-inspect-after", eas, "--project", project, "inspect", "CARD_A")
    write_json(output / "eas-after.json", after_node)
    change = run("eas-diff", eas, "--project", project, "diff", "--json")
    write_json(output / "eas-diff.json", change)
    checks["eas_one_revision"] = before["revision"] == 0 and applied["revision"] == after["revision"] == 1
    checks["eas_values"] = (
        after_node["target"]["x"] == before_node["target"]["x"] + 10
        and after_node["target"]["y"] == before_node["target"]["y"] + 5
        and after_node["target"]["metadata"]["reviewed"] is True
    )
    checks["eas_preview_matches_output"] = preview["hash_after"] == after["canonical_hash"]
    rejection = run("eas-stale-plan", eas, "--project", project, "apply-plan", eas_plan, exit_code=3)
    checks["eas_stale_plan_rejected"] = rejection["code"] == "REVISION_CONFLICT"

    invalid_plan = output / "plans/eas-invalid.plan.json"
    write_json(
        invalid_plan,
        {
            "protocol": "sfe/0.1",
            "format": "eas-hmi",
            "base": {"revision": after["revision"], "semantic_hash": after["canonical_hash"]},
            "steps": [
                {"op": "move", "ids": ["CARD_A"], "dx": 20},
                {"op": "set", "ids": ["CARD_A"], "property": "width", "value": -1},
            ],
        },
    )
    head_before = digest(project / "HEAD.json")
    rejection = run(
        "eas-failed-second-step", eas, "--project", project, "apply-plan", invalid_plan, exit_code=2
    )
    checks["eas_failed_batch_rolled_back"] = (
        rejection["code"] == "VALIDATION_FAILED" and digest(project / "HEAD.json") == head_before
    )
    undo_project = output / "eas-undo-check"
    shutil.copytree(project, undo_project)
    run("eas-undo", eas, "--project", undo_project, "undo")
    restored = run("eas-restored-status", eas, "--project", undo_project, "status")
    checks["eas_undo_restored_content"] = restored["canonical_hash"] == before["canonical_hash"]

    report = {
        "ok": all(checks.values()),
        "command_count": len(calls),
        "checks": checks,
        "output": str(output),
        "screen_capture_count": 0,
    }
    write_json(output / "results.json", report)
    rows = "\n".join(f"| {name} | {'PASS' if passed else 'FAIL'} |" for name, passed in checks.items())
    (output / "REPORT.md").write_text(
        "# 文件指令集实跑结果\n\n"
        f"真实执行 {len(calls)} 次 CLI 调用，含预期失败的冲突/回滚检查；屏幕截图 0 次。\n\n"
        "| 格式 | 实际修改 | 输出 |\n|---|---|---|\n"
        "| JSON | enabled=false→true；追加告警；添加 reviewed_by | [settings.json](edited/settings.json) |\n"
        "| CSV | P-01 改为 ready；增加 T-03 | [assets.csv](edited/assets.csv) |\n"
        "| Markdown | 替换 Findings；追加 Next action | [handover.md](edited/handover.md) |\n"
        "| YAML | replicas 1→3；追加端口；添加 reviewed 标签 | [deployment.yaml](edited/deployment.yaml) |\n"
        "| TOML | port 8080→8081；追加 backup；enabled=true | [service.toml](edited/service.toml) |\n"
        "| SVG | pump 平移 (24,12)；变绿；标签变为 Pump ready | [schematic.svg](edited/schematic.svg) |\n"
        "| XLSX | 按工作表和单元格修改数值、公式 | [budget.xlsx](edited/budget.xlsx) |\n"
        "| DOCX | 修改段落、表格单元格、追加段落 | [meeting.docx](edited/meeting.docx) |\n"
        "| PPTX | 修改幻灯片文字、对象位置和尺寸 | [project.pptx](edited/project.pptx) |\n"
        "| SRT | 改第 2 条字幕；整体延后 250ms | [captions.srt](edited/captions.srt) |\n"
        "| WebVTT | 改第 2 条字幕；延后 250ms；保留样式和设置 | [subtitles.vtt](edited/subtitles.vtt) |\n"
        "| ICS | 更改会议地点与说明；UTC 时间延后一小时 | [schedule.ics](edited/schedule.ics) |\n"
        "| VCF | 更改邮箱、组织；添加电话 | [contacts.vcf](edited/contacts.vcf) |\n"
        "| JSONL | 按 id 修改状态与检查结果；追加记录 | [records.jsonl](edited/records.jsonl) |\n"
        "| ODT | 修改段落和表格单元格；追加段落 | [notes.odt](edited/notes.odt) |\n"
        "| ODS | 修改重复行内单元格、文字和公式 | [costs.ods](edited/costs.ods) |\n"
        "| ODP | 修改幻灯片文字、位置和尺寸 | [briefing.odp](edited/briefing.odp) |\n"
        "| HTML | 按 ID 修改文字和属性；追加元素 | [page.html](edited/page.html) |\n"
        "| EPUB | 修改元数据、章节文字和时间戳 | [handbook.epub](edited/handbook.epub) |\n"
        "| draw.io | 修改压缩页节点文字、几何和边端点 | [network.drawio](edited/network.drawio) |\n"
        "| GeoJSON | 按 Feature ID 修改属性和点位 | [sites.geojson](edited/sites.geojson) |\n"
        "| GPX | 修改航点、路线点和轨迹点时间 | [walk.gpx](edited/walk.gpx) |\n"
        "| KML | 按 Placemark ID 修改名称、说明和点位 | [plant.kml](edited/plant.kml) |\n"
        "| SQLite | 按主键修改和追加设备记录 | [equipment.sqlite](edited/equipment.sqlite) |\n"
        "| Parquet | 按业务键修改和追加 typed row | [inventory.parquet](edited/inventory.parquet) |\n"
        "| Arrow IPC | 按业务键修改并追加 record batch | [telemetry.arrow](edited/telemetry.arrow) |\n"
        "| DXF | 按 handle 修改文字、位置和图层 | [layout.dxf](edited/layout.dxf) |\n"
        "| KiCad | 按 UUID 修改元件值和位置 | [control.kicad_sch](edited/control.kicad_sch) |\n"
        "| MusicXML | 修改音高、时值和力度 | [melody.musicxml](edited/melody.musicxml) |\n"
        "| EAS-HMI | CARD_A (40,300)→(50,305)；reviewed=true；revision 0→1 | [编辑后对象](eas-after.json) |\n\n"
        "EAS 编辑后的工程保留在 `eas-project/`；撤销检查在其副本 `eas-undo-check/` 执行。\n\n"
        "Office/ODF/EPUB 验证范围：此脚本检查实际 XML 值、未改部件保留、语义预览与输出一致。"
        "XLSX/ODS 公式缓存被清除，需电子表格引擎重算；此脚本不验证排版。\n\n"
        "GPX/KML/MusicXML 固定样例另以规范 XML Schema 校验；KiCad 未运行外部 ERC，DXF 未运行 CAD 排版检查。\n\n"
        "## 验证\n\n| 检查 | 结果 |\n|---|---|\n" + rows + "\n\n"
        "[机器可读结果](results.json) · 每次调用的命令、stdout、stderr、退出码见 `logs/`。\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
