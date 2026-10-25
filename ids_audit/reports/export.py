"""The Excel export of a screen (ENG-03 9): one data sheet per table on the
screen, in its order, and a Source sheet saying where the figures came from.

The tables are the ones reports/screens.py built for the screen, so the
export has its rows, order and totals. XlsxWriter writes every string as
text, never as a formula, link or number (ENG-01 6.4), and writes rows as
they come, so a large export's memory use stays flat.
"""

import datetime
import re
from decimal import Decimal
from typing import IO

import xlsxwriter
from django.conf import settings
from django.utils import timezone
from xlsxwriter.worksheet import Worksheet

from ids_audit.reports import filters as addresses
from ids_audit.reports import queries
from ids_audit.reports.models import Report
from ids_audit.reports.queries import Filters
from ids_audit.reports.screens import Cell, Screen, Table
from ids_audit.reports.templatetags.figures import moment

# Excel's last row is 1,048,576; the header and total rows take two.
MAX_DATA_ROWS = 1_048_574

OPTIONS = {
    "strings_to_formulas": False,
    "strings_to_urls": False,
    "strings_to_numbers": False,
    "constant_memory": True,
}

AMOUNT = "#,##0.000;-#,##0.000"
EXACT = "#,##0.000###;-#,##0.000###"

# Each kind's column width, in characters.
WIDTHS = {
    "code": 14,
    "text": 28,
    "item": 40,
    "amount": 18,
    "exact": 18,
    "share": 9,
    "count": 10,
    "date": 12,
    "month": 11,
}


def file_name(report: Report, view: str, at: datetime.datetime) -> str:
    """Return <report>_<view>_<yyyymmdd-hhmm>.xlsx, in Muscat time, keeping
    only A to Z, a to z, digits, hyphens and underscores (ENG-03 9.4).
    """

    def clean(text: str) -> str:
        return re.sub(r"[^A-Za-z0-9_-]", "", text)

    name = clean(report.name) or f"report{report.pk}"
    stamp = f"{timezone.localtime(at):%Y%m%d-%H%M}"
    return f"{name}_{clean(view.replace('/', '-'))}_{stamp}.xlsx"


def largest_sheet(screen: Screen) -> int:
    """Return the most data rows any of the screen's sheets would hold. Only
    source rows come as an iterator; their number is the screen's lines.
    """
    return max(
        (len(t.rows) if isinstance(t.rows, list) else screen.lines or 0)
        for t in screen.tables
    )


class Formats:
    """The workbook's cell formats (ENG-03 9.3), plain and bold for totals."""

    def __init__(self, workbook: xlsxwriter.Workbook) -> None:
        self.header = workbook.add_format({"bold": True})
        self.label = workbook.add_format({"bold": True})
        self.plain = {}
        self.bold = {}
        for kind, number_format in (
            ("amount", AMOUNT),
            ("exact", EXACT),
            ("share", "0.0%"),
            ("count", "0"),
            ("date", "dd.mm.yyyy"),
        ):
            self.plain[kind] = workbook.add_format({"num_format": number_format})
            self.bold[kind] = workbook.add_format(
                {"num_format": number_format, "bold": True}
            )
        self.bold["text"] = workbook.add_format({"bold": True})

    def of(self, kind: str, total: bool):
        formats = self.bold if total else self.plain
        return formats.get(kind, formats.get("text"))


def write_cell(
    sheet: Worksheet, row: int, column: int, cell: Cell, formats: Formats, total: bool
) -> None:
    """Write one cell as ENG-03 9.3 says: amounts, shares and counts as
    numbers, dates as Excel dates, and everything else as text.
    """
    value = cell.value
    style = formats.of(cell.kind, total)
    if value is None or value == "":
        sheet.write_blank(row, column, None, style)
    elif cell.kind in ("amount", "exact", "share"):
        # Excel holds numbers as binary fractions; below a trillion OMR an
        # amount keeps every baisa (ENG-03 9.3).
        sheet.write_number(row, column, float(Decimal(value)), style)
    elif cell.kind == "count":
        sheet.write_number(row, column, int(value), style)
    elif cell.kind == "date":
        sheet.write_datetime(
            row, column, datetime.datetime.combine(value, datetime.time()), style
        )
    else:
        sheet.write_string(row, column, str(value), style)


def write_table(workbook: xlsxwriter.Workbook, table: Table, formats: Formats) -> int:
    """Write a table as a sheet: a frozen header row, its rows, and its total
    row, bold. Return how many data rows it wrote.
    """
    sheet = workbook.add_worksheet(table.sheet)
    for column, heading in enumerate(table.columns):
        sheet.set_column(column, column, WIDTHS.get(heading.kind, 16))
    sheet.freeze_panes(1, 0)
    for column, heading in enumerate(table.columns):
        sheet.write_string(0, column, heading.title, formats.header)
    written = 0
    for written, row in enumerate(table.rows, start=1):
        for column, cell in enumerate(row):
            write_cell(sheet, written, column, cell, formats, total=False)
    if table.total is not None:
        for column, cell in enumerate(table.total):
            write_cell(sheet, written + 1, column, cell, formats, total=True)
    return written


