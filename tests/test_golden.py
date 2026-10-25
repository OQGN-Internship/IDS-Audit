"""The sample file and its semicolon copy, read and cleaned, against their
expected results (ENG-02 12.2 and 12.3). No database.

The figures the expected results hold are worked out here from what
read_file() gives, with the GL and cost center mappings it read from the
workbook. The report screens work them out in SQL later (ENG-03), so these
tests prove the rows and mappings they'll start from.
"""

import dataclasses
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import pytest

from ids_audit.pipeline import readers
from ids_audit.pipeline.read import read_file
from ids_audit.pipeline.readers import read_calamine
from ids_audit.pipeline.results import ReadResult
from ids_audit.pipeline.ruleset import DEFAULT_SETTINGS

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_XLSX = (FIXTURES_DIR / "sample.xlsx").read_bytes()
SAMPLE_CSV = (FIXTURES_DIR / "sample_semicolon.csv").read_bytes()


def expected(name: str) -> dict:
    """Return an expected result, without the keys a later step works out:
    the replaced postings come from the commit (ENG-02 7.3).
    """
    with open(FIXTURES_DIR / name, encoding="utf-8") as file:
        result = json.load(file)
    for key in ("fixture", "scenario", "replaced"):
        result.pop(key, None)
    return result


def baisa(amount: Decimal) -> str:
    """Write an amount as the expected results do, with three decimals."""
    return str(amount.quantize(Decimal("0.001")))


def figures(result: ReadResult, mappings: ReadResult) -> dict:
    """Work out an expected result's figures from a file's rows, with the
    mappings in force taken from another read (ENG-02 8.1 and 8.2).
    """
    gl_map = {row.gl_code: row for row in mappings.gl_map}
    cc_map = {row.cc_code: row for row in mappings.cc_map}
    actual = [row for row in result.clean_rows if row.is_actual]
    sums: dict[str, defaultdict] = {
        group: defaultdict(Decimal) for group in ("gl", "ci", "department", "month")
    }
    exceptions: dict[str, dict] = {
        "missing_from_gl_mapping": {},
        "listed_without_ci": {},
        "outside_ids": {},
    }

    def note(kind: str, code: str, row) -> None:
        found = exceptions[kind].setdefault(code, {"amount": Decimal(0), "rows": []})
        found["amount"] += row.amount
        found["rows"].append(row.row_number)

    for row in actual:
        gl = gl_map.get(row.gl_code)
        cc = cc_map.get(row.cost_center_code)
        if gl is None:
            note("missing_from_gl_mapping", row.gl_code, row)
        elif not gl.ci_code:
            note("listed_without_ci", row.gl_code, row)
        if cc is None:
            note("outside_ids", row.cost_center_code, row)
        sums["gl"][row.gl_code] += row.amount
        sums["ci"][gl.ci_code if gl and gl.ci_code else "none"] += row.amount
        sums["department"][cc.dept_code if cc else "outside"] += row.amount
        sums["month"][row.posting_date.strftime("%Y-%m")] += row.amount

    to_commit = sum((row.amount for row in result.clean_rows), Decimal(0))
    skipped = sum(
        (r.amount for r in result.skipped_rows if r.in_reconciliation and r.amount),
        Decimal(0),
    )
    total = sum((row.amount for row in actual), Decimal(0))
    outside = sum(
        (found["amount"] for found in exceptions["outside_ids"].values()), Decimal(0)
    )
    return {
        "header_row": result.header_row,
        "rows": {
            "to_commit": len(result.clean_rows),
            "skipped": len(result.skipped_rows),
            "other": len(result.other_rows),
        },
        "period": {
            "first_posting": result.period_start.isoformat(),
            "last_posting": result.period_end.isoformat(),
        },
        "reconciliation": {
            "control_total": baisa(result.control_total),
            "to_commit": baisa(to_commit),
            "skipped": baisa(skipped),
            "difference": baisa(result.control_total - to_commit - skipped),
        },
        "actual_spend": {
            "total": baisa(total),
            "inside_ids": baisa(total - outside),
            "outside_ids": baisa(outside),
        },
        **{
            f"by_{group}": {key: baisa(amount) for key, amount in sums[group].items()}
            for group in sums
        },
        "exceptions": {
            **{
                kind: {
                    code: {"amount": baisa(found["amount"]), "rows": found["rows"]}
                    for code, found in codes.items()
                }
                for kind, codes in exceptions.items()
            },
            "other_rows": [row.row_number for row in result.other_rows],
        },
        "skipped_rows": [
            {"row": r.row_number, "reason": r.reason} for r in result.skipped_rows
        ],
    }


