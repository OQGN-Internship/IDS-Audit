"""Reading a file's bytes into sheets of cells (ENG-02 4.1).

A reader decides nothing: no header, no cleaning. It gives every row as the
file stores it, from row 1, so a row's number is the one Excel shows (ENG-02 2).

Plain Python, like the rest of pipeline/: it imports nothing from Django.
"""

import csv
import datetime
import io
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

import openpyxl
from python_calamine import CalamineWorkbook, SheetTypeEnum

from ids_audit.pipeline.formats import TEXT, UTF8_ADVICE, XLS, text_encoding

# A cell as the readers give it. An empty cell is always "" (ENG-02 4.1).
type Cell = (
    str
    | int
    | float
    | bool
    | datetime.date
    | datetime.datetime
    | datetime.time
    | datetime.timedelta
)

# The separators delimited text may use, and how much of it they're tried on
# (ENG-02 4.1).
SEPARATORS = ";,\t"
SEPARATOR_CHARS = 4096


class FileUnreadable(Exception):
    """The file can't be read in the format its first bytes showed, so its
    upload ends at failed (ENG-02 6).
    """


@dataclass(frozen=True)
class Sheet:
    """One sheet's cells. rows[0] is row 1, and every row is the same width.

    A delimited text file is one sheet, named "" (ENG-02 2).
    """

    name: str
    rows: tuple[tuple[Cell, ...], ...]


def read_sheets(data: bytes, file_format: str) -> list[Sheet]:
    """Return every sheet in the file, read as file_format (formats.py), or
    raise FileUnreadable.
    """
    if file_format == TEXT:
        return [read_text(data)]
    try:
        return read_calamine(data)
    except Exception as error:
        # openpyxl reads only .xlsx, so an .xls calamine can't read is the end.
        if file_format == XLS:
            raise FileUnreadable("The file couldn't be opened as Excel.") from error
    # The fallback for an .xlsx calamine can't read (ENG-02 4.1).
    try:
        return read_openpyxl(data)
    except Exception as error:
        raise FileUnreadable("The file couldn't be opened as Excel.") from error


def read_calamine(data: bytes) -> list[Sheet]:
    """Read an .xlsx or .xls with python-calamine.

    calamine gives an empty cell as "", a number as a float, and a formula as
    the result saved with it. An error such as #N/A comes back as "" too,
    since calamine can't tell it from an empty cell (ENG-02 4.1).
    """
    workbook = CalamineWorkbook.from_filelike(io.BytesIO(data))
    sheets = []
    for sheet in workbook.sheets_metadata:
        if sheet.typ != SheetTypeEnum.WorkSheet:
            continue  # a chart sheet holds no cells
        # skip_empty_area=False starts at cell A1. By default calamine drops
        # blank rows above the first value, and the row numbers move up.
        rows = workbook.get_sheet_by_name(sheet.name).to_python(skip_empty_area=False)
        sheets.append(tidy(sheet.name, rows))
    return sheets


def read_openpyxl(data: bytes) -> list[Sheet]:
    """Read an .xlsx with openpyxl, made to give what calamine gives.

    data_only=True reads a formula's saved result, not its text. An error cell,
    whose data type is "e", becomes "", as it is in calamine (ENG-02 4.1).
    """
    workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        sheets = []
        for worksheet in workbook.worksheets:
            # Some programs save a wrong size for the sheet. Forgetting it makes
            # openpyxl read every row and column there is.
            worksheet.reset_dimensions()
            rows = [
                ["" if cell.data_type == "e" else cell.value for cell in row]
                for row in worksheet.iter_rows()
            ]
            sheets.append(tidy(worksheet.title, rows))
        return sheets
    finally:
        workbook.close()


def read_text(data: bytes) -> Sheet:
    """Read delimited text, in UTF-8 or UTF-16 (ENG-02 4.1). Every cell is a
    string.
    """
    try:
        text = data.decode(text_encoding(data))
    except UnicodeDecodeError:
        raise FileUnreadable(
            f"The file isn't valid UTF-8 or UTF-16 text. {UTF8_ADVICE}"
        ) from None
    separator = find_separator(text[:SEPARATOR_CHARS])
    return tidy("", split_lines(text, separator))


def find_separator(start: str) -> str:
    """Return the separator that splits the most lines of start into the same
    number of cells, more than one (ENG-02 4.1). If two split as many lines, the
    one giving more cells wins. If none splits a line, return ",".
    """
    best, best_split = ",", (0, 0)
    for separator in SEPARATORS:
        # How many lines have each number of cells, such as {14: 30, 1: 2}.
        widths = Counter(len(row) for row in split_lines(start, separator))
        # (lines, cells) for this separator's most common width above one.
        split = max(
            ((lines, cells) for cells, lines in widths.items() if cells > 1),
            default=(0, 0),
        )
        if split > best_split:
            best, best_split = separator, split
    return best


def split_lines(text: str, separator: str) -> Iterable[list[str]]:
    """Split text into rows of cells, keeping each quoted cell whole."""
    # newline="" leaves the line endings to the csv module, which knows a line
    # ending inside quotes belongs to the cell.
    return csv.reader(io.StringIO(text, newline=""), delimiter=separator)


def tidy(name: str, rows: Iterable[Iterable[Cell | None]]) -> Sheet:
    """Make a Sheet from a reader's rows: None becomes "", blank rows after the
    last value are dropped, and every row is cut or padded to the width of the
    last column holding a value.
    """
    cells = [["" if cell is None else cell for cell in row] for row in rows]
    while cells and all(cell == "" for cell in cells[-1]):
        cells.pop()
    width = max((filled_width(row) for row in cells), default=0)
    return Sheet(
        name, tuple(tuple(row[:width]) + ("",) * (width - len(row)) for row in cells)
    )


def filled_width(row: list[Cell]) -> int:
    """Return how many cells the row holds up to its last one that isn't ""."""
    for width in range(len(row), 0, -1):
        if row[width - 1] != "":
            return width
    return 0
