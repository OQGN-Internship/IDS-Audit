"""Finding each sheet's header row, columns and type (ENG-02 4.2 to 4.5). No
database.
"""

from pathlib import Path

import pytest

from ids_audit.pipeline.columns import (
    CC_MAP,
    GL_MAP,
    SPEND,
    ColumnProblem,
    NeedsColumns,
    column_letter,
    find_header_row,
    find_layouts,
    header_lookup,
    normalize_header,
)
from ids_audit.pipeline.readers import FileUnreadable, Sheet, read_calamine
from ids_audit.pipeline.ruleset import DEFAULT_SETTINGS, HEADER_NAMES

FIXTURES_DIR = Path(__file__).parent / "fixtures"
NAMES = header_lookup(HEADER_NAMES)

SPEND_HEADER = ("G/L Account", "Cost Center", "Posting Date", "Amount")
GL_HEADER = ("G/L Account", "G/L Account Name", "Commitment Item")
CC_HEADER = ("Cost Center", "Department Code", "Department Name")


def sheet(name: str, *rows: tuple) -> Sheet:
    """Return a sheet with the given rows, row 1 first."""
    return Sheet(name, tuple(rows))


def layouts(*sheets: Sheet, is_text: bool = False, **settings) -> dict:
    """Run find_layouts with the default settings, changed by settings."""
    return find_layouts(sheets, {**DEFAULT_SETTINGS, **settings}, is_text=is_text)


def problems(*sheets: Sheet, is_text: bool = False, **settings) -> set[ColumnProblem]:
    """Return the column check's problems, which must stop the upload."""
    with pytest.raises(NeedsColumns) as stopped:
        layouts(*sheets, is_text=is_text, **settings)
    return set(stopped.value.problems)


# Headers


@pytest.mark.parametrize(
    ("header", "normalized"),
    [
        ("G/L Account", "glaccount"),
        ("gl account", "glaccount"),
        ("Doc. No.", "docno"),
        ("Val/COArea Crcy", "valcoareacrcy"),
        (2026.0, "2026"),
    ],
)
def test_a_header_is_lower_case_letters_and_digits(header, normalized) -> None:
    """Verify headers lose case, spaces and punctuation before comparing."""
    assert normalize_header(header) == normalized


def test_the_sample_header_row_is_row_4_below_two_title_lines() -> None:
    """Verify the Transactions header is found on row 4, and the title lines
    above it don't count (ENG-02 4.3).
    """
    transactions = read_calamine((FIXTURES_DIR / "sample.xlsx").read_bytes())[1]
    row_number, matches = find_header_row(transactions, NAMES)
    assert row_number == 4
    assert matches[1] == "gl_code"
    assert matches[9] == "cost_center_code"


def test_the_row_matching_most_headers_wins_and_the_higher_on_a_tie() -> None:
    """Verify a title line naming one header loses to the header row, and of
    two rows matching as many, the first wins.
    """
    rows = (("Amount",), ("Account", "Amount"), ("Cost Center", "Amount"))
    assert find_header_row(sheet("S", *rows), NAMES)[0] == 2


def test_a_header_matches_only_as_a_whole_cell() -> None:
    """Verify "Account balance" doesn't match "Account", so a sentence in a
    README sheet is never a header.
    """
    assert find_header_row(sheet("README", ("Account balance",)), NAMES) is None


def test_a_header_below_row_25_isnt_found() -> None:
    """Verify only the first 25 rows are searched (ENG-02 4.3)."""
    blank = ("",) * 4
    assert find_header_row(sheet("S", *[blank] * 24, SPEND_HEADER), NAMES)[0] == 25
    assert find_header_row(sheet("S", *[blank] * 25, SPEND_HEADER), NAMES) is None


# Sheet types


def test_the_sample_has_a_spend_sheet_and_both_mapping_sheets() -> None:
    """Verify the README sheet is ignored, and each other sheet is found with
    its header row and columns (ENG-02 4.2, 12.3).
    """
    sheets = read_calamine((FIXTURES_DIR / "sample.xlsx").read_bytes())
    found = layouts(*sheets)
    assert {kind: layout.sheet.name for kind, layout in found.items()} == {
        SPEND: "Transactions",
        GL_MAP: "GL Mapping",
        CC_MAP: "Cost Center Mapping",
    }
    assert found[SPEND].header_row == 4
    assert found[SPEND].letters() == {
        "company_code": "A",
        "gl_code": "B",
        "document_number": "C",
        "line_item": "D",
        "posting_date": "G",
        "fiscal_year": "H",
        "period": "I",
        "cost_center_code": "J",
        "item_text": "L",
        "amount": "M",
        "currency": "N",
    }
    assert found[GL_MAP].letters() == {
        "gl_code": "A",
        "gl_name": "B",
        "ci_code": "C",
        "ci_name": "D",
    }


def test_a_sheet_with_an_amount_and_one_code_is_ignored() -> None:
    """Verify a sheet with a GL account and an amount but no cost center is
    neither spend nor a GL mapping, so it's ignored, and alone it leaves the
    file with no spend sheet.
    """
    totals = sheet("Totals", ("G/L Account", "Amount"))
    assert set(layouts(sheet("S", SPEND_HEADER), totals)) == {SPEND}
    with pytest.raises(FileUnreadable, match="no spend sheet"):
        layouts(totals)


def test_a_text_file_without_both_codes_has_no_spend_sheet() -> None:
    """Verify a text file is never a mapping: with a GL account and no cost
    center, it has no spend sheet (ENG-02 4.1).
    """
    with pytest.raises(FileUnreadable, match="no spend sheet"):
        layouts(sheet("", GL_HEADER), is_text=True)


