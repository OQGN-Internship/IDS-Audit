"""Telling a file's format from its first bytes (ENG-02 4.1). No database."""

import codecs
from pathlib import Path

import pytest

from ids_audit.pipeline.formats import (
    OLE2_SIGNATURE,
    SNIFF_BYTES,
    TEXT,
    XLS,
    XLSX,
    UnknownFormat,
    detect_format,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_XLSX = (FIXTURES_DIR / "sample.xlsx").read_bytes()
SAMPLE_CSV = (FIXTURES_DIR / "sample_semicolon.csv").read_bytes()
# The sample CSV as characters, without its byte-order mark. Row 10 is Arabic.
SAMPLE_TEXT = SAMPLE_CSV.decode("utf-8-sig")

# A short CSV for the encodings, and 200 lines that fill more than SNIFF_BYTES.
CSV_TEXT = "CoCd;G/L Acct;Amount in LC\r\n1000;0000610100;12,400.000\r\n"
FILLER = "1000;0000610100;12,400.000\r\n" * 200


# The accepted formats


def test_the_sample_workbook_is_xlsx() -> None:
    """Verify sample.xlsx, a ZIP file, is detected as .xlsx."""
    assert detect_format(SAMPLE_XLSX) == XLSX


def test_the_old_excel_mark_is_xls() -> None:
    """Verify bytes starting with the 8-byte .xls mark are detected as .xls."""
    assert detect_format(OLE2_SIGNATURE + bytes(504)) == XLS


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(SAMPLE_CSV, id="utf-8 with its mark"),
        pytest.param(SAMPLE_TEXT.encode("utf-8"), id="utf-8 without a mark"),
        pytest.param(
            codecs.BOM_UTF16_LE + CSV_TEXT.encode("utf-16-le"), id="utf-16 le"
        ),
        pytest.param(
            codecs.BOM_UTF16_BE + CSV_TEXT.encode("utf-16-be"), id="utf-16 be"
        ),
    ],
)
def test_utf8_and_utf16_with_its_mark_are_text(data: bytes) -> None:
    """Verify UTF-8, with or without a byte-order mark, and UTF-16 with its
    mark, are detected as text.
    """
    assert detect_format(data) == TEXT


def test_text_starting_with_pk_is_not_excel() -> None:
    """Verify only the full ZIP signature means .xlsx, not the letters PK."""
    assert detect_format(b"PKG;Amount\r\n1;2\r\n") == TEXT


def test_a_letter_cut_at_the_end_of_the_check_is_still_text() -> None:
    """Verify a two-byte Arabic letter split by the SNIFF_BYTES cut doesn't
    make the file unreadable.
    """
    data = ("a" * (SNIFF_BYTES - 1) + "ت\r\n").encode("utf-8")
    assert data[SNIFF_BYTES - 1 : SNIFF_BYTES + 1] == "ت".encode()
    assert detect_format(data) == TEXT


def test_cp1256_with_arabic_only_after_the_check_passes_as_text() -> None:
    """Verify Arabic in cp1256 beyond the first SNIFF_BYTES isn't seen here.
    Reading it fails later, with the same advice (ENG-02 4.1).
    """
    data = (CSV_TEXT + FILLER + "1000;0000610100;ت\r\n").encode("cp1256")
    assert data.find("ت".encode("cp1256")) > SNIFF_BYTES
    assert detect_format(data) == TEXT


# Refused as an unknown file type


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(b"<html><body><table>", id="html"),
        pytest.param(b"\xef\xbb\xbf  \r\n<!DOCTYPE html>", id="html after a mark"),
        pytest.param(b'<?xml version="1.0"?>', id="xml"),
        pytest.param(b"MIME-Version: 1.0\r\nX-Document-Type: Workbook", id="mhtml"),
        pytest.param(b"%PDF-1.4\n1 0 obj\n", id="pdf"),
    ],
)
def test_html_xml_mhtml_and_pdf_are_refused(data: bytes) -> None:
    """Verify files that start like HTML, XML, MHTML or PDF are refused, even
    after a byte-order mark and spaces.
    """
    with pytest.raises(UnknownFormat, match="HTML, XML, MHTML or PDF"):
        detect_format(data)


def test_cp1256_with_arabic_in_the_check_is_refused_with_advice() -> None:
    """Verify the sample CSV saved in cp1256 is refused, with the message that
    says to save it as CSV UTF-8.
    """
    with pytest.raises(UnknownFormat, match="Save it as CSV UTF-8"):
        detect_format(SAMPLE_TEXT.encode("cp1256"))


@pytest.mark.parametrize(
    "data",
    [
        pytest.param("Café;Total\r\n".encode("latin-1"), id="latin-1"),
        pytest.param(CSV_TEXT.encode("utf-16-le"), id="utf-16 without its mark"),
    ],
)
def test_text_in_other_encodings_is_refused(data: bytes) -> None:
    """Verify bytes that don't decode as UTF-8, such as Latin-1's é, or that
    decode to control characters, such as the zero bytes of UTF-16 without its
    mark, are refused.
    """
    with pytest.raises(UnknownFormat, match="isn't Excel, or text"):
        detect_format(data)


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(b"", id="no bytes"),
        pytest.param(b"  \r\n", id="spaces"),
        pytest.param(codecs.BOM_UTF8, id="only a mark"),
    ],
)
def test_an_empty_file_is_refused(data: bytes) -> None:
    """Verify a file with nothing but spaces or a byte-order mark is refused."""
    with pytest.raises(UnknownFormat, match="empty"):
        detect_format(data)
