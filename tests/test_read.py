"""Read and clean: sorting a file's rows, the total row, and the mapping rows
(ENG-02 5, 7.2 and 9.1). No database.
"""

import datetime
import io
from decimal import Decimal

import pytest
import xlsxwriter

from ids_audit.pipeline.read import read_file
from ids_audit.pipeline.results import (
    ANOTHER_CURRENCY,
    INVALID_DATE,
    LISTED_WITH_DIFFERENT_VALUES,
    MORE_THAN_3_DECIMALS,
    NAMES_DIFFER,
    SHEET_AND_SPEND_DISAGREE,
    UNREADABLE_AMOUNT,
    UNREADABLE_CODE,
    UNREADABLE_FISCAL_YEAR,
    CcMapRow,
    GlMapRow,
    MappingIssue,
    ReadResult,
)
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


# Spend rows


def test_a_clean_row_holds_every_field_cleaned() -> None:
    """Verify a spend row's fields are cleaned as ENG-02 5 says, the amount
    kept to 3 decimal places and the item text as written.
    """
    arabic = "تجديد ترخيص"
    result = read(csv_file(line(Text=f" {arabic} ", Amount_in_LC="1,200.5")))
    (clean,) = result.clean_rows
    assert clean.sheet == ""
    assert clean.row_number == 2
    assert clean.company_code == "1000"
    assert clean.fiscal_year == 2025
    assert clean.document_number == "5100000101"
    assert clean.line_item == "2"
    assert clean.line_key == "1000-2025-5100000101-2"
    assert clean.posting_date == datetime.date(2025, 7, 8)
    assert clean.gl_code == "610100"
    assert clean.cost_center_code == "4520"
    assert clean.value_type == ""
    assert clean.is_actual is True
    assert str(clean.amount) == "1200.500"
    assert clean.currency == "OMR"
    assert clean.item_text == arabic


@pytest.mark.parametrize(
    ("changes", "reason", "amount", "in_reconciliation"),
    [
        pytest.param({"LCurr": "USD"}, ANOTHER_CURRENCY, "1000.000", False, id="USD"),
        pytest.param(
            {"G/L Acct": "#N/A"}, UNREADABLE_CODE, "1000.000", True, id="#N/A"
        ),
        pytest.param({"Cost Ctr": "45 20"}, UNREADABLE_CODE, "1000.000", True, id="cc"),
        pytest.param(
            {"Amount in LC": "12,34.5"}, UNREADABLE_AMOUNT, None, True, id="bad"
        ),
        pytest.param({"Amount in LC": " "}, UNREADABLE_AMOUNT, None, True, id="empty"),
        pytest.param(
            {"Amount in LC": "12.3456"}, MORE_THAN_3_DECIMALS, "12.3456", True, id="4dp"
        ),
        pytest.param(
            {"Pstng Date": "31.02.2026"}, INVALID_DATE, "1000.000", True, id="day"
        ),
        pytest.param({"Pstng Date": ""}, INVALID_DATE, "1000.000", True, id="no date"),
        pytest.param(
            {"Year": "FY26"}, UNREADABLE_FISCAL_YEAR, "1000.000", True, id="year"
        ),
    ],
)
def test_a_spend_row_is_skipped_with_its_reason(
    changes, reason, amount, in_reconciliation
) -> None:
    """Verify each skip reason in ENG-02 5, with the amount kept exactly as
    read whenever it can be read, and only another currency left out of
    reconciliation.
    """
    result = read(csv_file(line(**changes)))
    assert result.clean_rows == ()
    (skipped,) = result.skipped_rows
    assert (skipped.sheet, skipped.row_number, skipped.reason) == ("", 2, reason)
    assert skipped.amount == (None if amount is None else Decimal(amount))
    assert str(skipped.amount) == str(amount)
    assert skipped.in_reconciliation is in_reconciliation
    assert skipped.cells == line(**changes)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        pytest.param({"LCurr": "USD", "G/L Acct": "#N/A"}, ANOTHER_CURRENCY, id="1 2"),
        pytest.param(
            {"G/L Acct": "#N/A", "Amount in LC": "x"}, UNREADABLE_CODE, id="2 3"
        ),
        pytest.param(
            {"Amount in LC": "x", "Pstng Date": "31.02.2026"},
            UNREADABLE_AMOUNT,
            id="3 5",
        ),
        pytest.param(
            {"Amount in LC": "1.2345", "Pstng Date": "x"},
            MORE_THAN_3_DECIMALS,
            id="4 5",
        ),
        pytest.param({"Pstng Date": "x", "Year": "FY26"}, INVALID_DATE, id="5 6"),
    ],
)
def test_a_row_with_two_problems_gets_the_first_reason(changes, reason) -> None:
    """Verify the reasons are tried in the order of ENG-02 5's table."""
    (skipped,) = read(csv_file(line(**changes))).skipped_rows
    assert skipped.reason == reason


