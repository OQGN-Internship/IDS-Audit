"""Small files for tests to read, as CSV text or as a workbook, and reading
them with the default settings changed.
"""

import io

import xlsxwriter

from ids_audit.pipeline.read import read_file
from ids_audit.pipeline.results import ReadResult
from ids_audit.pipeline.ruleset import DEFAULT_SETTINGS

# A spend sheet laid out like the sample's (ENG-02 12.3), with fewer columns.
HEADER = (
    "CoCd",
    "G/L Acct",
    "DocumentNo",
    "Line Item",
    "Pstng Date",
    "Year",
    "Cost Ctr",
    "Text",
    "Amount in LC",
    "LCurr",
)
COLUMN = {name: index for index, name in enumerate(HEADER)}
LINE = (
    "1000",
    "0000610100",
    "5100000101",
    "002",
    "08.07.2025",
    "2025",
    "0000004520",
    "ERP licence renewal",
    "1,000.000",
    "OMR",
)


def line(**changes: str) -> tuple:
    """Return LINE with some cells changed, named by header: line(LCurr="USD")."""
    cells = list(LINE)
    for name, value in changes.items():
        cells[COLUMN[name.replace("_", " ")]] = value
    return tuple(cells)


def csv_file(*rows: tuple, header: tuple = HEADER) -> bytes:
    """Return a semicolon-separated UTF-8 file with the header on row 1."""
    lines = [";".join(map(str, row)) for row in (header, *rows)]
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def xlsx_file(sheets: dict[str, list[tuple]]) -> bytes:
    """Return a workbook with one sheet per entry, row 1 first. Numbers are
    written as number cells, text as text, and None leaves a cell empty.
    """
    buffer = io.BytesIO()
    workbook = xlsxwriter.Workbook(buffer, {"in_memory": True})
    for name, rows in sheets.items():
        worksheet = workbook.add_worksheet(name)
        for row_index, row in enumerate(rows):
            for column, value in enumerate(row):
                if value is not None:
                    worksheet.write(row_index, column, value)
    workbook.close()
    return buffer.getvalue()


def read(data: bytes, **settings) -> ReadResult:
    """Read a file with the default settings, changed by settings."""
    return read_file(data, {**DEFAULT_SETTINGS, **settings})
