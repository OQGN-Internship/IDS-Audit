"""The rows the pick-columns screen shows: a sheet's header row and the rows
after it (ENG-02 4.5). Plain Python, with no database.
"""

import pytest

from ids_audit.pipeline.read import first_rows
from tests.files import HEADER, LINE, csv_file, line, xlsx_file


def test_the_header_row_is_found_below_title_lines_and_rows_follow_it() -> None:
    """Verify the header row is the one whose text matches, below two title
    lines, and it comes with the next rows, each as wide as the widest.
    """
    data = xlsx_file(
        {
            "Notes": [("Read me",)],
            "Spend": [
                ("G/L line items",),
                (),
                HEADER,
                LINE,
                line(DocumentNo="2"),
                ("Total", "", "", "", "", "", "", "", "2,000.000", "OMR", "extra"),
            ],
        }
    )
    found = first_rows(data, "Spend", HEADER, count=2)
    assert found.header_row == 3
    assert [number for number, _ in found.rows] == [3, 4, 5]
    assert found.rows[0][1][:2] == ("CoCd", "G/L Acct")
    assert {len(cells) for _, cells in found.rows} == {10}


def test_at_most_count_rows_follow_the_header() -> None:
    """Verify the header comes with 5 rows by default, or fewer if the sheet
    ends sooner, and a widest row widens them all.
    """
    rows = [line(DocumentNo=str(n)) for n in range(8)]
    found = first_rows(csv_file(*rows), "", HEADER)
    assert [number for number, _ in found.rows] == [1, 2, 3, 4, 5, 6]
    found = first_rows(csv_file(LINE, (*LINE, "wide")), "", HEADER)
    assert [len(cells) for _, cells in found.rows] == [11, 11, 11]


def test_a_header_that_isnt_found_starts_at_row_1() -> None:
    """Verify a header no row matches gives the sheet's first rows."""
    found = first_rows(csv_file(LINE), "", ("Something", "else"), count=1)
    assert found.header_row == 1
    assert found.rows[0][1][0] == "CoCd"


def test_a_sheet_that_isnt_in_the_file_is_refused() -> None:
    """Verify asking for a sheet the file doesn't have is a ValueError."""
    with pytest.raises(ValueError, match="no sheet named 'Spend'"):
        first_rows(csv_file(LINE), "Spend", HEADER)