def test_a_row_repeating_a_line_key_is_skipped_naming_the_first() -> None:
    """Verify the second row with the same line key is skipped as "Repeats
    row 2" and still counts in reconciliation (ENG-02 7.2).
    """
    other_line = line(DocumentNo="5100000102")
    unreadable = line(DocumentNo="5100000103", Amount_in_LC="x")
    result = read(csv_file(LINE, other_line, LINE, unreadable))
    assert [clean.row_number for clean in result.clean_rows] == [2, 3]
    repeat, after = result.skipped_rows
    assert (repeat.row_number, repeat.reason) == (4, "Repeats row 2")
    assert (repeat.amount, repeat.in_reconciliation) == (Decimal("1000.000"), True)
    assert (after.row_number, after.reason) == (5, UNREADABLE_AMOUNT)


def test_rows_without_a_full_line_key_are_never_repeats() -> None:
    """Verify a row missing a part of the line key has none, so two such rows
    are both clean.
    """
    no_document = line(DocumentNo="")
    result = read(csv_file(no_document, no_document))
    assert [clean.line_key for clean in result.clean_rows] == ["", ""]
    assert result.skipped_rows == ()


def test_the_same_line_from_excel_numbers_and_csv_text_has_one_line_key() -> None:
    """Verify number cells such as 610100.0 and 2025.0 give the same codes and
    line key as the CSV's text (ENG-02 5, Tested by).
    """
    numbers = (1000, 610100, 5100000101, 2, "08.07.2025", 2025, 4520, "x", 1000, "OMR")
    from_excel = read(xlsx_file({"Spend": [HEADER, numbers]})).clean_rows[0]
    from_csv = read(csv_file(LINE)).clean_rows[0]
    assert from_excel.line_key == from_csv.line_key == "1000-2025-5100000101-2"
    assert (from_excel.gl_code, from_excel.cost_center_code) == ("610100", "4520")
    assert from_excel.amount == from_csv.amount


def test_the_period_runs_from_the_first_to_the_last_clean_date() -> None:
    """Verify period_start and period_end come from clean rows only, so a
    skipped row's date doesn't widen the period.
    """
    result = read(
        csv_file(
            line(Pstng_Date="15.03.2026"),
            line(DocumentNo="2", Pstng_Date="01.02.2026"),
            line(DocumentNo="3", Pstng_Date="01.01.2026", Amount_in_LC="x"),
        )
    )
    assert (result.period_start, result.period_end) == (
        datetime.date(2026, 2, 1),
        datetime.date(2026, 3, 15),
    )


def test_a_file_with_no_clean_rows_has_no_period() -> None:
    """Verify a file whose only spend row is skipped has no period."""
    result = read(csv_file(line(Amount_in_LC="x")))
    assert (result.period_start, result.period_end) == (None, None)


def test_without_a_date_column_the_date_comes_from_year_and_period() -> None:
    """Verify fiscal year and period give the date when there's no date
    column, and a period that isn't one makes the date invalid.
    """
    header = ("G/L Acct", "Cost Ctr", "Year", "Period", "Amount in LC")
    result = read(
        csv_file(
            ("610100", "4520", "2026", "3", "1.000"),
            ("610100", "4520", "2026", "13", "2.000"),
            ("610100", "4520", "2026", "17", "3.000"),
            ("610100", "4520", "FY26", "3", "4.000"),
            header=header,
        )
    )
    dates = [clean.posting_date for clean in result.clean_rows]
    assert dates == [datetime.date(2026, 3, 1), datetime.date(2026, 12, 31)]
    assert [skipped.reason for skipped in result.skipped_rows] == [INVALID_DATE] * 2


# Currency, value type and decimal separator


def test_the_currency_read_is_the_local_currency() -> None:
    """Verify a USD invoice whose local currency is OMR counts: a Currency
    column, the document currency, isn't read (ENG-02 4.4).
    """
    header = (*HEADER, "Currency")
    result = read(csv_file((*LINE, "USD"), header=header))
    assert len(result.clean_rows) == 1
    assert result.column_layout[""]["columns"]["currency"] == "J"


