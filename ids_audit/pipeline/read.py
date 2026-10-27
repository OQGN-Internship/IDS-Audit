"""Read and clean: a file's bytes and a ruleset's settings in, sorted rows out
(ENG-01 4, build order; ENG-02 4 and 5).

read_file() raises UnknownFormat for a file that isn't an accepted format,
FileUnreadable for one whose upload ends at failed, and NeedsColumns for one
that stops at needs_columns. Otherwise it returns a ReadResult.

Plain Python, like the rest of pipeline/: it imports nothing from Django.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from ids_audit.pipeline.cleaning import (
    BAISA,
    cell_text,
    classify_value_type,
    clean_amount,
    clean_code,
    clean_date,
    clean_whole_number,
    date_from_period,
    has_more_than_3_decimals,
)
from ids_audit.pipeline.columns import (
    CC_MAP,
    GL_MAP,
    HEADER_SEARCH_ROWS,
    SPEND,
    Choice,
    Layout,
    find_layouts,
    header_text,
)
from ids_audit.pipeline.formats import TEXT, detect_format
from ids_audit.pipeline.mappings import (
    CC_FIELDS,
    GL_FIELDS,
    Entry,
    MapFields,
    MapRow,
    cell_of,
    is_blank,
    read_mapping_sheet,
    rows_below_header,
    settle,
)
from ids_audit.pipeline.readers import Cell, FileUnreadable, read_sheets
from ids_audit.pipeline.results import (
    ANOTHER_CURRENCY,
    INVALID_DATE,
    MORE_THAN_3_DECIMALS,
    NO_LINE_KEY,
    UNREADABLE_AMOUNT,
    UNREADABLE_CODE,
    UNREADABLE_FISCAL_YEAR,
    CcMapRow,
    CleanRow,
    GlMapRow,
    OtherRow,
    ReadResult,
    SkippedRow,
    raw_values,
    repeats_row,
)

# The codes a spend row may have. The commitment item and department are only
# found on an Excel spend sheet (ENG-02 4.2).
CODE_FIELDS = (
    "gl_code",
    "cost_center_code",
    "company_code",
    "document_number",
    "line_item",
    "ci_code",
    "dept_code",
)

# The word that marks the total row, as a whole word in any case, so "Grand
# Total" has it and "Subtotal" doesn't (ENG-02 5).
TOTAL = re.compile(r"\btotal\b", re.IGNORECASE)


@dataclass
class SpendRows:
    """The spend sheet's rows, sorted, and its spend rows' mapping entries."""

    clean: list[CleanRow] = field(default_factory=list)
    skipped: list[SkippedRow] = field(default_factory=list)
    other: list[OtherRow] = field(default_factory=list)
    control_total: Decimal | None = None
    control_total_row: int | None = None
    gl_entries: list[Entry] = field(default_factory=list)
    cc_entries: list[Entry] = field(default_factory=list)


def read_file(
    data: bytes,
    settings: Mapping,
    *,
    need_line_key: bool = False,
    choices: Sequence[Choice] = (),
    mapping_only: bool = False,
) -> ReadResult:
    """Read and clean a file with a ruleset version's settings (ENG-02 3).

    need_line_key is True for a report in line-key mode, where a spend row
    without a full line key is skipped (ENG-02 7.3). choices are the columns
    picked for sheets with a given header, the first that matches applying
    (4.5). mapping_only is True for a mapping upload, whose file is a workbook
    with mapping sheets and no spend sheet (8.5).
    """
    file_format = detect_format(data)
    if mapping_only and file_format == TEXT:
        raise FileUnreadable(
            "A mapping is uploaded as an Excel file: a CSV file holds one table, "
            "never a mapping sheet."
        )
    sheets = read_sheets(data, file_format)
    layouts = find_layouts(
        sheets,
        settings,
        is_text=file_format == TEXT,
        choices=choices,
        mapping_only=mapping_only,
    )
    spend = (
        read_spend_sheet(layouts[SPEND], settings, need_line_key)
        if SPEND in layouts
        else SpendRows()
    )

    skipped, other = list(spend.skipped), list(spend.other)
    mappings = {}
    issues = []
    for fields, spend_entries in (
        (GL_FIELDS, spend.gl_entries),
        (CC_FIELDS, spend.cc_entries),
    ):
        sheet_entries = []
        if fields.sheet_type in layouts:
            sheet_entries, sheet_skipped, sheet_other = read_mapping_sheet(
                layouts[fields.sheet_type], fields
            )
            skipped += sheet_skipped
            other += sheet_other
        rows, found = settle(fields, sheet_entries, spend_entries)
        mappings[fields.sheet_type] = rows
        issues += found

    # Rows are listed in the file's order: by sheet, then row.
    sheet_order = {sheet.name: index for index, sheet in enumerate(sheets)}

    def place(row: SkippedRow | OtherRow) -> tuple[int, int]:
        return (sheet_order[row.sheet], row.row_number)

    dates = [row.posting_date for row in spend.clean]
    return ReadResult(
        spend_sheet=layouts[SPEND].sheet.name if SPEND in layouts else None,
        header_row=layouts[SPEND].header_row if SPEND in layouts else None,
        column_layout={
            layout.sheet.name: {
                "header_row": layout.header_row,
                "header": list(layout.header),
                "columns": layout.letters(),
                "picked": dict(layout.picked),
            }
            for layout in layouts.values()
        },
        clean_rows=tuple(spend.clean),
        skipped_rows=tuple(sorted(skipped, key=place)),
        other_rows=tuple(sorted(other, key=place)),
        control_total=spend.control_total,
        control_total_row=spend.control_total_row,
        period_start=min(dates, default=None),
        period_end=max(dates, default=None),
        gl_map=tuple(GlMapRow(*map_values(row)) for row in mappings[GL_MAP]),
        cc_map=tuple(CcMapRow(*map_values(row)) for row in mappings[CC_MAP]),
        mapping_issues=tuple(issues),
    )


def map_values(row: MapRow) -> tuple[str, str, str, str]:
    """Return a mapping row's code, code name, value and value name."""
    return (row.code, row.code_name, row.value, row.value_name)


