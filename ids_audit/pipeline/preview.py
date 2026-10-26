"""The parts of an upload's preview that come from the file alone (ENG-02 11).
imports/ adds the parts that depend on the report.

Every amount is written with amount_text, so the preview never holds a float.
Plain Python, like the rest of pipeline/: it imports nothing from Django.
"""

import datetime
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal

from ids_audit.pipeline.reconciliation import Reconciliation, amount_text
from ids_audit.pipeline.results import (
    ANOTHER_CURRENCY,
    LISTED_WITH_DIFFERENT_VALUES,
    NAMES_DIFFER,
    SHEET_AND_SPEND_DISAGREE,
    CleanRow,
    ReadResult,
)

# The exception types, in the order of ENG-02 11.2's table.
DIFFERENCE_NOT_ZERO = "difference_not_zero"
NO_CONTROL_TOTAL = "no_control_total"
ROW_IN_ANOTHER_CURRENCY = "another_currency"
UNCLASSIFIED_VALUE_TYPE = "unclassified_value_type"
# LISTED_WITH_DIFFERENT_VALUES = "listed_with_different_values", from results.
SKIPPED_ROW = "skipped_row"
OTHER_ROW = "other_row"
MISSING_FROM_GL_MAP = "missing_from_gl_map"
LISTED_WITHOUT_CI = "listed_without_ci"
OUTSIDE_IDS = "outside_ids"
LISTED_WITHOUT_DEPARTMENT = "listed_without_department"
MAPPING_CHANGE = "mapping_change"
# SHEET_AND_SPEND_DISAGREE = "sheet_and_spend_disagree", from results.
# NAMES_DIFFER = "names_differ", from results.
DUPLICATE_DIFFERENT_AMOUNT = "duplicate_different_amount"
MONTH_WITHOUT_ROWS = "month_without_rows"
REPLACES_OUTSIDE_DATES = "replaces_outside_dates"

ORDER = (
    DIFFERENCE_NOT_ZERO,
    NO_CONTROL_TOTAL,
    ROW_IN_ANOTHER_CURRENCY,
    UNCLASSIFIED_VALUE_TYPE,
    LISTED_WITH_DIFFERENT_VALUES,
    SKIPPED_ROW,
    OTHER_ROW,
    MISSING_FROM_GL_MAP,
    LISTED_WITHOUT_CI,
    OUTSIDE_IDS,
    LISTED_WITHOUT_DEPARTMENT,
    MAPPING_CHANGE,
    SHEET_AND_SPEND_DISAGREE,
    NAMES_DIFFER,
    DUPLICATE_DIFFERENT_AMOUNT,
    MONTH_WITHOUT_ROWS,
    REPLACES_OUTSIDE_DATES,
)

# The types that block approval (ENG-02 9.3).
BLOCKING = frozenset(
    (
        DIFFERENCE_NOT_ZERO,
        NO_CONTROL_TOTAL,
        ROW_IN_ANOTHER_CURRENCY,
        UNCLASSIFIED_VALUE_TYPE,
        LISTED_WITH_DIFFERENT_VALUES,
    )
)

# Which mapping a mapping issue's field belongs to.
GL_FIELDS = ("gl_code", "gl_name", "ci_code", "ci_name")

Place = tuple[str, int]


def item(rows: Iterable[Place], amount: Decimal | None, **keys) -> dict:
    """Return one item of an exception: the rows it covers, as pairs of sheet
    and row number, its amount or None, and its own keys.
    """
    return {
        "rows": [[sheet, number] for sheet, number in rows],
        "amount": None if amount is None else amount_text(amount),
        **keys,
    }


def exception(kind: str, items: list[dict], amount: Decimal | None = None) -> dict:
    """Return an exception of a type with its items. Its amount is the given
    one, or the sum of its items' amounts, or None when none has one.
    """
    if amount is None:
        amounts = [Decimal(i["amount"]) for i in items if i["amount"] is not None]
        amount = sum(amounts, Decimal(0)) if amounts else None
    return {
        "type": kind,
        "blocks_approval": kind in BLOCKING,
        "amount": None if amount is None else amount_text(amount),
        "items": items,
    }


def in_order(exceptions: Iterable[dict]) -> list[dict]:
    """Return the exceptions in the order of ENG-02 11.2's table."""
    return sorted(exceptions, key=lambda e: ORDER.index(e["type"]))


def occurring(kind: str, items: list[dict]) -> list[dict]:
    """Return the type's exception in a list when it has items, or an empty
    list when it doesn't occur.
    """
    return [exception(kind, items)] if items else []