@pytest.mark.parametrize("currency", ["", " omr "])
def test_an_empty_or_lower_case_local_currency_is_the_ruleset_s(currency) -> None:
    """Verify an empty currency cell means the ruleset's currency, and the
    currency is compared upper-cased.
    """
    (clean,) = read(csv_file(line(LCurr=currency))).clean_rows
    assert clean.currency == "OMR"


def test_value_types_are_actual_not_actual_or_unclassified() -> None:
    """Verify with a rule, ACTUAL matches Actual, Commitment isn't actual, and
    Plan is unclassified. With the rule null, all three are actual spend.
    """
    header = (*HEADER, "Value Type")
    data = csv_file(
        (*LINE, "ACTUAL "),
        (*line(DocumentNo="2"), "Commitment"),
        (*line(DocumentNo="3"), "Plan"),
        header=header,
    )
    rule = {"actual": ["Actual"], "not_actual": ["Commitment"]}
    with_rule = read(data, actual_spend_rule=rule).clean_rows
    assert [clean.is_actual for clean in with_rule] == [True, False, None]
    assert [clean.value_type for clean in with_rule] == ["ACTUAL", "Commitment", "Plan"]
    without_rule = read(data).clean_rows
    assert [clean.is_actual for clean in without_rule] == [True, True, True]


def test_the_decimal_separator_comes_from_the_ruleset() -> None:
    """Verify "1.234,500-" reads as -1234.500 with decimal_separator ",", and
    "12,400.000" can't be read with it.
    """
    data = csv_file(line(Amount_in_LC="1.234,500-"), line(Amount_in_LC="12,400.000"))
    result = read(data, decimal_separator=",")
    assert [clean.amount for clean in result.clean_rows] == [Decimal("-1234.500")]
    assert [skipped.reason for skipped in result.skipped_rows] == [UNREADABLE_AMOUNT]


# The total row and other rows


def test_the_total_row_gives_the_control_total_as_read() -> None:
    """Verify a row with Total, an amount and neither code is the total row,
    and its amount is kept unrounded (ENG-02 9.1).
    """
    total = ("Total", "", "", "", "", "", "", "", 1000.0000000001, "OMR")
    result = read(xlsx_file({"Spend": [HEADER, LINE, total]}))
    assert result.control_total == Decimal("1000.0000000001")
    assert result.control_total_row == 3
    assert result.other_rows == ()


@pytest.mark.parametrize("word", ["Grand Total", "TOTAL:", "total"])
def test_total_is_matched_as_a_whole_word_in_any_case(word) -> None:
    """Verify Grand Total, TOTAL: and total each mark the total row."""
    total = (word, "", "", "", "", "", "", "", "1,000.000", "OMR")
    assert read(csv_file(LINE, total)).control_total == Decimal("1000.000")


def test_a_subtotal_or_a_total_with_a_code_is_an_other_row() -> None:
    """Verify Subtotal isn't the word Total, and a Total row with a GL account
    isn't the total row: both are other rows, with their amounts.
    """
    subtotal = ("Subtotal", "", "", "", "", "", "", "", "1,000.000", "OMR")
    gl_total = ("Total", "610100", "", "", "", "", "", "", "1,000.000", "OMR")
    result = read(csv_file(LINE, subtotal, gl_total))
    assert result.control_total is None
    assert [(o.row_number, o.amount) for o in result.other_rows] == [
        (3, Decimal("1000.000")),
        (4, Decimal("1000.000")),
    ]


def test_a_total_row_needs_an_amount() -> None:
    """Verify a Total row whose amount can't be read is an other row, with no
    amount, and the file has no control total.
    """
    total = ("Total", "", "", "", "", "", "", "", "n/a", "OMR")
    result = read(csv_file(LINE, total))
    assert result.control_total is None
    assert [(o.row_number, o.amount) for o in result.other_rows] == [(3, None)]


def test_only_the_last_total_below_the_last_spend_row_counts() -> None:
    """Verify of two Total rows below the last spend row, the last is used,
    and a Total row above it is an other row (ENG-02 9.1).
    """
    total = ("Total", "", "", "", "", "", "", "", "{}", "OMR")

    def total_of(amount: str) -> tuple:
        return tuple(cell.format(amount) for cell in total)

    label = ("Checked", "", "", "", "", "", "", "", "", "")
    result = read(
        csv_file(
            total_of("5.000"),
            LINE,
            total_of("6.000"),
            label,
            total_of("7.000"),
        )
    )
    assert (result.control_total, result.control_total_row) == (Decimal("7.000"), 6)
    assert [o.row_number for o in result.other_rows] == [2, 4, 5]
    above_only = read(csv_file(total_of("5.000"), LINE))
    assert above_only.control_total is None


