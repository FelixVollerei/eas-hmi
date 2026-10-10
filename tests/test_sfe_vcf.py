from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from sfe_files import vcf_profile as vcf
from sfe_files.core import FileEditError


def _card(extra="", *, version="4.0", uid="contact-01", newline="\r\n"):
    lines = ["BEGIN:VCARD", "VERSION:" + version]
    if uid is not None:
        lines.append("UID;VALUE=text:" + uid if version == "4.0" else "UID:" + uid)
    lines += ["FN:Test Contact", "N:Contact;Test;;;", *extra.splitlines(), "END:VCARD"]
    return (newline.join(lines) + newline).encode("utf-8")


def _properties(doc, uid="contact-01"):
    return vcf.inspect(doc, "/contacts/" + uid.replace("~", "~0").replace("/", "~1"))["properties"]


def test_noop_preserves_folded_unknown_photo_bom_and_multiple_records():
    original = b"\xef\xbb\xbf" + _card(
        "PHOTO:data:image/png;base64,ABC123\n DEF456\nX-EXT;X-LABEL=\"one;two:three\":opaque"
    ) + b"\r\n" + _card("PHOTO;ENCODING=b;TYPE=PNG:ABC123\n DEF456", version="3.0", uid="other")
    doc = vcf.parse(original)
    assert vcf.summary(doc)["contact_count"] == 2
    assert vcf.serialize(doc) == original
    assert next(p for p in _properties(doc) if p["name"] == "PHOTO")["editable"] is False


def test_repeated_property_edit_targets_ordinal_retains_group_params_and_other_bytes():
    original = _card('item1.EMAIL;TYPE=work:first@example.com\nitem2.email;TYPE="home":second@example.com\n'
                     'PHOTO;VALUE=uri:data:image/png;base64,ABC\n DEF\nREV:20260926T000000Z')
    doc = vcf.parse(original)
    vcf.edit(doc, {"op": "set-property", "uid": "contact-01", "name": "EMAIL", "index": 2,
                   "value": "new@example.com"})
    expected = original.replace(b"second@example.com", b"new@example.com")
    assert vcf.serialize(doc) == expected
    emails = [p for p in _properties(doc) if p["name"] == "EMAIL"]
    assert emails[0]["value"] == "first@example.com"
    assert emails[1]["index"] == 2 and emails[1]["group"] == "item2"
    assert emails[1]["params"] == ';TYPE="home"'


def test_text_escaping_and_multibyte_folding_roundtrip():
    text = "调试完成" * 30 + "\nLine 2, with; separators \\ end"
    doc = vcf.parse(_card("NOTE:old"))
    vcf.edit(doc, {"op": "set-property", "uid": "contact-01", "name": "NOTE", "index": 1, "value": text})
    data = vcf.serialize(doc)
    assert b"\r\n " in data
    assert max(map(len, data.split(b"\r\n"))) <= 75
    assert next(p for p in _properties(vcf.parse(data)) if p["name"] == "NOTE")["value"] == text


def test_org_array_preserves_literal_delimiters_in_components():
    doc = vcf.parse(_card("ORG:Old;Department"))
    value = ["Company; West", "Engineering, R&D", "Site \\ A"]
    vcf.edit(doc, {"op": "set-property", "uid": "contact-01", "name": "ORG", "index": 1, "value": value})
    assert next(p for p in _properties(vcf.parse(vcf.serialize(doc))) if p["name"] == "ORG")["value"] == value
    with pytest.raises(FileEditError, match="array"):
        vcf.edit(doc, {"op": "set-property", "uid": "contact-01", "name": "ORG", "index": 1, "value": "flat"})


def test_version4_tel_defaults_text_but_explicit_uri_is_validated_and_not_escaped():
    doc = vcf.parse(_card("TEL;TYPE=cell:+66 (81) 555 0110\nTEL;VALUE=uri;TYPE=work:tel:+66-2-555-0100"))
    vcf.edit(doc, {"op": "set-property", "uid": "contact-01", "name": "TEL", "index": 1,
                   "value": "+66 (81) 555 0111"})
    vcf.edit(doc, {"op": "set-property", "uid": "contact-01", "name": "TEL", "index": 2,
                   "value": "tel:+66-2-555-0100;ext=9"})
    assert b"tel:+66-2-555-0100;ext=9" in vcf.serialize(doc)
    assert b"0100\\;ext" not in vcf.serialize(doc)
    with pytest.raises(FileEditError, match="URI"):
        vcf.edit(doc, {"op": "set-property", "uid": "contact-01", "name": "TEL", "index": 2,
                       "value": "+66-2-555-0100"})


@pytest.mark.parametrize("value", ["tel:😀", "tel:+66 123", "tel:+66%ZZ", "tel:", "tel:+66\\;ext=1"])
def test_uri_rejects_non_ascii_spaces_malformed_escapes_and_text_escaping(value):
    original = _card("TEL;VALUE=uri:tel:+66-2-555-0100")
    doc = vcf.parse(original)
    with pytest.raises(FileEditError) as error:
        vcf.edit(doc, {"op": "set-property", "uid": "contact-01", "name": "TEL", "index": 1,
                       "value": value})
    assert error.value.code == "INVALID_URI"
    assert vcf.serialize(doc) == original


