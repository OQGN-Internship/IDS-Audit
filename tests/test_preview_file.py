"""The preview's parts that come from the file alone (ENG-02 11.1 and 11.2).
The parts that depend on the report are in test_preview.py.
"""

import datetime
from decimal import Decimal

from ids_audit.pipeline.preview import (
    actual_and_not,
    exception,
    file_exceptions,
    first_of_next_month,
    in_order,
    months_between,
    value_types,
)
from ids_audit.pipeline.reconciliation import reconcile
from tests.files import HEADER, LINE, csv_file, line, read, total_row, xlsx_file

RULE = {"actual": ["Actual"], "not_actual": ["Commitment"]}
WITH_TYPE = (*HEADER, "Value Type")
GL = ("G/L Account", "G/L Account Name", "Commitment Item", "Commitment Item Name")
CC = ("Cost Center", "Cost Center Name", "Department Code", "Department Name")


def exceptions_of(result, duplicates=None, months=()) -> dict[str, dict]:
    """Return the file's exceptions by type, with the duplicates' rows left
    out of the rows to commit.
    """
    duplicates = duplicates or {}
    to_commit = [
        r for r in result.clean_rows if (r.sheet, r.row_number) not in duplicates
    ]
    found = file_exceptions(
        result, reconcile(result), to_commit, duplicates, list(months)
    )
    return {e["type"]: e for e in found}


def test_a_file_that_reconciles_and_is_clean_has_no_exceptions() -> None:
    """Verify a file with nothing to report lists no exception at all."""
    assert exceptions_of(read(csv_file(LINE, total_row("1000.000")))) == {}


def test_a_file_with_no_total_row_has_no_control_total() -> None:
    """Verify the blocking exception, with no amount and no items."""
    assert exceptions_of(read(csv_file(LINE))) == {
        "no_control_total": {
            "type": "no_control_total",
            "blocks_approval": True,
            "amount": None,
            "items": [],
        }
    }


def test_a_difference_is_the_exception_s_amount() -> None:
    """Verify a total 0.001 short leaves a difference of -0.001, which blocks."""
    found = exceptions_of(read(csv_file(LINE, total_row("999.999"))))
    assert found == {
        "difference_not_zero": {
            "type": "difference_not_zero",
            "blocks_approval": True,
            "amount": "-0.001",
            "items": [],
        }
    }


def test_rows_in_another_currency_block_and_aren_t_skipped_rows() -> None:
    """Verify each USD row is an item with its amount, and the type blocks."""
    usd = line(DocumentNo="5100000102", Amount_in_LC="50.000", LCurr="USD")
    found = exceptions_of(read(csv_file(LINE, usd, total_row("1000.000"))))
    assert found == {
        "another_currency": {
            "type": "another_currency",
            "blocks_approval": True,
            "amount": "50.000",
            "items": [{"rows": [["", 3]], "amount": "50.000"}],
        }
    }


def test_unclassified_value_types_block_one_item_per_value_type() -> None:
    """Verify Plan's two rows and Budget's one each make an item with their
    rows and amount, and an actual row isn't one.
    """
    data = csv_file(
        (*LINE, "Actual"),
        (*line(DocumentNo="2", Amount_in_LC="10.000"), "Plan"),
        (*line(DocumentNo="3", Amount_in_LC="5.000"), "Budget"),
        (*line(DocumentNo="4", Amount_in_LC="2.500"), "Plan"),
        (*total_row("1017.500"), ""),
        header=WITH_TYPE,
    )
    found = exceptions_of(read(data, actual_spend_rule=RULE))
    assert list(found) == ["unclassified_value_type"]
    assert found["unclassified_value_type"]["blocks_approval"] is True
    assert found["unclassified_value_type"]["amount"] == "17.500"
    assert found["unclassified_value_type"]["items"] == [
        {"rows": [["", 3], ["", 5]], "amount": "12.500", "value_type": "Plan"},
        {"rows": [["", 4]], "amount": "5.000", "value_type": "Budget"},
    ]


def test_skipped_rows_are_items_with_their_reason_and_amount() -> None:
    """Verify a skipped row keeps its amount as read, one that couldn't be
    read has none, and neither blocks.
    """
    data = csv_file(
        LINE,
        line(DocumentNo="2", Amount_in_LC="12.3456"),
        line(DocumentNo="3", Amount_in_LC="12,34.5"),
        total_row("1012.3456"),
    )
    found = exceptions_of(read(data))
    assert found["skipped_row"] == {
        "type": "skipped_row",
        "blocks_approval": False,
        "amount": "12.3456",
        "items": [
            {
                "rows": [["", 3]],
                "amount": "12.3456",
                "reason": "More than 3 decimal places",
            },
            {"rows": [["", 4]], "amount": None, "reason": "Unreadable amount"},
        ],
    }
    # The total leaves out the unreadable amount, so the file reconciles.
    assert list(found) == ["skipped_row"]


def test_other_rows_are_items_with_their_amount_if_they_have_one() -> None:
    """Verify a subtotal keeps its amount and a label has none."""
    subtotal = ("", "0000610100", "", "", "", "", "", "", "1,000.000", "OMR")
    label = ("Spend to date", "", "", "", "", "", "", "", "", "")
    found = exceptions_of(read(csv_file(LINE, subtotal, label, total_row("1000"))))
    assert found == {
        "other_row": {
            "type": "other_row",
            "blocks_approval": False,
            "amount": "1000.000",
            "items": [
                {"rows": [["", 3]], "amount": "1000.000"},
                {"rows": [["", 4]], "amount": None},
            ],
        }
    }


