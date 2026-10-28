"""What the upload screens show: an upload's preview laid out for its page,
and the names of its exception types and their items (ENG-02 11, ENG-03 4).

Every figure comes from what the upload saved when it was read, so the
preview of any past upload shows exactly what it showed then (ENG-02 11).
Nothing here adds an amount up.
"""

from dataclasses import dataclass

from ids_audit.audit.models import AuditEvent
from ids_audit.imports.models import Upload
from ids_audit.ledger.models import CcDeptMap, GlCiMap
from ids_audit.pipeline import preview as types
from ids_audit.pipeline.columns import ColumnProblem

# Each exception type's name, as in ENG-02 11.2's table.
EXCEPTION_NAMES = {
    types.DIFFERENCE_NOT_ZERO: "Difference not zero",
    types.NO_CONTROL_TOTAL: "No control total",
    types.ROW_IN_ANOTHER_CURRENCY: "Row in another currency",
    types.UNCLASSIFIED_VALUE_TYPE: "Unclassified value type",
    types.LISTED_WITH_DIFFERENT_VALUES: "Mapping code listed with different values",
    types.SKIPPED_ROW: "Skipped row",
    types.OTHER_ROW: "Other row without GL account or cost center",
    types.MISSING_FROM_GL_MAP: "GL account with no budget line",
    types.LISTED_WITHOUT_CI: "GL account listed without a commitment item",
    types.OUTSIDE_IDS: "Cost center outside IDS",
    types.LISTED_WITHOUT_DEPARTMENT: "Cost center listed without a department",
    types.MAPPING_CHANGE: "Mapping change",
    types.SHEET_AND_SPEND_DISAGREE: "Mapping sheet and spend sheet disagree",
    types.NAMES_DIFFER: "Names differ for one code",
    types.DUPLICATE_DIFFERENT_AMOUNT: "Duplicate with a different amount",
    types.MONTH_WITHOUT_ROWS: "A month with no rows inside the replaced months",
    types.REPLACES_OUTSIDE_DATES: "Replaced postings outside the file's dates",
}

# The keys an item may have beside its rows and amount (ENG-02 11.2), in the
# order they're shown, with their headings. The amounts among them are marked.
ITEM_KEYS = {
    "code": "Code",
    "mapping": "Mapping",
    "field": "Field",
    "value_type": "Value type",
    "reason": "Reason",
    "month": "Month",
    "upload": "Upload",
    "postings": "Postings replaced",
    "before": "Before",
    "after": "After",
    "committed": "Of it, already committed",
    "posting_amount": "Amount in the report",
}
AMOUNT_KEYS = {"committed", "posting_amount"}
MAPPING_NAMES = {"gl": "GL", "cc": "Cost center"}
# What each value type counts as (ENG-02 5).
VALUE_TYPE_CLASSES = {
    "actual": "Actual spend",
    "not_actual": "Not actual spend",
    "unclassified": "Unclassified",
}


@dataclass(frozen=True)
class Column:
    """One column of an exception's item table."""

    key: str
    heading: str
    is_amount: bool


def item_columns(exception: dict) -> list[Column]:
    """Return the columns an exception's items need: each key any item has,
    in ITEM_KEYS' order.
    """
    present = {key for item in exception["items"] for key in item}
    return [
        Column(key, heading, key in AMOUNT_KEYS)
        for key, heading in ITEM_KEYS.items()
        if key in present
    ]


def cell(item: dict, column: Column) -> str:
    """Return how one item key is written in its table. An amount is left to
    the template's amount filter.
    """
    value = item.get(column.key)
    if column.key == "mapping":
        return MAPPING_NAMES.get(value, value)
    if column.key in ("before", "after"):
        # Empty for a GL account listed without a commitment item, None when
        # the mapping doesn't list the code (ENG-02 11.2).
        return "not listed" if value is None else (value or "none")
    return "" if value is None else str(value)


def item_rows(exception: dict) -> list[dict]:
    """Return an exception's items as rows of its table: each column's text or
    amount, its rows in the file, and its amount.
    """
    columns = item_columns(exception)
    return [
        {
            "cells": [
                {
                    "value": item.get(c.key) if c.is_amount else cell(item, c),
                    "is_amount": c.is_amount,
                }
                for c in columns
            ],
            "rows": item["rows"],
            "amount": item["amount"],
        }
        for item in exception["items"]
    ]


