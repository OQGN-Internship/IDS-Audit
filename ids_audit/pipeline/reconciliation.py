"""Reconciliation: proving a file was read completely (ENG-02 9).

Every amount in the file is either staged or skipped with a reason, and
together they add up to the file's own total.

Plain Python, like the rest of pipeline/: it imports nothing from Django.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from ids_audit.pipeline.cleaning import BAISA
from ids_audit.pipeline.results import ReadResult


@dataclass(frozen=True)
class Reconciliation:
    """The lines of ENG-02 9.2 for one file.

    staged is every clean row's amount, to commit or duplicate: which is which
    is decided against the report (7.3), and the difference is the same either
    way. skipped is the skipped rows that count in reconciliation, with a
    readable amount, as read. control_total and difference are rounded half
    up to 3 decimal places, and are None when the file has no total row.
    """

    control_total: Decimal | None
    staged: Decimal
    skipped: Decimal
    difference: Decimal | None


def to_baisa(amount: Decimal) -> Decimal:
    """Round an amount half up to 3 decimal places (ENG-02 9.1)."""
    return amount.quantize(BAISA, rounding=ROUND_HALF_UP)


def amount_text(amount: Decimal) -> str:
    """Write an amount exactly, with at least 3 decimal places, such as
    "0.000" or "12.3456". JSON has no decimal type, so upload.preview and the
    audit log keep amounts as text (ENG-02 11.1).
    """
    if amount.as_tuple().exponent > -3:
        amount = amount.quantize(BAISA)
    # "f" never uses an exponent, which str() does for 0.0000001.
    return format(amount, "f")


def reconcile(result: ReadResult) -> Reconciliation:
    """Work out a file's reconciliation (ENG-02 9.2).

    The difference is worked out from the control total as read, and only
    then rounded, so Excel's binary errors in its sums, such as
    1234.5000000001, leave no difference (9.1).
    """
    staged = sum((row.amount for row in result.clean_rows), Decimal(0))
    skipped = sum(
        (
            row.amount
            for row in result.skipped_rows
            if row.in_reconciliation and row.amount is not None
        ),
        Decimal(0),
    )
    if result.control_total is None:
        return Reconciliation(None, staged, skipped, None)
    return Reconciliation(
        control_total=to_baisa(result.control_total),
        staged=staged,
        skipped=skipped,
        difference=to_baisa(result.control_total - staged - skipped),
    )