def fail(data: bytes) -> None:
    """Stand in for calamine failing, so the file is read with openpyxl."""
    raise ValueError("calamine can't read this file")


@pytest.fixture(params=["calamine", "openpyxl"])
def reader(request, monkeypatch) -> str:
    """Run a test once with each Excel reader (ENG-02 4.1)."""
    if request.param == "openpyxl":
        monkeypatch.setattr(readers, "read_calamine", fail)
    return request.param


def test_the_sample_matches_its_expected_result(reader) -> None:
    """Verify sample.xlsx, read as the first upload into an empty report, gives
    every figure in sample.expected.json, through either reader.
    """
    result = read_file(SAMPLE_XLSX, DEFAULT_SETTINGS)
    assert figures(result, result) == expected("sample.expected.json")


def test_both_readers_give_the_same_result(monkeypatch) -> None:
    """Verify calamine and openpyxl give the same rows, mapping rows and
    control total for the sample file (ENG-02 4.5, Tested by).
    """
    with_calamine = read_file(SAMPLE_XLSX, DEFAULT_SETTINGS)
    monkeypatch.setattr(readers, "read_calamine", fail)
    assert read_file(SAMPLE_XLSX, DEFAULT_SETTINGS) == with_calamine


def test_the_semicolon_copy_matches_its_expected_result() -> None:
    """Verify sample_semicolon.csv gives every figure in its expected result,
    with the mappings of sample.xlsx, uploaded before it into the same report
    (ENG-02 8.1, 12.3).
    """
    workbook = read_file(SAMPLE_XLSX, DEFAULT_SETTINGS)
    result = read_file(SAMPLE_CSV, DEFAULT_SETTINGS)
    assert (result.gl_map, result.cc_map) == ((), ())
    assert figures(result, workbook) == expected("sample_semicolon.expected.json")


def test_the_semicolon_copy_gives_the_workbook_s_rows() -> None:
    """Verify each CSV row is the workbook row of the same number, cleaned the
    same, Arabic item texts included. Only the sheet name differs.
    """
    workbook = read_file(SAMPLE_XLSX, DEFAULT_SETTINGS)
    result = read_file(SAMPLE_CSV, DEFAULT_SETTINGS)
    as_csv = [dataclasses.replace(row, sheet="") for row in workbook.clean_rows]
    assert list(result.clean_rows) == as_csv
    assert result.control_total == workbook.control_total


def test_every_row_below_the_sample_header_is_accounted_for() -> None:
    """Verify every row below the header is clean, skipped, the total row or
    an other row, and only blank rows are left out (ENG-02 5, Tested by).
    """
    result = read_file(SAMPLE_XLSX, DEFAULT_SETTINGS)
    transactions = read_calamine(SAMPLE_XLSX)[1]
    below_header = range(result.header_row + 1, len(transactions.rows) + 1)
    not_blank = {
        n for n in below_header if any(cell != "" for cell in transactions.rows[n - 1])
    }
    sorted_rows = [
        *(row.row_number for row in result.clean_rows),
        *(row.row_number for row in result.skipped_rows),
        *(row.row_number for row in result.other_rows),
        result.control_total_row,
    ]
    assert sorted(sorted_rows) == sorted(not_blank)


def test_the_sample_column_layout_is_kept_for_the_upload() -> None:
    """Verify the layout upload.column_layout keeps names each classified
    sheet's header row and columns (ENG-02 4.5).
    """
    layout = read_file(SAMPLE_XLSX, DEFAULT_SETTINGS).column_layout
    assert set(layout) == {"Transactions", "GL Mapping", "Cost Center Mapping"}
    assert layout["Transactions"]["header_row"] == 4
    assert layout["Transactions"]["columns"]["amount"] == "M"
    assert layout["Cost Center Mapping"] == {
        "header_row": 1,
        "header": [
            "Cost Center",
            "Cost Center Name",
            "Department Code",
            "Department Name",
        ],
        "columns": {
            "cost_center_code": "A",
            "cc_name": "B",
            "dept_code": "C",
            "dept_name": "D",
        },
        "picked": {},
    }