def exception_summary(preview: dict) -> list[dict]:
    """Return each exception in the preview with its name and row count, in
    ENG-02 11.2's order.
    """
    return [
        {
            **exception,
            "name": EXCEPTION_NAMES[exception["type"]],
            "row_count": sum(len(item["rows"]) for item in exception["items"]),
        }
        for exception in preview.get("exceptions", ())
    ]


def value_types(preview: dict) -> list[dict]:
    """Return each value type in the preview, with what it counts as."""
    return [
        {**value, "counts_as": VALUE_TYPE_CLASSES[value["class"]]}
        for value in preview.get("value_types", ())
    ]


def mappings(preview: dict) -> list[tuple[str, int | None]]:
    """Return which upload's GL and cost center mappings the file's postings
    will use, by upload number, or None when no upload has one (ENG-02 11.1).
    """
    found = preview.get("mappings", {})
    return [(MAPPING_NAMES[name], found.get(name)) for name in ("gl", "cc")]


def mapping_codes(upload: Upload) -> list[dict]:
    """Return how many codes each of the upload's mappings lists, and for a
    mapping it hasn't, the upload whose mapping stays in force, by number, or
    None when no upload has one (ENG-03 4, a mapping upload's summary).
    """
    found = (upload.preview or {}).get("mappings", {})
    return [
        {
            "label": MAPPING_NAMES[name],
            "codes": model.objects.filter(upload=upload).count(),
            "noun": noun,
            "in_force": found.get(name),
        }
        for name, model, noun in (
            ("gl", GlCiMap, "GL account"),
            ("cc", CcDeptMap, "cost center"),
        )
    ]


def moves(preview: dict) -> dict:
    """Return the spend approving moves to another budget line or department,
    and how many codes change: the preview's mapping change exception, whose
    amount is its items' (ENG-02 11.2).
    """
    for exception in preview.get("exceptions", ()):
        if exception["type"] == types.MAPPING_CHANGE:
            return {"amount": exception["amount"], "codes": len(exception["items"])}
    return {"amount": "0.000", "codes": 0}


def find_exception(upload: Upload, kind: str) -> dict | None:
    """Return the upload's exception of one type, or None if its preview has
    none of that type.
    """
    for exception in (upload.preview or {}).get("exceptions", ()):
        if exception["type"] == kind:
            return exception
    return None


# The events that close an upload with a reason, and what each is called.
CLOSINGS = {
    AuditEvent.Action.UPLOAD_REJECTED: "Rejected",
    AuditEvent.Action.UPLOAD_WITHDRAWN: "Withdrawn",
    AuditEvent.Action.UPLOAD_ROLLED_BACK: "Rolled back",
}


def closing(upload: Upload) -> dict | None:
    """Return how the upload was last rejected, withdrawn or rolled back, and
    the reason given, which only that event keeps (ENG-02 6 and 10.3), or None
    if it wasn't.
    """
    event = (
        AuditEvent.objects.filter(upload=upload, action__in=CLOSINGS)
        .order_by("-pk")
        .first()
    )
    if event is None:
        return None
    return {"label": CLOSINGS[event.action], "reason": event.details["reason"]}


def column_problems(upload: Upload) -> tuple[list[dict], dict[str, list[str]]]:
    """Return the problems the upload last stopped at needs_columns with, and
    each problem sheet's header, kept in its upload_needs_columns event
    (ENG-02 4.5 and 6).
    """
    stop = (
        AuditEvent.objects.filter(
            upload=upload, action=AuditEvent.Action.UPLOAD_NEEDS_COLUMNS
        )
        .order_by("-pk")
        .first()
    )
    if stop is None:
        return [], {}
    problems = []
    for problem in stop.details["problems"]:
        found = ColumnProblem(
            problem["sheet"], problem["field"], tuple(problem["columns"])
        )
        problems.append({**problem, "text": str(found), "field_name": found.field_name})
    return problems, stop.details["headers"]