def test_blank_rows_are_left_out_and_every_other_row_is_listed() -> None:
    """Verify a blank row, even of spaces, isn't recorded, and a subtotal and
    a label are other rows, the label with no amount.
    """
    blank = ("",) * len(HEADER)
    spaces = ("  ",) * len(HEADER)
    subtotal = ("", "0000610100", "", "", "", "", "", "", "1,000.000", "OMR")
    label = ("Spend to date", "", "", "", "", "", "", "", "", "")
    result = read(csv_file(LINE, blank, spaces, subtotal, label))
    assert [(o.row_number, o.amount) for o in result.other_rows] == [
        (5, Decimal("1000.000")),
        (6, None),
    ]
    assert result.other_rows[1].cells == label


# Mapping rows (ENG-02 5, Mapping rows)

SPEND = [HEADER, LINE]
GL = ("G/L Account", "G/L Account Name", "Commitment Item", "Commitment Item Name")
CC = ("Cost Center", "Cost Center Name", "Department Code", "Department Name")


def test_mapping_rows_are_cleaned_and_listed_without_ci_kept() -> None:
    """Verify mapping codes stored as numbers match spend codes written with
    zeros, and a GL account listed with no commitment item keeps no name for
    it.
    """
    result = read(
        xlsx_file(
            {
                "Spend": SPEND,
                "GL": [GL, (610100, "Software", 2101, "Software and licences")],
                "CC": [CC, (4520, "Enterprise applications", "app", "Applications")],
            }
        )
    )
    assert result.gl_map == (
        GlMapRow("610100", "Software", "2101", "Software and licences"),
    )
    assert result.cc_map == (
        CcMapRow("4520", "Enterprise applications", "APP", "Applications"),
    )
    listed_without = read(
        xlsx_file({"Spend": SPEND, "GL": [GL, ("650200", "Travel", None, "Ignored")]})
    )
    assert listed_without.gl_map == (GlMapRow("650200", "Travel", "", ""),)


def test_an_exact_repeat_keeps_one_row() -> None:
    """Verify a GL account listed twice with the same commitment item gives
    one row and no issue.
    """
    rows = [
        GL,
        ("610100", "Software", "2101", "Licences"),
        ("0610100", "Software", "2101", "Licences"),
    ]
    result = read(xlsx_file({"Spend": SPEND, "GL": rows}))
    assert result.gl_map == (GlMapRow("610100", "Software", "2101", "Licences"),)
    assert result.mapping_issues == ()


def test_a_repeat_with_a_different_value_keeps_the_first_and_blocks() -> None:
    """Verify 610100 listed with 2101 and then 2201 keeps 2101, and the issue
    names every row of 610100 and blocks approval.
    """
    rows = [
        GL,
        ("610100", "Software", "2101", "Licences"),
        ("610100", "Software", "2101", "Licences"),
        ("610100", "Software", "2201", "Hardware"),
    ]
    result = read(xlsx_file({"Spend": SPEND, "GL": rows}))
    assert result.gl_map == (GlMapRow("610100", "Software", "2101", "Licences"),)
    (issue,) = result.mapping_issues
    assert issue == MappingIssue(
        LISTED_WITH_DIFFERENT_VALUES,
        "gl_code",
        "610100",
        (("GL", 2), ("GL", 3), ("GL", 4)),
    )
    assert issue.blocks_approval


def test_an_unreadable_mapping_code_is_skipped_and_doesnt_reconcile() -> None:
    """Verify #N/A as a cost center on the mapping sheet is skipped with no
    amount, outside reconciliation, and a row with no code is an other row.
    """
    rows = [
        CC,
        ("#N/A", "Unknown", "APP", ""),
        ("", "", "", ""),
        ("", "Updated 2026", "", ""),
    ]
    result = read(xlsx_file({"Spend": SPEND, "CC": rows}))
    assert result.cc_map == ()
    (skipped,) = result.skipped_rows
    assert (skipped.sheet, skipped.row_number, skipped.reason) == (
        "CC",
        2,
        UNREADABLE_CODE,
    )
    assert (skipped.amount, skipped.in_reconciliation) == (None, False)
    assert [(o.sheet, o.row_number) for o in result.other_rows] == [("CC", 4)]


