"""Reconciliation: a file's rows against its own total (ENG-02 9.1 and 9.2).
No database.
"""

from decimal import Decimal

import pytest

from ids_audit.pipeline.reconciliation import Reconciliation, reconcile
from tests.files import HEADER, LINE, csv_file, line, read, total_row, xlsx_file


def test_a_file_whose_rows_add_up_to_its_total_reconciles() -> None:
    """Verify the staged rows, credits included, are taken from the control
    total, leaving a difference of exactly 0.000.
    """
    credit = line(DocumentNo="5100000102", Amount_in_LC="250.500-")
    result = reconcile(read(csv_file(LINE, credit, total_row("749.500"))))
    assert result == Reconciliation(
        control_total=Decimal("749.500"),
        staged=Decimal("749.500"),
        skipped=Decimal(0),
        difference=Decimal(0),
    )
    assert str(result.difference) == "0.000"


def test_the_difference_is_worked_out_before_rounding() -> None:
    """Verify a total with Excel's binary error, 1000.0000000001, is saved as
    1000.000 and leaves a difference of 0.000 (ENG-02 9.1).
    """
    result = reconcile(
        read(xlsx_file({"Spend": [HEADER, LINE, total_row(1000.0000000001)]}))
    )
    assert str(result.control_total) == "1000.000"
    assert str(result.difference) == "0.000"


@pytest.mark.parametrize(
    ("written", "control_total", "difference"),
    [
        pytest.param("1000.0005", "1000.001", "0.001", id="half up"),
        pytest.param("999.9995", "1000.000", "-0.001", id="negative half"),
        pytest.param("1000.0004", "1000.000", "0.000", id="below half"),
    ],
)
def test_amounts_are_rounded_half_up(written, control_total, difference) -> None:
    """Verify the control total and the difference are rounded half up to 3
    decimal places, away from zero, never half to even (ENG-02 9.1).
    """
    result = reconcile(read(csv_file(LINE, total_row(written))))
    assert str(result.control_total) == control_total
    assert str(result.difference) == difference


def test_skipped_rows_count_only_with_a_readable_amount_in_omr() -> None:
    """Verify a row skipped for its 4 decimals counts as read, while a row in
    another currency and a row whose amount can't be read don't count, so the
    file still reconciles (ENG-02 9.2).
    """
    rows = [
        LINE,
        line(DocumentNo="5100000102", Amount_in_LC="12.3455"),
        line(DocumentNo="5100000103", Amount_in_LC="50.000", LCurr="USD"),
        line(DocumentNo="5100000104", Amount_in_LC="12,34.5"),
    ]
    result = reconcile(read(csv_file(*rows, total_row("1012.3455"))))
    assert result.staged == Decimal("1000.000")
    assert str(result.skipped) == "12.3455"
    assert str(result.difference) == "0.000"


def test_a_file_with_no_total_row_has_no_difference() -> None:
    """Verify a file with no total row has no control total and no
    difference, but its staged amount is still worked out (ENG-02 9.2).
    """
    result = reconcile(read(csv_file(LINE)))
    assert result == Reconciliation(None, Decimal("1000.000"), Decimal(0), None)