def read_spend_sheet(
    layout: Layout, settings: Mapping, need_line_key: bool = False
) -> SpendRows:
    """Sort the spend sheet's rows below its header (ENG-02 5), each list in
    row order.

    A blank row is left out. A spend row, with a GL account and a cost center,
    is clean or skipped. Of the rest, the last row reading Total below the last
    spend row, with an amount and neither code, is the total row (9.1), and
    every other one is an other row.
    """
    result = SpendRows()
    rows = [
        (n, row, is_spend_row(layout, row))
        for n, row in rows_below_header(layout)
        if not is_blank(row)
    ]
    last_spend = max((n for n, _, is_spend in rows if is_spend), default=0)
    totals: list[tuple[int, Decimal, tuple[Cell, ...]]] = []
    kept: list[tuple[CleanRow, dict[str, str], tuple[Cell, ...]]] = []

    for row_number, row, is_spend in rows:
        if is_spend:
            outcome = clean_spend_row(layout, row_number, row, settings)
            if isinstance(outcome, SkippedRow):
                result.skipped.append(outcome)
            else:
                kept.append((*outcome, row))
            continue
        amount = readable_amount(layout, row, settings)
        if row_number > last_spend and amount is not None and is_total_row(layout, row):
            totals.append((row_number, amount, row))
        else:
            result.other.append(OtherRow(layout.sheet.name, row_number, amount, row))

    if totals:
        *earlier, (result.control_total_row, result.control_total, _) = totals
        result.other += [
            OtherRow(layout.sheet.name, n, amount, row) for n, amount, row in earlier
        ]

    # A line key already seen in this file skips the row that repeats it (7.2).
    first_with_key: dict[str, int] = {}
    for clean, codes, row in kept:
        if need_line_key and not clean.line_key:
            result.skipped.append(
                SkippedRow(
                    clean.sheet, clean.row_number, NO_LINE_KEY, clean.amount, True, row
                )
            )
            continue
        first = first_with_key.setdefault(clean.line_key, clean.row_number)
        if clean.line_key and first != clean.row_number:
            result.skipped.append(
                SkippedRow(
                    clean.sheet,
                    clean.row_number,
                    repeats_row(first),
                    clean.amount,
                    True,
                    row,
                )
            )
            continue
        result.clean.append(clean)
        add_entry(result.gl_entries, GL_FIELDS, layout, clean, codes, row)
        add_entry(result.cc_entries, CC_FIELDS, layout, clean, codes, row)
    return result


def is_spend_row(layout: Layout, row: Sequence[Cell]) -> bool:
    """Return True when the row's GL account and cost center cells both hold
    something (ENG-02 5).
    """
    return all(
        cell_text(cell_of(layout, row, f)) != ""
        for f in ("gl_code", "cost_center_code")
    )


def is_total_row(layout: Layout, row: Sequence[Cell]) -> bool:
    """Return True when the row has neither code and the word Total in a cell."""
    no_codes = all(
        cell_text(cell_of(layout, row, f)) == ""
        for f in ("gl_code", "cost_center_code")
    )
    return no_codes and any(isinstance(c, str) and TOTAL.search(c) for c in row)


def readable_amount(
    layout: Layout, row: Sequence[Cell], settings: Mapping
) -> Decimal | None:
    """Return the row's amount exactly as read, or None if it has none or it
    can't be read.
    """
    try:
        return clean_amount(
            cell_of(layout, row, "amount"), settings["decimal_separator"]
        )
    except ValueError:
        return None


