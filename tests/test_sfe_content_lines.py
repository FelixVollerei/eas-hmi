import pytest

from sfe_files.content_lines import decode_text, encode_text, property_line, replace_value, split
from sfe_files.core import FileEditError


def test_quoted_parameter_colon_folding_and_raw_preservation():
    raw = b'\xef\xbb\xbfBEGIN:VCARD\r\nitem1.EMAIL;TYPE="work:office":first@exa\r\n mple.test\r\n\r\nEND:VCARD'
    doc = split(raw)
    email = doc.lines[1]
    assert email.group == "item1" and email.name == "EMAIL"
    assert email.params == ';TYPE="work:office"' and email.value == "first@example.test"
    assert doc.lines[2].name == ""
    assert doc.serialize() == raw
    doc.lines[1] = replace_value(email, "new@example.test", doc.newline)
    assert doc.serialize().startswith(b'\xef\xbb\xbfBEGIN:VCARD\r\nitem1.EMAIL;TYPE="work:office":new@')
    assert doc.serialize().endswith(b"\r\n\r\nEND:VCARD")


def test_utf8_folding_obeys_75_octets_and_is_reversible():
    value = "施工检查表" * 30 + "\nNext;section,one\\two"
    line = property_line("DESCRIPTION", encode_text(value))
    assert all(len(part.encode()) <= 75 for part in line.raw.splitlines())
    assert decode_text(split(line.raw.encode()).lines[0].value) == value


def test_literal_backslash_n_and_unicode_line_separator():
    value = "literal \\n vs newline\n and \u2028"
    assert decode_text(encode_text(value)) == value
    assert split(property_line("NOTE", encode_text(value)).raw.encode()).lines[0].value == encode_text(value)


@pytest.mark.parametrize("data", [b" folded", b"BEGIN:VCARD\n\n folded", b"NOTE-no-colon", b".FN:a", b"N\x00:bad", b"\xff"])
def test_invalid_content_lines(data):
    with pytest.raises(FileEditError):
        split(data)


def test_property_value_cannot_inject_a_second_line():
    with pytest.raises(FileEditError):
        property_line("FN", "Person\r\nTEL:123")
    with pytest.raises(FileEditError):
        decode_text("bad\\q")


@pytest.mark.parametrize("value", ["a\x00b", "a\x7fb"])
def test_writer_rejects_control_characters_that_reader_rejects(value):
    with pytest.raises(FileEditError):
        property_line("FN", value)
    with pytest.raises(FileEditError):
        replace_value(property_line("FN", "valid"), value, "\r\n")
