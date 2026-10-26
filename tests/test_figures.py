"""How every screen writes amounts, dates, times and months (ENG-03 2.4)."""

import datetime
from decimal import Decimal

from ids_audit.reports.screens import Cell
from ids_audit.reports.templatetags.figures import (
    amount,
    day,
    moment,
    month,
    places,
    share,
    td,
)


def test_amounts_have_three_decimals_commas_and_a_minus() -> None:
    """Verify amounts from Decimal and from a preview's text are written in
    full, never rounded, with commas for thousands and a minus for credits.
    """
    assert amount(Decimal("-1250")) == "-1,250.000"
    assert amount("12400.000") == "12,400.000"
    assert amount("1234567.891") == "1,234,567.891"
    assert amount(Decimal("0.000")) == "0.000"
    assert amount(None) == ""


def test_dates_and_times_are_day_first_and_times_in_muscat() -> None:
    """Verify a date is written day first, and a time stored in UTC is
    written in Muscat time, four hours ahead.
    """
    assert day(datetime.date(2026, 4, 3)) == "03.04.2026"
    at = datetime.datetime(2026, 4, 3, 10, 5, tzinfo=datetime.UTC)
    assert moment(at) == "03.04.2026 14:05"
    assert day(None) == moment(None) == ""


def test_months_and_rows_are_written_for_people() -> None:
    """Verify a month kept as 2026-03 is Mar 2026, and an item's rows are
    grouped by sheet, with a text file's nameless sheet left out.
    """
    assert month("2026-03") == "Mar 2026"
    assert places([["Spend", 4], ["Spend", 9], ["GL", 2]]) == (
        "Spend: rows 4, 9; GL: row 2"
    )
    assert places([["", 12]]) == "row 12"


def test_a_share_is_a_percentage_with_one_decimal_or_a_dash() -> None:
    """Verify a share kept as a fraction is written as a percentage with one
    decimal place, and a missing share as a dash (ENG-03 2.2).
    """
    assert share(Decimal("0.944")) == "94.4%"
    assert share(Decimal("-0.005")) == "-0.5%"
    assert share(Decimal("1.000")) == "100.0%"
    assert share(None) == "–"


def test_a_cell_is_written_escaped_by_its_kind() -> None:
    """Verify a table cell is written by its kind, its text escaped, with its
    link, and an amount kept as read never rounded.
    """
    assert td(Cell(Decimal("-1250"), "amount", "/rows?gl=1")) == (
        '<td class="amount"><a href="/rows?gl=1">-1,250.000</a></td>'
    )
    assert td(Cell(Decimal("12.3456"), "exact")) == '<td class="exact">12.3456</td>'
    assert td(Cell("<b>x</b>", "text")) == '<td class="text">&lt;b&gt;x&lt;/b&gt;</td>'
    assert td(Cell(None, "share")) == '<td class="share">–</td>'
    assert td(Cell(datetime.date(2026, 4, 3), "date")) == (
        '<td class="date">03.04.2026</td>'
    )
