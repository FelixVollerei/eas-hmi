import io
import json
from copy import deepcopy
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

import pytest
from lxml import etree

from sfe_files import epub_profile as epub
from sfe_files.core import FileEditError, sha256

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples/sfe/handbook.epub"
OPF_PART = "EPUB/package.opf"
INTRO = "EPUB/Text/start here.xhtml"
REVIEW = "EPUB/Text/review.xhtml"


def sample_parts():
    return {
        "mimetype": b"application/epub+zip",
        "META-INF/container.xml": b"""<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>""",
        OPF_PART: b"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="book-id">urn:uuid:e3b5e702-301a-427b-9f1d-7597246cd8db</dc:identifier>
    <dc:title>Commissioning handbook</dc:title>
    <dc:creator>Operations team</dc:creator>
    <dc:language>en</dc:language>
    <dc:description>A sample handbook for structured file editing.</dc:description>
    <meta property="dcterms:modified">2026-09-25T00:00:00Z</meta>
    <meta property="rendition:layout">reflowable</meta>
  </metadata>
  <manifest>
    <item id="intro" href="Text/start%20here.xhtml" media-type="application/xhtml+xml"/>
    <item id="review" href="Text/review.xhtml" media-type="application/xhtml+xml"/>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="styles" href="Styles/book.css" media-type="text/css"/>
    <item id="image" href="Images/pump.svg" media-type="image/svg+xml"/>
  </manifest>
  <spine><itemref idref="intro"/><itemref idref="review"/></spine>
</package>""",
        INTRO: b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en" xml:lang="en">
  <head><title>Start here</title><link rel="stylesheet" type="text/css" href="../Styles/book.css"/></head>
  <body><section epub:type="chapter" id="start">
    <h1 id="heading">Start here</h1>
    <p id="overview">This handbook accompanies the equipment review.</p>
    <p id="links">Continue to the <a href="review.xhtml#status">review checklist</a>.</p>
    <img id="diagram" src="../Images/pump.svg" alt="Outline of a pump"/>
  </section></body>
</html>""",
        REVIEW: b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<!-- Keep this top-level comment. -->
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en" xml:lang="en">
  <head><title>Review checklist</title><link rel="stylesheet" type="text/css" href="../Styles/book.css"/></head>
  <body><section epub:type="chapter" id="review-section">
    <h1 id="heading">Review checklist</h1>
    <p id="status" class="status">Draft review status.</p>
    <p id="evidence">Keep the pressure readings with the test record.</p>
  </section></body>
</html>""",
        "EPUB/nav.xhtml": b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en" xml:lang="en">
  <head><title>Contents</title></head>
  <body><nav epub:type="toc" id="toc"><h1>Contents</h1><ol>
    <li><a href="Text/start%20here.xhtml">Start here</a></li>
    <li><a href="Text/review.xhtml">Review checklist</a></li>
  </ol></nav></body>
</html>""",
        "EPUB/Styles/book.css": b"body { font-family: sans-serif; line-height: 1.5; }\nh1 { color: #183153; }\n",
        "EPUB/Images/pump.svg": b"""<svg xmlns="http://www.w3.org/2000/svg" width="240" height="100" viewBox="0 0 240 100"><title>Pump outline</title><rect x="20" y="20" width="200" height="60" rx="10" fill="none" stroke="#183153" stroke-width="3"/></svg>""",
    }


def packed(values):
    output = io.BytesIO()
    with ZipFile(output, "w") as archive:
        for name, data in values.items():
            info = ZipInfo(name, (2026, 9, 25, 0, 0, 0))
            info.compress_type = ZIP_STORED if name == "mimetype" else ZIP_DEFLATED
            info.comment = b"sample"
            archive.writestr(info, data)
    return output.getvalue()