def clean_spend_row(
    layout: Layout, row_number: int, row: Sequence[Cell], settings: Mapping
) -> SkippedRow | tuple[CleanRow, dict[str, str]]:
    """Clean one spend row, field by field (ENG-02 5).

    Return the skipped row, with the first reason in the order of ENG-02 5, or
    the clean row with its codes.
    """

    def cell(field: str) -> Cell:
        return cell_of(layout, row, field)

    problems = []
    currency = settings["currency"].upper()
    if cell_text(cell("currency")).upper() not in ("", currency):
        problems.append(ANOTHER_CURRENCY)

    try:
        codes = {f: clean_code(cell(f)) for f in CODE_FIELDS}
    except ValueError:
        problems.append(UNREADABLE_CODE)
        codes = {}

    amount = readable_amount(layout, row, settings)
    if amount is None:
        problems.append(UNREADABLE_AMOUNT)
    elif has_more_than_3_decimals(amount):
        problems.append(MORE_THAN_3_DECIMALS)

    try:
        fiscal_year = clean_whole_number(cell("fiscal_year"))
    except ValueError:
        fiscal_year, fiscal_year_readable = None, False
    else:
        fiscal_year_readable = True
    try:
        if "posting_date" in layout.columns:
            posting_date = clean_date(cell("posting_date"), settings["date_order"])
            if posting_date is None:
                raise ValueError("the date is empty")
        else:
            period = clean_whole_number(cell("period"))
            posting_date = date_from_period(fiscal_year, period)
    except ValueError:
        problems.append(INVALID_DATE)
    if not fiscal_year_readable and "posting_date" in layout.columns:
        problems.append(UNREADABLE_FISCAL_YEAR)

    if problems:
        return SkippedRow(
            layout.sheet.name,
            row_number,
            problems[0],
            amount,
            problems[0] != ANOTHER_CURRENCY,
            tuple(row),
        )

    value_type = cell_text(cell("value_type"))
    key_parts = (
        codes["company_code"],
        fiscal_year,
        codes["document_number"],
        codes["line_item"],
    )
    line_key = "-".join(map(str, key_parts)) if all(key_parts) else ""
    clean = CleanRow(
        sheet=layout.sheet.name,
        row_number=row_number,
        company_code=codes["company_code"],
        fiscal_year=fiscal_year,
        document_number=codes["document_number"],
        line_item=codes["line_item"],
        line_key=line_key,
        posting_date=posting_date,
        gl_code=codes["gl_code"],
        cost_center_code=codes["cost_center_code"],
        value_type=value_type,
        is_actual=classify_value_type(value_type, settings["actual_spend_rule"]),
        amount=amount.quantize(BAISA),
        currency=currency,
        item_text=cell_text(cell("item_text")),
    )
    return clean, codes


def add_entry(
    entries: list[Entry],
    fields: MapFields,
    layout: Layout,
    clean: CleanRow,
    codes: Mapping[str, str],
    row: Sequence[Cell],
) -> None:
    """Add a clean spend row's mapping of its code, when the spend sheet has a
    column for the value it maps to and the row's isn't empty (ENG-02 5).
    """
    value = codes[fields.value]
    if fields.value not in layout.columns or not value:
        return
    entries.append(
        Entry(
            code=codes[fields.code],
            value=value,
            code_name=cell_text(cell_of(layout, row, fields.code_name)),
            value_name=cell_text(cell_of(layout, row, fields.value_name)),
            sheet=clean.sheet,
            row_number=clean.row_number,
        )
    )


@dataclass(frozen=True)
class SheetRows:
    """A sheet's header row and the rows after it, for picking columns
    (ENG-02 4.5). Each row is its number and its cells as text, every row as
    wide as the widest.
    """

    header_row: int
    rows: tuple[tuple[int, tuple[str, ...]], ...]


def first_rows(
    data: bytes, sheet: str, header: Sequence[str], count: int = 5
) -> SheetRows:
    """Return the header row of the named sheet and the count rows after it.

    The header row is the first of the sheet's first 25 rows whose text is
    exactly header, as the column problems keep it, or row 1 if none is. A
    delimited text file's one sheet is named "".
    """
    sheets = read_sheets(data, detect_format(data))
    found = next((s for s in sheets if s.name == sheet), None)
    if found is None:
        raise ValueError(f"The file has no sheet named {sheet!r}.")
    start = next(
        (
            index
            for index, row in enumerate(found.rows[:HEADER_SEARCH_ROWS])
            if header_text(row) == tuple(header)
        ),
        0,
    )
    texts = [raw_values(row) for row in found.rows[start : start + count + 1]]
    width = max((len(cells) for cells in texts), default=0)
    return SheetRows(
        start + 1,
        tuple(
            (start + 1 + offset, tuple(cells + [""] * (width - len(cells))))
            for offset, cells in enumerate(texts)
        ),
    )
