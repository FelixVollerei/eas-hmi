import io
import warnings
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

import pytest

from sfe_files.core import FileEditError
from sfe_files.zip_document import ODF_MIMES, ZipDocument, open_odf

MIME = ODF_MIMES["odt"]
CONTENT = b'<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" office:version="1.3"><office:body><office:text/></office:body></office:document-content>'
MANIFEST = ('<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.3">'
            f'<manifest:file-entry manifest:full-path="/" manifest:media-type="{MIME}"/>'
            '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
            '</manifest:manifest>').encode()


def package(*, mime=MIME, compressed=False, extra=b"", duplicate=False, content=CONTENT, manifest=MANIFEST):
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        info = ZipInfo("mimetype", date_time=(2025, 1, 1, 0, 0, 0))
        info.compress_type = ZIP_DEFLATED if compressed else ZIP_STORED
        info.extra = extra
        archive.writestr(info, mime)
        archive.writestr("content.xml", content)
        archive.writestr("META-INF/manifest.xml", manifest)
        archive.writestr("Pictures/icon.bin", bytes(range(256)))
        if duplicate:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                archive.writestr("content.xml", content)
        archive.comment = b"Keep this archive comment"
    return buffer.getvalue()


def test_noop_and_edit_preserve_payload_metadata_and_mimetype_layout():
    original = package()
    doc = open_odf(original, "odt")
    assert doc.serialize() == original
    doc.xml("content.xml").set("test", "changed")
    doc.mark_changed("content.xml")
    result = doc.serialize()
    with ZipFile(io.BytesIO(original)) as before, ZipFile(io.BytesIO(result)) as after:
        assert before.comment == after.comment
        assert after.infolist()[0].filename == "mimetype" and after.infolist()[0].compress_type == ZIP_STORED
        assert after.infolist()[0].extra == b""
        for name in before.namelist():
            if name != "content.xml":
                assert before.read(name) == after.read(name)
            assert before.getinfo(name).date_time == after.getinfo(name).date_time


@pytest.mark.parametrize("kwargs", [
    {"compressed": True}, {"mime": "application/wrong"}, {"duplicate": True},
    {"extra": b"\x99\x99\x00\x00"}, {"manifest": MANIFEST.replace(b"text/xml", b"wrong/type")},
    {"content": CONTENT.replace(b'office:version="1.3"', b'office:version="1.1"')},
    {"manifest": MANIFEST.replace(b"</manifest:manifest>", b"<manifest:encryption-data/></manifest:manifest>")},
])
def test_incompatible_packages_rejected(kwargs):
    with pytest.raises(FileEditError):
        open_odf(package(**kwargs), "odt")


def test_xhtml_plain_doctype_preserved_after_edit():
    data = package(content=b'<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml"><body><p>Hello</p></body></html>')
    doc = ZipDocument.open(data, MIME)
    doc.xml("content.xml").find(".//{http://www.w3.org/1999/xhtml}p").text = "Edited"
    doc.mark_changed("content.xml")
    with ZipFile(io.BytesIO(doc.serialize())) as archive:
        assert b"<!DOCTYPE html>" in archive.read("content.xml")


@pytest.mark.parametrize("xml", [
    b'<!DOCTYPE html SYSTEM "https://example.test/external.dtd"><html/>',
    b'<!DOCTYPE html [<!ENTITY x "replaced">]><html>&x;</html>',
    b'<!DOCTYPE office><office/>',
])
def test_external_or_internal_dtd_rejected(xml):
    doc = ZipDocument.open(package(content=xml), MIME)
    with pytest.raises(FileEditError) as caught:
        doc.xml("content.xml")
    assert caught.value.code == "INVALID_DOCUMENT_XML"