def place(row: CleanRow) -> Place:
    """Return a row's place in the file."""
    return (row.sheet, row.row_number)


def grouped(rows: Iterable[CleanRow], key) -> list[tuple[str, list[CleanRow]]]:
    """Return the rows grouped by key(row), in the order each group first
    appears.
    """
    groups: dict[str, list[CleanRow]] = {}
    for row in rows:
        groups.setdefault(key(row), []).append(row)
    return list(groups.items())


def total(rows: Iterable[CleanRow]) -> Decimal:
    """Return the sum of the rows' amounts."""
    return sum((row.amount for row in rows), Decimal(0))


def file_exceptions(
    result: ReadResult,
    reconciliation: Reconciliation,
    to_commit: Sequence[CleanRow],
    duplicates: Mapping[Place, Decimal],
    months_replaced: Sequence[str],
) -> list[dict]:
    """Return the exceptions that come from the file alone and occur in it
    (ENG-02 11.2), unordered. Those that depend on the report are added by
    imports/.
    """
    found = []
    if reconciliation.control_total is None:
        found.append(exception(NO_CONTROL_TOTAL, []))
    elif reconciliation.difference != 0:
        found.append(exception(DIFFERENCE_NOT_ZERO, [], reconciliation.difference))

    in_currency = [r for r in result.skipped_rows if r.reason == ANOTHER_CURRENCY]
    found += occurring(
        ROW_IN_ANOTHER_CURRENCY,
        [item([(r.sheet, r.row_number)], r.amount) for r in in_currency],
    )
    unclassified = [r for r in to_commit if r.is_actual is None]
    found += occurring(
        UNCLASSIFIED_VALUE_TYPE,
        [
            item(map(place, rows), total(rows), value_type=value_type)
            for value_type, rows in grouped(unclassified, lambda r: r.value_type)
        ],
    )
    for kind in (LISTED_WITH_DIFFERENT_VALUES, SHEET_AND_SPEND_DISAGREE, NAMES_DIFFER):
        issues = [i for i in result.mapping_issues if i.kind == kind]
        found += occurring(
            kind,
            [
                item(
                    i.rows,
                    None,
                    mapping="gl" if i.field in GL_FIELDS else "cc",
                    code=i.code,
                    **({"field": i.field} if kind == NAMES_DIFFER else {}),
                )
                for i in issues
            ],
        )
    found += occurring(
        SKIPPED_ROW,
        [
            item([(r.sheet, r.row_number)], r.amount, reason=r.reason)
            for r in result.skipped_rows
            if r.reason != ANOTHER_CURRENCY
        ],
    )
    found += occurring(
        OTHER_ROW,
        [item([(r.sheet, r.row_number)], r.amount) for r in result.other_rows],
    )
    found += occurring(
        DUPLICATE_DIFFERENT_AMOUNT,
        [
            item([place(r)], r.amount, posting_amount=amount_text(posting))
            for r in result.clean_rows
            if (posting := duplicates.get(place(r))) is not None and posting != r.amount
        ],
    )
    with_rows = {r.posting_date.strftime("%Y-%m") for r in to_commit}
    found += occurring(
        MONTH_WITHOUT_ROWS,
        [item([], None, month=m) for m in months_replaced if m not in with_rows],
    )
    return found


def first_of_next_month(day: datetime.date) -> datetime.date:
    """Return the first day of the month after day's."""
    if day.month == 12:
        return datetime.date(day.year + 1, 1, 1)
    return datetime.date(day.year, day.month + 1, 1)


def months_between(first: datetime.date, last: datetime.date) -> list[str]:
    """Return each month from first's to last's, such as "2026-03"."""
    months = []
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        months.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def value_types(result: ReadResult, to_commit: Sequence[CleanRow]) -> list[dict]:
    """Return each value type among the rows to commit, with its row count,
    amount and class, when the spend sheet has a value-type column (11.2).
    """
    columns = result.column_layout[result.spend_sheet]["columns"]
    if "value_type" not in columns:
        return []
    classes = {True: "actual", False: "not_actual", None: "unclassified"}
    return [
        {
            "value_type": value_type,
            "rows": len(rows),
            "amount": amount_text(total(rows)),
            "class": classes[rows[0].is_actual],
        }
        for value_type, rows in grouped(to_commit, lambda r: r.value_type)
    ]


def actual_and_not(to_commit: Sequence[CleanRow]) -> tuple[Decimal, Decimal]:
    """Return the amount of the rows to commit that are actual spend, and of
    those that aren't, an unclassified one included (ENG-02 5).
    """
    actual = total(r for r in to_commit if r.is_actual is True)
    return actual, total(to_commit) - actual
