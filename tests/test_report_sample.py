"""The sample file's report figures against its expected result, worked out
by the script that made the file (ENG-03 2, 3, 6, 8 and 11).

The sample reaches the report as a user's upload does: received, read and
approved through the imports/ functions.
"""

from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest

from ids_audit.reports import queries
from ids_audit.reports.queries import ALL, Filters, Keys

SAMPLE = Path(__file__).parent / "fixtures" / "sample.xlsx"
EXCLUDING = Filters(exclude_outside=True)


def amounts(groups) -> dict[str, str]:
    """Each group's amount by its key, written as the expected result does."""
    return {group.key: f"{group.amount:.3f}" for group in groups}


def test_each_year_s_total_is_its_expected_total(
    sample_report, sample_expected
) -> None:
    """Verify with year set, only the postings dated in that year count
    (ENG-03 3.1).
    """
    for year, total in sample_expected["screens"]["by_year"].items():
        found = queries.totals(sample_report, Filters(year=int(year)))
        assert f"{found.total:.3f}" == total, year
    assert queries.years(sample_report) == [2026, 2025]


def test_excluding_outside_ids_drops_3100_and_3200_by_their_amount(
    sample_report, sample_expected
) -> None:
    """Verify excluding outside IDS removes 3100 and 3200, and the total falls
    by exactly their amount (ENG-03 3.1).
    """
    excluding = sample_expected["screens"]["excluding_outside"]
    found = queries.totals(sample_report, EXCLUDING)
    assert (f"{found.total:.3f}", found.lines) == (
        excluding["total"],
        excluding["lines"],
    )
    everything = queries.totals(sample_report)
    outside = sample_expected["exceptions"]["outside_ids"]
    assert everything.total - found.total == sum(
        Decimal(outside[code]["amount"]) for code in ("3100", "3200")
    )
    assert (
        amounts(queries.by_gl_account(sample_report, EXCLUDING)) == excluding["by_gl"]
    )
    assert (
        amounts(queries.by_department(sample_report, EXCLUDING))
        == excluding["by_department"]
    )


def test_each_detail_list_equals_its_expected_figures(
    sample_report, sample_expected
) -> None:
    """Verify each GL account by cost center, and each department and budget
    line by GL account, as their screens list them (ENG-03 4).
    """
    screens = sample_expected["screens"]
    for gl, expected in screens["gl_by_cost_center"].items():
        found = queries.by_cost_center(sample_report, ALL, Keys(gl=gl))
        assert {c.key: f"{c.amount:.3f}" for c in found} == expected, gl
    for dept, expected in screens["department_by_gl"].items():
        found = queries.by_gl_account(sample_report, ALL, Keys(dept=dept))
        assert amounts(found) == expected, dept
    for ci, expected in screens["budget_line_by_gl"].items():
        found = queries.by_gl_account(sample_report, ALL, Keys(ci=ci))
        assert amounts(found) == expected, ci


def test_the_running_total_and_lines_equal_their_expected_figures(
    sample_report, sample_expected
) -> None:
    """Verify the running total by month and the number of lines."""
    months = queries.by_month(sample_report)
    assert {
        m.key.strftime("%Y-%m"): f"{m.running:.3f}" for m in months
    } == sample_expected["screens"]["running_total"]
    assert queries.totals(sample_report).lines == sample_expected["screens"]["lines"]


def test_the_exceptions_lists_equal_the_expected_exceptions(
    sample_report, sample_expected
) -> None:
    """Verify 640200 is missing from the GL mapping, 650200 listed without a
    commitment item, 3100 and 3200 outside IDS, each with its expected amount,
    and no cost center is in the mapping without a department (ENG-03 8).
    """
    exceptions = sample_expected["exceptions"]
    no_ci = {
        u.key: (u.reason, f"{u.amount:.3f}")
        for u in queries.no_budget_line(sample_report)
    }
    assert no_ci == {
        "640200": (
            "missing_from_gl_map",
            exceptions["missing_from_gl_mapping"]["640200"]["amount"],
        ),
        "650200": (
            "listed_without_ci",
            exceptions["listed_without_ci"]["650200"]["amount"],
        ),
    }
    assert {u.key: f"{u.amount:.3f}" for u in queries.outside_ids(sample_report)} == {
        code: item["amount"] for code, item in exceptions["outside_ids"].items()
    }
    assert queries.no_department(sample_report) == []


def test_ci_none_and_dept_outside_list_exactly_their_postings(sample_report) -> None:
    """Verify ci=none lists exactly the postings for 640200 and 650200, and
    dept=outside exactly those for 3100 and 3200 (ENG-03 6).
    """
    every = list(queries.source_rows(sample_report))
    no_ci = queries.source_rows(sample_report, ALL, Keys(ci="none"))
    assert {r.pk for r in no_ci} == {
        r.pk for r in every if r.gl_code in ("640200", "650200")
    }
    outside = queries.source_rows(sample_report, ALL, Keys(dept="outside"))
    assert {r.pk for r in outside} == {
        r.pk for r in every if r.cost_center_code in ("3100", "3200")
    }


def test_a_source_row_s_sheet_and_row_point_to_its_values_in_the_file(
    sample_report,
) -> None:
    """Verify each source row's sheet and row number hold its GL account,
    cost center and amount in the original file (ENG-03 6).
    """
    workbook = openpyxl.load_workbook(SAMPLE, read_only=True)
    for row in queries.source_rows(sample_report):
        cells = next(
            workbook[row.sheet].iter_rows(
                min_row=row.row_number, max_row=row.row_number, values_only=True
            )
        )
        assert cells[1].lstrip("0") == row.gl_code
        assert cells[9].lstrip("0") == row.cost_center_code
        written = f"{abs(row.amount):,.3f}" + ("-" if row.amount < 0 else "")
        assert cells[12] == written
    workbook.close()


@pytest.mark.parametrize(
    "by", [queries.by_gl_account, queries.by_budget_line, queries.by_department]
)
def test_each_amount_s_source_rows_add_up_to_it(sample_report, by) -> None:
    """Verify for every amount on the overview, GL accounts and departments
    screens, the source rows add up to it (ENG-03 6).
    """
    key = {
        queries.by_gl_account: "gl",
        queries.by_budget_line: "ci",
        queries.by_department: "dept",
    }[by]
    for group in by(sample_report):
        rows = queries.source_rows(sample_report, ALL, Keys(**{key: group.key}))
        assert sum((r.amount for r in rows), Decimal(0)) == group.amount, group.key


def test_the_lists_run_highest_first_with_the_catch_alls_last(
    sample_report, sample_expected
) -> None:
    """Verify the GL accounts list runs by actual spend, highest first, and
    the budget lines list puts No budget line last, though it's larger than
    two budget lines (ENG-03 4).
    """
    by_gl = sample_expected["by_gl"]
    assert [a.key for a in queries.gl_accounts(sample_report)] == sorted(
        by_gl, key=lambda gl: -Decimal(by_gl[gl])
    )
    assert [b.key for b in queries.budget_lines(sample_report)] == [
        "2101",
        "2201",
        "2401",
        "2301",
        "2501",
        "none",
    ]