def test_an_unreadable_value_code_skips_the_mapping_row() -> None:
    """Verify a commitment item that can't be read skips the row too."""
    rows = [GL, ("610100", "Software", "#N/A", "")]
    result = read(xlsx_file({"Spend": SPEND, "GL": rows}))
    assert result.gl_map == ()
    assert [s.reason for s in result.skipped_rows] == [UNREADABLE_CODE]


def test_a_code_given_two_names_keeps_the_first_given() -> None:
    """Verify commitment item 2101, named two ways, has its first name on
    every row, with an issue that doesn't block approval. An empty name
    doesn't count.
    """
    rows = [
        GL,
        ("610100", "", "2101", ""),
        ("610200", "Support", "2101", "Software and licences"),
        ("610300", "Cloud", "2101", "Software"),
        ("610100", "Licences", "2101", ""),
    ]
    result = read(xlsx_file({"Spend": SPEND, "GL": rows}))
    assert {row.ci_name for row in result.gl_map} == {"Software and licences"}
    assert result.gl_map[0] == GlMapRow(
        "610100", "Licences", "2101", "Software and licences"
    )
    (issue,) = result.mapping_issues
    assert issue == MappingIssue(
        NAMES_DIFFER, "ci_name", "2101", (("GL", 3), ("GL", 4))
    )
    assert not issue.blocks_approval


def test_spend_sheet_columns_add_the_codes_the_mapping_sheet_lacks() -> None:
    """Verify a spend sheet's Commitment Item column adds 640200, which the GL
    mapping sheet lacks, with the spend sheet's names. 610100 keeps the
    sheet's 2101, and the spend row saying 2201 is an issue (ENG-02 4.2).
    """
    header = (*HEADER, "Commitment Item", "Cmmt Item Name")
    spend = [
        header,
        (*LINE, "2201", "Hardware"),
        (
            *line(**{"G/L Acct": "640200"}, DocumentNo="2"),
            "2401",
            "Professional services",
        ),
        (*line(DocumentNo="3"), "", ""),
    ]
    gl_sheet = [GL, ("610100", "Software", "2101", "Licences")]
    result = read(xlsx_file({"Spend": spend, "GL": gl_sheet}))
    assert result.gl_map == (
        GlMapRow("610100", "Software", "2101", "Licences"),
        GlMapRow("640200", "", "2401", "Professional services"),
    )
    (issue,) = result.mapping_issues
    assert issue == MappingIssue(
        SHEET_AND_SPEND_DISAGREE, "gl_code", "610100", (("GL", 2), ("Spend", 2))
    )
    assert not issue.blocks_approval


def test_a_spend_row_is_compared_with_the_row_the_sheet_keeps() -> None:
    """Verify a spend row agreeing with the first of two conflicting sheet rows
    raises no disagreement, since the first is the row kept.
    """
    header = (*HEADER, "Commitment Item")
    gl_sheet = [
        GL,
        ("610100", "Software", "2101", "Licences"),
        ("610100", "Software", "2201", "Hardware"),
    ]
    result = read(xlsx_file({"Spend": [header, (*LINE, "2101")], "GL": gl_sheet}))
    assert [issue.kind for issue in result.mapping_issues] == [
        LISTED_WITH_DIFFERENT_VALUES
    ]


def test_spend_rows_giving_one_code_two_values_block() -> None:
    """Verify a GL account found only on spend rows, with two commitment
    items, is a code listed with different values. A department column builds
    the cost center mapping the same way.
    """
    header = (*HEADER, "Commitment Item", "Department")
    spend = [
        header,
        (*LINE, "2401", "APP"),
        (*line(DocumentNo="2"), "2501", "APP"),
    ]
    result = read(xlsx_file({"Spend": spend}))
    assert [issue.kind for issue in result.mapping_issues] == [
        LISTED_WITH_DIFFERENT_VALUES
    ]
    assert result.gl_map == (GlMapRow("610100", "", "2401", ""),)
    assert result.cc_map == (CcMapRow("4520", "", "APP", ""),)


def test_a_csv_s_commitment_item_column_builds_no_mapping() -> None:
    """Verify a text file never has a mapping of its own (ENG-02 4.1)."""
    header = (*HEADER, "Commitment Item")
    result = read(csv_file((*LINE, "2401"), header=header))
    assert (result.gl_map, result.cc_map) == ((), ())
