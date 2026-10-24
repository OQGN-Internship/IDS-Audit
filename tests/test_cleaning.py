"""Cleaning one cell's value, field by field (ENG-02 5). No database."""

import datetime
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ids_audit.pipeline.cleaning import (
    cell_text,
    classify_value_type,
    clean_amount,
    clean_code,
    clean_date,
    clean_whole_number,
    date_from_period,
    has_more_than_3_decimals,
)

RULE = {"actual": ["Actual"], "not_actual": ["Commitment"]}


# Codes


@pytest.mark.parametrize(
    ("cell", "code"),
    [
        ("0000610100", "610100"),
        (4510.0, "4510"),
        (610100, "610100"),
        (" ab12 ", "AB12"),
        ("IT-01_A.2", "IT-01_A.2"),
        ("000", "0"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_a_code_is_trimmed_upper_cased_and_loses_leading_zeros(cell, code) -> None:
    """Verify the code rule's examples, and that an empty cell gives ""."""
    assert clean_code(cell) == code


@pytest.mark.parametrize(
    "cell",
    [
        pytest.param("#N/A", id="#N/A as text"),
        pytest.param("N/A", id="slash"),
        pytest.param("61 0100", id="space inside"),
        pytest.param("٦١٠١٠٠", id="arabic digits"),
        pytest.param(4510.5, id="number not whole"),
        pytest.param(True, id="true"),
        pytest.param(datetime.date(2026, 3, 1), id="date"),
    ],
)
def test_an_unreadable_code_raises(cell) -> None:
    """Verify a code with anything but English letters, digits, "-", "_" and
    ".", or a number that isn't whole, is unreadable.
    """
    with pytest.raises(ValueError):
        clean_code(cell)


@given(st.text(alphabet="0123456789abcXYZ-_.", min_size=1, max_size=12))
def test_cleaning_a_clean_code_leaves_it_unchanged(text) -> None:
    """Verify cleaning is idempotent: a code cleaned twice is cleaned once."""
    assert clean_code(clean_code(text)) == clean_code(text)


# Fiscal year and period


@pytest.mark.parametrize(
    ("cell", "number"), [(2026.0, 2026), (2026, 2026), ("003", 3), (" 12 ", 12)]
)
def test_a_fiscal_year_or_period_is_a_whole_number(cell, number) -> None:
    """Verify 2026.0 becomes 2026 and "003" becomes 3, each an int."""
    whole = clean_whole_number(cell)
    assert (whole, type(whole)) == (number, int)


def test_an_empty_fiscal_year_is_none() -> None:
    """Verify an empty cell gives None, not 0."""
    assert clean_whole_number("") is None


@pytest.mark.parametrize("cell", ["FY26", 2026.5, "-3", True])
def test_a_fiscal_year_that_isnt_a_whole_number_raises(cell) -> None:
    """Verify anything but a whole number is unreadable."""
    with pytest.raises(ValueError):
        clean_whole_number(cell)


# Amounts


@pytest.mark.parametrize(
    ("text", "amount"),
    [
        ("1,234.500-", "-1234.500"),
        ("(300.000)", "-300.000"),
        ("OMR 1,200", "1200"),
        ("1,200 OMR", "1200"),
        ("-45.5", "-45.5"),
        ("1,234,567,890.123", "1234567890.123"),
        ("1 234 567.890", "1234567.890"),
        ("1\u00a0234.5", "1234.5"),
        ("1\u202f234.5", "1234.5"),
        ("12.3456", "12.3456"),
    ],
)
def test_a_text_amount_with_a_decimal_point(text, amount) -> None:
    """Verify the amount rule's examples with decimal_separator ".", read
    exactly and not rounded.
    """
    assert str(clean_amount(text, ".")) == amount


@pytest.mark.parametrize(
    ("text", "amount"),
    [("1.234,500-", "-1234.500"), ("1 234,5", "1234.5"), ("12,5", "12.5")],
)
def test_a_text_amount_with_a_decimal_comma(text, amount) -> None:
    """Verify decimal_separator "," reads "1.234,500-" as -1234.500."""
    assert str(clean_amount(text, ",")) == amount


@pytest.mark.parametrize(
    ("text", "decimal_separator"),
    [
        pytest.param("12,34.5", ".", id="groups not of three"),
        pytest.param("1,234 567", ".", id="two kinds of grouping"),
        pytest.param("12,400.000", ",", id="point after a decimal comma"),
        pytest.param("1200.", ".", id="no digit after the point"),
        pytest.param(".5", ".", id="no digit before the point"),
        pytest.param("(1,200-)", ".", id="two signs"),
        pytest.param("--5", ".", id="two minuses"),
        pytest.param("+5", ".", id="plus"),
        pytest.param("-", ".", id="only a minus"),
        pytest.param("1,2OMR00", ".", id="letters inside"),
        pytest.param("١٢٣", ".", id="arabic digits"),
    ],
)
def test_an_unreadable_text_amount_raises(text, decimal_separator) -> None:
    """Verify anything outside the amount rule is unreadable, never guessed."""
    with pytest.raises(ValueError):
        clean_amount(text, decimal_separator)


def test_an_excel_number_goes_through_its_shortest_text() -> None:
    """Verify 1234.5 reads as 1234.5, not the float's binary value, and a
    whole number as itself.
    """
    assert clean_amount(1234.5, ".") == Decimal("1234.5")
    assert clean_amount(0.1 + 0.2, ".") == Decimal("0.30000000000000004")
    assert clean_amount(12400, ".") == Decimal(12400)
    # Excel can store minus zero, which is zero.
    assert str(clean_amount(-0.0, ".")) == "0.0"


def test_an_empty_amount_is_none() -> None:
    """Verify an empty cell gives None, so the caller decides what it means."""
    assert clean_amount("  ", ".") is None


@pytest.mark.parametrize(
    "cell",
    [
        pytest.param(True, id="true"),
        pytest.param(datetime.date(2026, 3, 1), id="date"),
        pytest.param("1,000,000,000,000,000", id="too large as text"),
        pytest.param(-1e15, id="too large as a number"),
    ],
)
def test_an_amount_that_isnt_one_or_doesnt_fit_raises(cell) -> None:
    """Verify a true or false cell, a date, and an amount numeric(18,3) can't
    hold are unreadable.
    """
    with pytest.raises(ValueError):
        clean_amount(cell, ".")


def test_the_largest_amount_that_fits_is_read() -> None:
    """Verify 999,999,999,999,999.999 still fits numeric(18,3)."""
    largest = "999,999,999,999,999.999"
    assert clean_amount(largest, ".") == Decimal("999999999999999.999")


@pytest.mark.parametrize(
    ("amount", "more"),
    [("12.3456", True), ("12.3450", False), ("12.345", False)],
)
def test_more_than_3_decimals_doesnt_count_zeros_at_the_end(amount, more) -> None:
    """Verify 12.3456 needs a fourth decimal place and 12.3450 doesn't."""
    assert has_more_than_3_decimals(Decimal(amount)) is more


BAISA_AMOUNTS = st.integers(min_value=-(10**18) + 1, max_value=10**18 - 1)


def sap_text(baisa: int, decimal_separator: str, grouping: str, sign: str, crcy: str):
    """Write an amount the way SAP or Excel can: three decimals, grouped by
    threes or not, with the sign as a trailing minus, a leading minus or
    brackets, and a currency before or after it, or none.
    """
    whole, fraction = divmod(abs(baisa), 1000)
    digits = str(whole)
    if grouping:
        groups = []
        while len(digits) > 3:
            digits, group = digits[:-3], digits[-3:]
            groups.insert(0, group)
        digits = grouping.join([digits, *groups])
    text = f"{digits}{decimal_separator}{fraction:03d}"
    if baisa < 0:
        text = {"trailing": f"{text}-", "leading": f"-{text}", "brackets": f"({text})"}[
            sign
        ]
    return {"": text, "before": f"OMR {text}", "after": f"{text} OMR"}[crcy]


@settings(max_examples=3000)
@given(
    BAISA_AMOUNTS,
    st.sampled_from([".", ","]),
    st.sampled_from(["none", "separator", " ", "\u00a0"]),
    st.sampled_from(["trailing", "leading", "brackets"]),
    st.sampled_from(["", "before", "after"]),
)
def test_any_amount_in_sap_text_reads_back_the_same(
    baisa, decimal_separator, grouping, sign, crcy
) -> None:
    """Verify thousands of random amounts, written as text in SAP's formats,
    each read back as the same Decimal (ENG-02 5, Tested by).
    """
    thousands = {"none": "", "separator": "," if decimal_separator == "." else "."}
    text = sap_text(
        baisa, decimal_separator, thousands.get(grouping, grouping), sign, crcy
    )
    assert clean_amount(text, decimal_separator) == Decimal(baisa) / 1000


@settings(max_examples=3000)
@given(st.integers(min_value=-(10**15) + 1, max_value=10**15 - 1))
def test_any_amount_in_an_excel_number_reads_back_the_same(baisa) -> None:
    """Verify an amount of up to 15 digits, stored as Excel stores a number
    (a float), reads back as the same Decimal.
    """
    amount = Decimal(baisa) / 1000
    assert clean_amount(float(amount), ".") == amount


# Dates


@pytest.mark.parametrize(
    ("cell", "date"),
    [
        ("03.04.2026", datetime.date(2026, 4, 3)),
        ("03/04/2026", datetime.date(2026, 4, 3)),
        ("03-04-2026", datetime.date(2026, 4, 3)),
        ("3.4.26", datetime.date(2026, 4, 3)),
        ("2026-04-03", datetime.date(2026, 4, 3)),
        (46082, datetime.date(2026, 3, 1)),
        (46082.75, datetime.date(2026, 3, 1)),
        ("46082", datetime.date(2026, 3, 1)),
        (datetime.datetime(2026, 3, 1, 13, 5), datetime.date(2026, 3, 1)),
        (datetime.date(2026, 3, 1), datetime.date(2026, 3, 1)),
    ],
)
def test_a_date_read_day_first(cell, date) -> None:
    """Verify each date form ENG-02 5 accepts, with date_order day_first."""
    assert clean_date(cell, "day_first") == date


def test_month_first_reads_the_month_before_the_day() -> None:
    """Verify date_order month_first reads 03.04.2026 as 4 March, and leaves
    yyyy-mm-dd as written.
    """
    assert clean_date("03.04.2026", "month_first") == datetime.date(2026, 3, 4)
    assert clean_date("2026-04-03", "month_first") == datetime.date(2026, 4, 3)


def test_an_empty_date_is_none() -> None:
    """Verify an empty cell gives None, so the caller decides what it means."""
    assert clean_date(" ", "day_first") is None


@pytest.mark.parametrize(
    "cell",
    [
        pytest.param("31.02.2026", id="no such day"),
        pytest.param("03.04/2026", id="two separators"),
        pytest.param("2026/04/03", id="year first with slashes"),
        pytest.param("03.04.026", id="three-digit year"),
        pytest.param(19999, id="day number too small"),
        pytest.param(80001, id="day number too large"),
        pytest.param("2026", id="a year alone"),
        pytest.param(datetime.time(9, 30), id="time"),
        pytest.param(True, id="true"),
    ],
)
def test_an_invalid_date_raises(cell) -> None:
    """Verify anything outside the date rule is invalid, never guessed."""
    with pytest.raises(ValueError):
        clean_date(cell, "day_first")


@given(st.dates(datetime.date(1955, 1, 1), datetime.date(2118, 12, 31)))
def test_any_date_reads_back_in_each_form(date) -> None:
    """Verify a date written as dd.mm.yyyy, as yyyy-mm-dd, or as an Excel day
    number reads back as the same date.
    """
    serial = (date - datetime.date(1899, 12, 30)).days
    assert clean_date(date.strftime("%d.%m.%Y"), "day_first") == date
    assert clean_date(date.strftime("%m/%d/%Y"), "month_first") == date
    assert clean_date(date.isoformat(), "day_first") == date
    assert clean_date(float(serial), "day_first") == date


@pytest.mark.parametrize(
    ("period", "date"),
    [
        (3, datetime.date(2026, 3, 1)),
        (12, datetime.date(2026, 12, 1)),
        (13, datetime.date(2026, 12, 31)),
        (16, datetime.date(2026, 12, 31)),
    ],
)
def test_a_date_from_fiscal_year_and_period(period, date) -> None:
    """Verify periods 1 to 12 are the first of the month, and 13 to 16 are
    31 December.
    """
    assert date_from_period(2026, period) == date


@pytest.mark.parametrize(("year", "period"), [(2026, 17), (None, 3), (2026, None)])
def test_a_missing_or_other_period_gives_no_date(year, period) -> None:
    """Verify period 17, or a missing year or period, is invalid."""
    with pytest.raises(ValueError):
        date_from_period(year, period)


# Value type, and text


@pytest.mark.parametrize(
    ("value_type", "is_actual"),
    [
        ("ACTUAL ", True),
        ("actual", True),
        (" commitment", False),
        ("Plan", None),
        ("", None),
    ],
)
def test_a_value_type_is_actual_not_actual_or_unclassified(
    value_type, is_actual
) -> None:
    """Verify the rule's lists match trimmed and lower-cased, and a value in
    neither list is unclassified (None).
    """
    assert classify_value_type(value_type, RULE) is is_actual


@pytest.mark.parametrize("value_type", ["Actual", "Commitment", "Plan", ""])
def test_with_no_rule_every_value_type_is_actual(value_type) -> None:
    """Verify actual_spend_rule null makes every row actual spend."""
    assert classify_value_type(value_type, None) is True


@pytest.mark.parametrize(
    ("cell", "text"),
    [
        ("  تجديد ترخيص  ", "تجديد ترخيص"),
        (12345.0, "12345"),
        (12.5, "12.5"),
        (7, "7"),
        (datetime.date(2026, 3, 1), "2026-03-01"),
    ],
)
def test_cell_text_trims_and_writes_numbers_shortest(cell, text) -> None:
    """Verify text is trimmed and kept as written, Arabic included, and a
    number is written without ".0".
    """
    assert cell_text(cell) == text