UPLOAD_COLUMNS = [
    ("Upload", "count"),
    ("File", "text"),
    ("SHA-256", "code"),
    ("Rows read", "count"),
    ("Approved by", "text"),
    ("Approved", "text"),
    ("Ruleset version", "count"),
    ("Lines counted", "count"),
    ("Actual spend counted", "amount"),
]


def facts(
    report: Report,
    screen: Screen,
    address: str,
    filters: Filters,
    user,
    at: datetime.datetime,
) -> list[tuple[str, str]]:
    """The Source sheet's facts: the report, the view, its filters and keys,
    the as-of line, an FAQ's question and answer, and who exported it when,
    with the app's version tag (ENG-03 9.2).
    """
    found = [
        ("Report", report.name),
        ("View", screen.title),
        ("Address", address),
        ("Filters", addresses.in_words(filters, screen.outside_applies)),
    ]
    latest = queries.latest_committed(report)
    if latest is None:
        found.append(("Figures as of", "No approved uploads yet."))
    else:
        found.append(
            (
                "Figures as of",
                f"Upload {latest.number}, {latest.original_name}, approved by "
                f"{latest.decided_by} on {moment(latest.decided_at)}.",
            )
        )
    waiting = queries.awaiting_approval(report)
    if waiting is not None:
        found.append(("Awaiting approval", f"Upload {waiting.number} isn't included."))
    if screen.view.startswith("faq/"):
        found.append(("Question", screen.title))
        if latest is None:
            found.append(("Answer", "No approved uploads yet."))
        else:
            found.append(
                ("Answer", screen.answer or "No actual spend for these filters.")
            )
    found += [
        ("Exported by", str(user)),
        ("Exported", moment(at)),
        ("App version", settings.APP_VERSION),
    ]
    return found


def write_source(
    workbook: xlsxwriter.Workbook,
    report: Report,
    screen: Screen,
    address: str,
    filters: Filters,
    user,
    at: datetime.datetime,
    formats: Formats,
) -> None:
    """Write the Source sheet: the facts, then every committed upload with
    what it still counts under the export's filters, and their total.
    """
    sheet = workbook.add_worksheet("Source")
    sheet.set_column(0, 0, 22)
    sheet.set_column(1, 1, 40)
    sheet.set_column(2, 2, 66)
    sheet.set_column(3, len(UPLOAD_COLUMNS) - 1, 20)
    row = 0
    for label, value in facts(report, screen, address, filters, user, at):
        sheet.write_string(row, 0, label, formats.label)
        sheet.write_string(row, 1, value)
        row += 1
    # What the uploads count follows the filters that apply on the screen.
    counted = filters if screen.outside_applies else Filters(year=filters.year)
    row += 1
    sheet.write_string(row, 0, "Committed uploads", formats.label)
    row += 1
    for column, (heading, _) in enumerate(UPLOAD_COLUMNS):
        sheet.write_string(row, column, heading, formats.header)
    for each in queries.committed_uploads(report, counted):
        row += 1
        upload = each.upload
        values = [
            upload.number,
            upload.original_name,
            upload.file_sha256,
            upload.rows_read,
            str(upload.decided_by),
            moment(upload.decided_at),
            upload.ruleset.version,
            each.lines,
            each.amount,
        ]
        for column, ((_, kind), value) in enumerate(
            zip(UPLOAD_COLUMNS, values, strict=True)
        ):
            write_cell(sheet, row, column, Cell(value, kind), formats, total=False)
    totals = queries.totals(report, counted)
    row += 1
    sheet.write_string(row, 0, "Total", formats.bold["text"])
    write_cell(sheet, row, 7, Cell(totals.lines, "count"), formats, total=True)
    write_cell(sheet, row, 8, Cell(totals.total, "amount"), formats, total=True)


def write_workbook(
    output: IO[bytes],
    report: Report,
    screen: Screen,
    address: str,
    filters: Filters,
    user,
    at: datetime.datetime,
) -> int:
    """Write the screen's export into output, and return how many data rows
    its sheets hold.
    """
    workbook = xlsxwriter.Workbook(output, OPTIONS)
    formats = Formats(workbook)
    rows = sum(write_table(workbook, table, formats) for table in screen.tables)
    write_source(workbook, report, screen, address, filters, user, at, formats)
    workbook.close()
    return rows
