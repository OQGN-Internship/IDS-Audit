"""The line-key rules for lines already in the report (ENG-02 7.3).

Plain Python, like the rest of pipeline/: it imports nothing from Django.
"""

from collections.abc import Mapping, Sequence
from decimal import Decimal

from ids_audit.pipeline.results import CleanRow


def line_key_offered(rows: Sequence[CleanRow]) -> bool:
    """Return True when line-key mode can be chosen for a file: it has rows to
    commit, and every one has a full line key.
    """
    return bool(rows) and all(row.line_key for row in rows)


def find_duplicates(
    rows: Sequence[CleanRow], counted: Mapping[str, Decimal]
) -> dict[tuple[str, int], Decimal]:
    """Return the rows whose line key is a posting's that counts, each by its
    place in the file, with that posting's amount.

    counted maps each line key of the report's postings that count to the
    posting's amount. A row with no line key is never a duplicate.
    """
    return {
        (row.sheet, row.row_number): counted[row.line_key]
        for row in rows
        if row.line_key and row.line_key in counted
    }