def test_vcard3_phone_number_and_base64_photo_preserved():
    data = _card("TEL;TYPE=WORK,VOICE:+66-2-555-0100\nPHOTO;ENCODING=b;TYPE=PNG:ABC123\n DEF456", version="3.0")
    doc = vcf.parse(data)
    vcf.edit(doc, {"op": "set-property", "uid": "contact-01", "name": "TEL", "index": 1,
                   "value": "+66-2-555-0101"})
    assert vcf.serialize(doc) == data.replace(b"0100", b"0101")


def test_add_property_keeps_contact_boundaries_and_updates_later_offsets():
    original = _card() + _card(uid="second")
    doc = vcf.parse(original)
    vcf.edit(doc, {"op": "add-property", "uid": "contact-01", "name": "TEL", "group": "item3",
                   "params": ";VALUE=uri;TYPE=work", "value": "tel:+66-2-555-0100"})
    vcf.edit(doc, {"op": "add-property", "uid": "second", "name": "EMAIL", "value": "second@example.com"})
    result = vcf.parse(vcf.serialize(doc))
    assert next(p for p in _properties(result) if p["name"] == "TEL")["group"] == "item3"
    assert next(p for p in _properties(result, "second") if p["name"] == "EMAIL")["value"] == "second@example.com"


def test_uid_pointer_escaping_and_uidless_record_inspection():
    doc = vcf.parse(_card(uid="a/b~c") + _card(uid=None))
    assert _properties(doc, "a/b~c")
    assert vcf.inspect(doc, "/records/2")["uid"] is None
    assert vcf.summary(doc)["contacts"][1]["editable"] is False


def test_lf_and_missing_terminal_newline_are_preserved():
    original = _card("NOTE:old", newline="\n").rstrip(b"\n")
    doc = vcf.parse(original)
    vcf.edit(doc, {"op": "set-property", "uid": "contact-01", "name": "NOTE", "index": 1,
                   "value": "new\\two"})
    result = vcf.serialize(doc)
    assert not result.endswith((b"\n", b"\r"))
    assert b"\r" not in result
    assert next(p for p in _properties(vcf.parse(result)) if p["name"] == "NOTE")["value"] == "new\\two"


@pytest.mark.parametrize("extra,version", [
    ("NOTE;ENCODING=QUOTED-PRINTABLE:hello", "3.0"),
    ("FN;CHARSET=ISO-8859-1:hello", "3.0"),
    ("PHOTO;ENCODING=b:ABC", "4.0"),
    ("", "2.1"),
    ("VERSION:4.0", "4.0"),
    ("UID:duplicate", "4.0"),
])
def test_unsupported_or_ambiguous_records_rejected(extra, version):
    with pytest.raises(FileEditError):
        vcf.parse(_card(extra, version=version))


def test_duplicate_uid_across_records_is_rejected():
    with pytest.raises(FileEditError) as error:
        vcf.parse(_card() + _card())
    assert error.value.code == "DUPLICATE_UID"


@pytest.mark.parametrize("name", ["N", "ADR", "PHOTO", "UID", "REV", "X-EXT"])
def test_unsafe_structured_and_identity_properties_are_read_only(name):
    original = _card()
    doc = vcf.parse(original)
    with pytest.raises(FileEditError):
        vcf.edit(doc, {"op": "add-property", "uid": "contact-01", "name": name, "value": "changed"})
    assert vcf.serialize(doc) == original


@pytest.mark.parametrize("index", [0, -1, 2, True, "1"])
def test_invalid_occurrence_is_rejected_without_mutation(index):
    original = _card("EMAIL:test@example.com")
    doc = vcf.parse(original)
    with pytest.raises(FileEditError):
        vcf.edit(doc, {"op": "set-property", "uid": "contact-01", "name": "EMAIL", "index": index, "value": "new@example.com"})
    assert vcf.serialize(doc) == original


@pytest.mark.parametrize("params", ["TYPE=work", ";VALUE=uri;VALUE=text", ";TYPE=work:injection", ';TYPE="unclosed'])
def test_invalid_new_parameters_rejected_without_mutation(params):
    original = _card()
    doc = vcf.parse(original)
    with pytest.raises(FileEditError):
        vcf.edit(doc, {"op": "add-property", "uid": "contact-01", "name": "TEL", "params": params,
                       "value": "tel:+66-2-555-0100"})
    assert vcf.serialize(doc) == original


def test_example_fixed_hash_and_plan():
    directory = Path(__file__).resolve().parents[1] / "examples" / "sfe"
    data = (directory / "contacts.vcf").read_bytes()
    plan = json.loads((directory / "contacts.plan.json").read_text(encoding="utf-8"))
    assert plan["base"]["sha256"] == hashlib.sha256(data).hexdigest()
    doc = vcf.parse(data)
    for step in plan["steps"]:
        vcf.edit(doc, step)
    uid = "urn:uuid:5b30ba4a-8bc5-4a90-a37e-96c34dbaeff1"
    props = _properties(vcf.parse(vcf.serialize(doc)), uid)
    assert [p for p in props if p["name"] == "EMAIL"][1]["value"] == "li.support@example.com"
    assert next(p for p in props if p["name"] == "ORG")["value"] == ["EAS", "Field Service"]
    assert [p for p in props if p["name"] == "TEL"][1]["value"] == "tel:+66-2-555-0100"