def test_mapping_issues_are_items_naming_their_mapping_and_code() -> None:
    """Verify a GL account listed with two commitment items blocks, and a cost
    center given two names and a spend row disagreeing with the sheet don't.
    """
    spend = [
        (*HEADER, "Commitment Item"),
        (*line(**{"G/L Acct": "620100"}), "2201"),
        (*line(**{"G/L Acct": "620100"}, DocumentNo="2"), "2301"),
        (*total_row("2000.000"), ""),
    ]
    gl_sheet = [
        GL,
        ("610100", "Software", "2101", "Licences"),
        ("610100", "Software", "2401", "Licences"),
        ("620100", "Hardware", "2201", "Hardware"),
    ]
    cc_sheet = [
        CC,
        ("4520", "Enterprise applications", "APP", "Applications"),
        ("4520", "Enterprise apps", "APP", "Applications"),
    ]
    found = exceptions_of(
        read(xlsx_file({"Spend": spend, "GL": gl_sheet, "CC": cc_sheet}))
    )
    assert found["listed_with_different_values"] == {
        "type": "listed_with_different_values",
        "blocks_approval": True,
        "amount": None,
        "items": [
            {
                "rows": [["GL", 2], ["GL", 3]],
                "amount": None,
                "mapping": "gl",
                "code": "610100",
            }
        ],
    }
    assert found["sheet_and_spend_disagree"]["blocks_approval"] is False
    assert found["sheet_and_spend_disagree"]["items"] == [
        {
            "rows": [["GL", 4], ["Spend", 3]],
            "amount": None,
            "mapping": "gl",
            "code": "620100",
        }
    ]
    assert found["names_differ"]["items"] == [
        {
            "rows": [["CC", 2], ["CC", 3]],
            "amount": None,
            "mapping": "cc",
            "code": "4520",
            "field": "cc_name",
        }
    ]


def test_a_duplicate_with_another_amount_is_listed_with_both_amounts() -> None:
    """Verify only the duplicate whose amount differs from the posting's is
    listed, with the posting's amount.
    """
    second = line(DocumentNo="2", Amount_in_LC="200.000")
    result = read(csv_file(LINE, second, total_row("1200.000")))
    duplicates = {("", 2): Decimal("1000.000"), ("", 3): Decimal("250.000")}
    found = exceptions_of(result, duplicates)
    assert found == {
        "duplicate_different_amount": {
            "type": "duplicate_different_amount",
            "blocks_approval": False,
            "amount": "200.000",
            "items": [
                {"rows": [["", 3]], "amount": "200.000", "posting_amount": "250.000"}
            ],
        }
    }


def test_a_month_with_no_rows_to_commit_is_listed() -> None:
    """Verify February, between a January and a March row, is listed, and a
    month whose only row is a duplicate counts as empty.
    """
    rows = [
        line(Pstng_Date="15.01.2026"),
        line(DocumentNo="2", Pstng_Date="10.02.2026"),
        line(DocumentNo="3", Pstng_Date="03.03.2026"),
    ]
    result = read(csv_file(*rows, total_row("3000.000")))
    months = ["2026-01", "2026-02", "2026-03"]
    assert exceptions_of(result, months=months) == {}
    found = exceptions_of(result, {("", 3): Decimal("1000.000")}, months)
    assert found["month_without_rows"]["items"] == [
        {"rows": [], "amount": None, "month": "2026-02"}
    ]


def test_exceptions_are_in_the_table_s_order() -> None:
    """Verify in_order sorts by ENG-02 11.2's table, whatever order they came."""
    found = [
        exception(kind, [])
        for kind in ("month_without_rows", "other_row", "no_control_total")
    ]
    assert [e["type"] for e in in_order(found)] == [
        "no_control_total",
        "other_row",
        "month_without_rows",
    ]


def test_months_run_from_the_first_month_to_the_last() -> None:
    """Verify the months replaced cross a year end, and one month is one."""
    nov, feb = datetime.date(2025, 11, 30), datetime.date(2026, 2, 1)
    assert months_between(nov, feb) == ["2025-11", "2025-12", "2026-01", "2026-02"]
    assert months_between(feb, feb) == ["2026-02"]
    assert first_of_next_month(datetime.date(2025, 12, 31)) == datetime.date(2026, 1, 1)


def test_value_types_are_listed_only_with_a_value_type_column() -> None:
    """Verify each value type among the rows to commit, as written, with its
    rows, amount and class, and none without the column.
    """
    data = csv_file(
        (*LINE, "Actual"),
        (*line(DocumentNo="2", Amount_in_LC="10.000"), "Commitment"),
        (*line(DocumentNo="3", Amount_in_LC="5.000"), "Plan"),
        (*line(DocumentNo="4", Amount_in_LC="20.000"), "Actual"),
        header=WITH_TYPE,
    )
    result = read(data, actual_spend_rule=RULE)
    assert value_types(result, result.clean_rows) == [
        {"value_type": "Actual", "rows": 2, "amount": "1020.000", "class": "actual"},
        {
            "value_type": "Commitment",
            "rows": 1,
            "amount": "10.000",
            "class": "not_actual",
        },
        {"value_type": "Plan", "rows": 1, "amount": "5.000", "class": "unclassified"},
    ]
    plain = read(csv_file(LINE))
    assert value_types(plain, plain.clean_rows) == []


def test_actual_spend_and_the_rest_split_the_rows_to_commit() -> None:
    """Verify an unclassified row isn't actual spend (ENG-02 5)."""
    data = csv_file(
        (*LINE, "Actual"),
        (*line(DocumentNo="2", Amount_in_LC="10.000"), "Commitment"),
        (*line(DocumentNo="3", Amount_in_LC="5.000"), "Plan"),
        header=WITH_TYPE,
    )
    rows = read(data, actual_spend_rule=RULE).clean_rows
    assert actual_and_not(rows) == (Decimal("1000.000"), Decimal("15.000"))
