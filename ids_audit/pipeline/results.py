"""What reading a file gives: its rows, sorted, and its mapping rows.

Plain dataclasses, so imports/ can stage them and the tests can check them
without a database. Plain Python, like the rest of pipeline/: it imports
nothing from Django.
"""

import datetime
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from ids_audit.pipeline.cleaning import cell_text
from ids_audit.pipeline.readers import Cell

# Why a row is skipped, in the order a row with several problems is given one
# (ENG-02 5). The reason "Repeats row n" comes from repeats_row().
ANOTHER_CURRENCY = "Another currency"
UNREADABLE_CODE = "Unreadable code"
UNREADABLE_AMOUNT = "Unreadable amount"
MORE_THAN_3_DECIMALS = "More than 3 decimal places"
INVALID_DATE = "Invalid date"
UNREADABLE_FISCAL_YEAR = "Unreadable fiscal year"


def repeats_row(row_number: int) -> str:
    """Return the reason for a row repeating the line key of an earlier one."""
    return f"Repeats row {row_number}"


# The mapping issues (ENG-02 5, Mapping rows, and 11.2).
LISTED_WITH_DIFFERENT_VALUES = "listed_with_different_values"
SHEET_AND_SPEND_DISAGREE = "sheet_and_spend_disagree"
NAMES_DIFFER = "names_differ"


@dataclass(frozen=True)
class CleanRow:
    """A spend row that passed every cleaning rule: a staging row to be
    (ENG-02 2, posting).

    is_actual is None when the value type is unclassified (ENG-02 5). It's
    staged as false, and approval is blocked.
    """

    sheet: str
    row_number: int
    company_code: str
    fiscal_year: int | None
    document_number: str
    line_item: str
    line_key: str
    posting_date: datetime.date
    gl_code: str
    cost_center_code: str
    value_type: str
    is_actual: bool | None
    amount: Decimal
    currency: str
    item_text: str


@dataclass(frozen=True)
class SkippedRow:
    """A row that couldn't be cleaned, with why (ENG-02 2, skipped_row).

    amount is exactly as read, or None if it couldn't be read. A spend row
    counts in reconciliation unless it's in another currency, and a mapping
    row never does (ENG-02 5).
    """

    sheet: str
    row_number: int
    reason: str
    amount: Decimal | None
    in_reconciliation: bool
    cells: tuple[Cell, ...]


def raw_values(cells: Sequence[Cell]) -> list[str]:
    """Return a row's cells as skipped_row.raw_values keeps them (ENG-02 2):
    each cell as text, from column A to the last cell that isn't empty.
    """
    texts = [raw_text(cell) for cell in cells]
    while texts and texts[-1] == "":
        texts.pop()
    return texts


def raw_text(cell: Cell) -> str:
    """Return one cell as text: text exactly as read, a logical cell as Excel
    shows it, and anything else as cell_text writes it (ENG-02 2).
    """
    if isinstance(cell, str):
        return cell
    # Checked first: cell_text would write True as "True".
    if isinstance(cell, bool):
        return "TRUE" if cell else "FALSE"
    return cell_text(cell)


@dataclass(frozen=True)
class OtherRow:
    """A row that isn't blank and isn't spend, such as a subtotal or a label.
    It's listed in the preview, with its amount if it has one (ENG-02 5).
    """

    sheet: str
    row_number: int
    amount: Decimal | None
    cells: tuple[Cell, ...]


@dataclass(frozen=True)
class GlMapRow:
    """One GL account's row in the upload's GL mapping (ENG-02 2, gl_ci_map)."""

    gl_code: str
    gl_name: str
    ci_code: str
    ci_name: str


@dataclass(frozen=True)
class CcMapRow:
    """One cost center's row in the upload's cost center mapping (ENG-02 2,
    cc_dept_map).
    """

    cc_code: str
    cc_name: str
    dept_code: str
    dept_name: str


@dataclass(frozen=True)
class MappingIssue:
    """A repeat or disagreement in the mapping rows (ENG-02 5, Mapping rows).

    field is the code field for a code listed with different values or a
    disagreement, and the name field, such as "ci_name", when names differ.
    rows are (sheet, row number) pairs.
    """

    kind: str
    field: str
    code: str
    rows: tuple[tuple[str, int], ...]

    @property
    def blocks_approval(self) -> bool:
        """Only a code listed with different values blocks approval (9.3)."""
        return self.kind == LISTED_WITH_DIFFERENT_VALUES


@dataclass(frozen=True)
class ReadResult:
    """Everything read from one file.

    control_total is the total row's amount exactly as read, before any
    rounding (ENG-02 9.1), or None if the file has no total row. column_layout
    is what upload.column_layout keeps: for each classified sheet, its header
    row and the column letter of each field.
    """

    spend_sheet: str
    header_row: int
    column_layout: dict[str, dict]
    clean_rows: tuple[CleanRow, ...]
    skipped_rows: tuple[SkippedRow, ...]
    other_rows: tuple[OtherRow, ...]
    control_total: Decimal | None
    control_total_row: int | None
    period_start: datetime.date | None
    period_end: datetime.date | None
    gl_map: tuple[GlMapRow, ...]
    cc_map: tuple[CcMapRow, ...]
    mapping_issues: tuple[MappingIssue, ...]
