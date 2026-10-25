"""How every screen writes amounts, dates, times and months (ENG-03 2.4)."""

import datetime
from decimal import Decimal

from ids_audit.reports.templatetags.figures import amount, day, moment, month, places


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