def members(raw):
    with ZipFile(io.BytesIO(raw)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def changed_xml(part, change):
    values = sample_parts()
    root = etree.fromstring(values[part])
    change(root)
    values[part] = etree.tostring(root.getroottree())
    return packed(values)


def test_epub_sample_plan_preserves_navigation_references_styles_media_and_zip_metadata():
    raw = SOURCE.read_bytes()
    plan = json.loads(SOURCE.with_suffix(".plan.json").read_text(encoding="utf-8"))
    assert plan["base"]["sha256"] == sha256(raw)
    document = epub.parse(raw)
    assert epub.serialize(document) == raw
    assert epub.summary(document)["chapter_count"] == 2
    assert epub.inspect(document, "/chapters/intro")["value"]["part"] == "EPUB/Text/start-here.xhtml"
    opf = document.package.xml(OPF_PART)
    manifest = etree.tostring(opf.find("opf:manifest", epub.NS))
    spine = etree.tostring(opf.find("opf:spine", epub.NS))
    for step in plan["steps"]:
        epub.edit(document, step)
    output = epub.serialize(document)
    after = epub.parse(output)
    assert epub.inspect(after, "/metadata")["value"]["title"] == ["Commissioning handbook: reviewed"]
    assert epub.inspect(after, "/metadata")["value"]["modified"] == ["2026-09-26T00:00:00Z"]
    assert epub.inspect(after, "/chapters/review/elements/status")["value"]["text"] == "Ready for review."
    assert etree.tostring(after.package.xml(OPF_PART).find("opf:manifest", epub.NS)) == manifest
    assert etree.tostring(after.package.xml(OPF_PART).find("opf:spine", epub.NS)) == spine
    old_parts, new_parts = members(raw), members(output)
    assert old_parts.keys() == new_parts.keys()
    assert {name for name in old_parts if old_parts[name] != new_parts[name]} == {OPF_PART, REVIEW}
    assert b"<!DOCTYPE html>" in new_parts[REVIEW]
    assert b"<!-- Keep this top-level comment. -->" in new_parts[REVIEW]
    with ZipFile(io.BytesIO(raw)) as old, ZipFile(io.BytesIO(output)) as new:
        assert new.infolist()[0].filename == "mimetype" and new.infolist()[0].compress_type == ZIP_STORED
        for name in old.namelist():
            assert new.getinfo(name).date_time == old.getinfo(name).date_time
            assert new.getinfo(name).comment == old.getinfo(name).comment


def test_epub_leaf_replacement_escapes_markup_and_retains_attributes_and_tail():
    document = epub.parse(packed(sample_parts()))
    old = document.package.xml(REVIEW).xpath("//*[@id='status']")[0]
    tail = old.tail
    epub.edit(
        document,
        {
            "op": "set-text",
            "chapter": "review",
            "id": "status",
            "text": "Reviewed <b>literally</b> & complete",
        },
    )
    output = epub.serialize(document)
    target = epub.parse(output).package.xml(REVIEW).xpath("//*[@id='status']")[0]
    assert target.text == "Reviewed <b>literally</b> & complete"
    assert len(target) == 0 and target.get("class") == "status" and target.tail == tail
    assert b"&lt;b&gt;" in members(output)[REVIEW]
    assert epub.inspect(epub.parse(output), "/metadata")["value"]["modified"] == ["2026-09-25T00:00:00Z"]


@pytest.mark.parametrize(
    "step",
    [
        {"op": "set-text", "chapter": "intro", "id": "links", "text": "Would erase link"},
        {"op": "set-text", "chapter": "intro", "id": "diagram", "text": "Wrong element"},
        {"op": "set-text", "chapter": "review", "id": "missing", "text": "Wrong id"},
        {"op": "set-text", "chapter": "missing", "id": "status", "text": "Wrong chapter"},
        {"op": "set-metadata", "name": "identifier", "value": "new-id"},
        {"op": "set-metadata", "name": "title", "value": ""},
        {"op": "set-metadata", "name": "title", "value": "bad\x00text"},
        {"op": "set-metadata", "name": "creator", "value": "New", "extra": 1},
        {"op": "set-modified", "value": "2026-02-30T00:00:00Z"},
        {"op": "set-modified", "value": "2026-09-26T00:00:00+07:00"},
    ],
)
def test_epub_wrong_or_destructive_operations_are_rejected_without_mutation(step):
    raw = packed(sample_parts())
    document = epub.parse(raw)
    with pytest.raises(FileEditError):
        epub.edit(document, step)
    assert epub.serialize(document) == raw


def test_epub_repeated_metadata_requires_unambiguous_target():
    def duplicate_creator(root):
        metadata = root.find("opf:metadata", epub.NS)
        metadata.append(deepcopy(metadata.find("dc:creator", epub.NS)))

    raw = changed_xml(OPF_PART, duplicate_creator)
    document = epub.parse(raw)
    assert len(epub.inspect(document, "/metadata")["value"]["creator"]) == 2
    with pytest.raises(FileEditError, match="one existing"):
        epub.edit(document, {"op": "set-metadata", "name": "creator", "value": "New"})
    assert epub.serialize(document) == raw


def test_epub_relative_uri_resolution_uses_package_directory_and_utf8_percent_decoding():
    values = sample_parts()
    root = etree.fromstring(values.pop(OPF_PART))
    for item in root.findall("opf:manifest/opf:item", epub.NS):
        item.set("href", "../" + item.get("href"))
    values["EPUB/Meta/package.opf"] = etree.tostring(root)
    container = etree.fromstring(values["META-INF/container.xml"])
    container.find("ocf:rootfiles/ocf:rootfile", epub.NS).set("full-path", "EPUB/Meta/package.opf")
    values["META-INF/container.xml"] = etree.tostring(container)
    document = epub.parse(packed(values))
    assert document.manifest["intro"].part == INTRO
    assert document.manifest["review"].part == REVIEW


@pytest.mark.parametrize(
    "href", ["../../outside.xhtml", "Text/missing.xhtml", "/EPUB/Text/review.xhtml", "Text/bad%ZZ.xhtml"]
)
def test_epub_bad_manifest_references_rejected(href):
    raw = changed_xml(OPF_PART, lambda root: root.find("opf:manifest/opf:item", epub.NS).set("href", href))
    with pytest.raises(FileEditError):
        epub.parse(raw)


def test_epub_duplicate_ids_fixed_layout_and_encryption_are_rejected():
    raw = changed_xml(REVIEW, lambda root: root.xpath("//*[@id='status']")[0].set("id", "heading"))
    with pytest.raises(FileEditError, match="unique"):
        epub.parse(raw)
    raw = changed_xml(
        OPF_PART,
        lambda root: setattr(
            root.xpath("opf:metadata/opf:meta[@property='rendition:layout']", namespaces=epub.NS)[0],
            "text",
            "pre-paginated",
        ),
    )
    with pytest.raises(FileEditError, match="Fixed-layout"):
        epub.parse(raw)
    values = sample_parts()
    values["META-INF/encryption.xml"] = b"<encryption/>"
    with pytest.raises(FileEditError, match="Encrypted"):
        epub.parse(packed(values))


def test_epub_projection_includes_spine_order_and_all_editable_text():
    document = epub.parse(packed(sample_parts()))
    projection = epub.projection(document)
    assert projection["spine"] == ["intro", "review"]
    target = next(node for node in projection["chapters"][1]["elements"] if node["id"] == "status")
    assert target["text"] == "Draft review status." and target["editable_text"]
    assert not next(node for node in projection["chapters"][0]["elements"] if node["id"] == "links")[
        "editable_text"
    ]


def test_epub_navigation_in_spine_stays_read_only():
    values = sample_parts()
    opf = etree.fromstring(values[OPF_PART])
    etree.SubElement(opf.find("opf:spine", epub.NS), f"{{{epub.OPF}}}itemref", idref="nav")
    values[OPF_PART] = etree.tostring(opf)
    nav = etree.fromstring(values["EPUB/nav.xhtml"])
    nav.find(".//h:h1", epub.NS).set("id", "nav-title")
    values["EPUB/nav.xhtml"] = etree.tostring(nav.getroottree())
    raw = packed(values)
    document = epub.parse(raw)
    assert not epub.inspect(document, "/chapters/nav/elements/nav-title")["value"]["editable_text"]
    with pytest.raises(FileEditError, match="non-navigation"):
        epub.edit(document, {"op": "set-text", "chapter": "nav", "id": "nav-title", "text": "Changed"})
    assert epub.serialize(document) == raw
