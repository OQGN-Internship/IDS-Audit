"""Telling a file's format from its first bytes, never its name (ENG-02 4.1).

Plain Python, like the rest of pipeline/: it imports nothing from Django.
"""

import codecs
import unicodedata

# The formats detect_format() returns.
XLSX = "xlsx"
XLS = "xls"
TEXT = "text"

# Every ZIP file, and so every .xlsx, starts with "PK" and the start of its
# first entry. The old .xls format starts with the Microsoft container's mark.
ZIP_SIGNATURE = b"PK\x03\x04"
OLE2_SIGNATURE = bytes.fromhex("D0 CF 11 E0 A1 B1 1A E1")

# UTF-16 needs its byte-order mark, either way round. UTF-8 may have one.
UTF16_MARKS = (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)

# How much of a file the check reads to decide that it's text.
SNIFF_BYTES = 4096

# The only control characters text may hold.
TEXT_CONTROLS = {"\t", "\n", "\r"}

# HTML, XML, MHTML and PDF start like this, after any byte-order mark and
# spaces. They can look like text, but they're never a table.
NOT_A_TABLE = ("<", "mime-version", "%pdf")

# What the user is told when text isn't UTF-8 or UTF-16.
UTF8_ADVICE = (
    "Save it as CSV UTF-8 in Excel, or choose UTF-8 when downloading from SAP, "
    "and upload it again."
)


class UnknownFormat(ValueError):
    """The file isn't a format ENG-02 4.1 accepts, so it's refused."""


def text_encoding(data: bytes) -> str:
    """Return the encoding to read text in: UTF-16 if it has that byte-order
    mark, and UTF-8 otherwise.

    Python's "utf-16" reads the mark to learn which way round the bytes go, and
    "utf-8-sig" drops a UTF-8 mark if there is one. Neither keeps the mark.
    """
    return "utf-16" if data.startswith(UTF16_MARKS) else "utf-8-sig"


def detect_format(data: bytes) -> str:
    """Return XLSX, XLS or TEXT for the file's bytes, or raise UnknownFormat."""
    if data.startswith(ZIP_SIGNATURE):
        return XLSX
    if data.startswith(OLE2_SIGNATURE):
        return XLS

    decoder = codecs.getincrementaldecoder(text_encoding(data))()
    try:
        # final=False lets the cut at SNIFF_BYTES fall inside a letter that
        # takes several bytes, such as an Arabic one in UTF-8. A file shorter
        # than that is decoded to its end.
        start = decoder.decode(data[:SNIFF_BYTES], final=len(data) <= SNIFF_BYTES)
    except UnicodeDecodeError:
        raise UnknownFormat(
            f"The file isn't Excel, or text in UTF-8 or UTF-16. {UTF8_ADVICE}"
        ) from None

    if not start.strip():
        raise UnknownFormat("The file is empty.")
    if start.lstrip().lower().startswith(NOT_A_TABLE):
        raise UnknownFormat(
            "The file is HTML, XML, MHTML or PDF. Upload the export as Excel or CSV."
        )
    if any(
        unicodedata.category(char) == "Cc" and char not in TEXT_CONTROLS
        for char in start
    ):
        raise UnknownFormat(
            f"The file isn't Excel, or text in UTF-8 or UTF-16. {UTF8_ADVICE}"
        )
    return TEXT