def test_two_spend_sheets_fail_naming_both() -> None:
    """Verify choosing between two spend sheets isn't guessed (ENG-02 4.2)."""
    with pytest.raises(FileUnreadable, match="2 spend sheets: Jan and Feb"):
        layouts(sheet("Jan", SPEND_HEADER), sheet("Feb", SPEND_HEADER))


def test_two_gl_mapping_sheets_fail() -> None:
    """Verify at most one GL mapping sheet is allowed."""
    with pytest.raises(FileUnreadable, match="2 GL mapping sheets: GL and GL old"):
        layouts(
            sheet("S", SPEND_HEADER), sheet("GL", GL_HEADER), sheet("GL old", GL_HEADER)
        )


# The column check


def test_department_code_and_department_are_the_code_and_the_name() -> None:
    """Verify of two columns for one subject, the one ending in "code" is the
    code and the other is the name (ENG-02 4.4).
    """
    cc_sheet = sheet("CC", ("Cost Center", "Department", "Department Code"))
    found = layouts(sheet("S", SPEND_HEADER), cc_sheet)
    assert found[CC_MAP].columns == {
        "cost_center_code": 0,
        "dept_name": 1,
        "dept_code": 2,
    }


def test_a_spend_sheet_without_an_amount_needs_columns() -> None:
    """Verify a missing required field stops the upload (ENG-02 4.5)."""
    found = problems(sheet("S", ("G/L Account", "Cost Center", "Posting Date")))
    assert found == {ColumnProblem("S", "amount", ())}


def test_two_columns_for_one_field_need_columns() -> None:
    """Verify Posting Date and Date both naming the posting date stops the
    upload, naming both columns.
    """
    header = ("G/L Account", "Cost Center", "Posting Date", "Amount", "Date")
    assert problems(sheet("S", header)) == {
        ColumnProblem("S", "posting_date", ("C", "E"))
    }


def test_document_date_isnt_the_posting_date() -> None:
    """Verify a file with Posting Date and Document Date uses Posting Date."""
    header = ("G/L Account", "Cost Center", "Document Date", "Posting Date", "Amount")
    assert layouts(sheet("S", header))[SPEND].columns["posting_date"] == 3


def test_fiscal_year_and_period_stand_in_for_the_posting_date() -> None:
    """Verify the posting date is required unless fiscal year and period are
    both present (ENG-02 4.4).
    """
    with_both = ("G/L Account", "Cost Center", "Amount", "Year", "Period")
    assert "posting_date" not in layouts(sheet("S", with_both))[SPEND].columns
    year_only = ("G/L Account", "Cost Center", "Amount", "Year")
    assert problems(sheet("S", year_only)) == {ColumnProblem("S", "posting_date", ())}


def test_the_value_type_is_required_only_with_a_rule() -> None:
    """Verify a file with no value-type column is read when actual_spend_rule
    is null, and needs columns when it isn't.
    """
    rule = {"actual": ["Actual"], "not_actual": []}
    assert layouts(sheet("S", SPEND_HEADER))
    found = problems(sheet("S", SPEND_HEADER), actual_spend_rule=rule)
    assert found == {ColumnProblem("S", "value_type", ())}


def test_a_gl_mapping_sheet_without_a_commitment_item_needs_columns() -> None:
    """Verify a mapping sheet's required fields are checked too, so a
    commitment item header that isn't recognised stops the upload.
    """
    gl_sheet = sheet("GL", ("G/L Account", "G/L Account Name", "Budget Line"))
    found = problems(sheet("S", SPEND_HEADER), gl_sheet)
    assert found == {ColumnProblem("GL", "ci_code", ())}


def test_a_header_added_to_the_ruleset_is_recognised() -> None:
    """Verify header_names comes from the ruleset, not the code."""
    names = {**HEADER_NAMES, "ci_code": [*HEADER_NAMES["ci_code"], "Budget Line"]}
    gl_sheet = sheet("GL", ("G/L Account", "G/L Account Name", "Budget Line"))
    found = layouts(sheet("S", SPEND_HEADER), gl_sheet, header_names=names)
    assert found[GL_MAP].columns["ci_code"] == 2


def test_every_sheet_s_problems_are_reported_together() -> None:
    """Verify one upload lists the problems of every classified sheet."""
    found = problems(
        sheet("S", ("G/L Account", "Cost Center", "Posting Date")),
        sheet("CC", ("Cost Center", "Cost Center Name")),
    )
    assert found == {
        ColumnProblem("S", "amount", ()),
        ColumnProblem("CC", "dept_code", ()),
    }


def test_a_text_file_ignores_its_mapping_columns() -> None:
    """Verify a CSV's commitment item columns are neither read nor checked
    (ENG-02 4.1), while an Excel spend sheet's are.
    """
    header = (*SPEND_HEADER, "Commitment Item", "Cmmt Item")
    found = layouts(sheet("", header), is_text=True)
    assert "ci_code" not in found[SPEND].columns
    assert problems(sheet("S", header)) == {ColumnProblem("S", "ci_code", ("E", "F"))}


def test_the_problem_message_names_the_sheet_and_columns() -> None:
    """Verify the message the uploader sees says what's wrong and where."""
    assert str(ColumnProblem("S", "amount", ())) == (
        "In sheet S, no column matches amount."
    )
    assert str(ColumnProblem("", "posting_date", ("C", "E"))) == (
        "In the file, columns C and E both match posting_date."
    )


@pytest.mark.parametrize(
    ("index", "letters"), [(0, "A"), (25, "Z"), (26, "AA"), (701, "ZZ"), (702, "AAA")]
)
def test_column_letters_are_as_excel_shows_them(index, letters) -> None:
    """Verify column numbers turn into Excel's letters."""
    assert column_letter(index) == letters
