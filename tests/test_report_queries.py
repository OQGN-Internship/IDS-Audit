"""The report screens' figures in reports/queries.py: the filters, the keys
of a drill-down, shares, the screens' lists, the as-of line and the
exceptions lists (ENG-03 2, 3, 6 and 8).

Every row is made by hand, so each expected figure is worked out here. The
report a_report() builds is described in tests/test_queries.py.
"""

import datetime
from decimal import Decimal

import pytest

from ids_audit.imports.models import SkippedRow, Upload
from ids_audit.reports import queries
from ids_audit.reports.models import Report
from ids_audit.reports.queries import (
    ALL,
    NO_KEYS,
    Filters,
    GlAccount,
    Group,
    Keys,
    Skipped,
    Totals,
    Unmatched,
    UnmatchedTotals,
)
from tests.factories import (
    add_posting,
    commit,
    make_report,
    make_upload,
    map_cc,
    map_gl,
    roll_back,
)
from tests.test_queries import AUG, JUL, a_report

pytestmark = pytest.mark.django_db

LINE_KEY = Report.DuplicateMode.LINE_KEY


def d(text: str) -> Decimal:
    """An amount written as text, so it never passes through a float."""
    return Decimal(text)


# Filters (ENG-03 3.1)


def test_the_year_filter_counts_only_postings_dated_in_that_year() -> None:
    """Verify year=2025 counts only the postings dated in 2025, and 2026 the
    rest, so the two add up to all years.
    """
    report = a_report()
    assert queries.totals(report, Filters(year=2025)) == Totals(
        d("177.000"), d("157.000"), d("20.000"), 4
    )
    assert queries.totals(report, Filters(year=2026)) == Totals(
        d("3.000"), d("3.000"), d("0.000"), 1
    )


def test_excluding_outside_ids_drops_its_cost_centers_and_shares_follow() -> None:
    """Verify exclude_outside drops the cost centers outside IDS from every
    figure, the total falls by exactly their amount, and shares are of the
    total shown.
    """
    report = a_report()
    excluding = Filters(exclude_outside=True)
    assert queries.totals(report, excluding) == Totals(
        d("160.000"), d("160.000"), d("0.000"), 4
    )
    assert queries.by_department(report, excluding) == [
        Group("APP", "Dept APP", d("157.000"), 3, d("0.981")),
        Group("none", "No department", d("3.000"), 1, d("0.019")),
    ]


def test_a_year_with_no_postings_gives_zero_totals_and_no_groups() -> None:
    """Verify a valid year with no postings gives 0.000 and no groups, never
    an error (ENG-03 3.1 and 2.2).
    """
    report = a_report()
    empty = Filters(year=2024)
    assert queries.totals(report, empty) == Totals(
        d("0.000"), d("0.000"), d("0.000"), 0
    )
    assert queries.by_gl_account(report, empty) == []
    assert queries.by_month(report, empty) == []
    assert queries.gl_accounts(report, empty) == []


def test_years_lists_each_year_with_actual_spend_newest_first() -> None:
    """Verify the year filter's choices are the years with actual spend that
    counts, newest first (ENG-03 3.1).
    """
    assert queries.years(a_report()) == [2026, 2025]


def test_the_running_total_starts_from_the_first_month_the_filters_include() -> None:
    """Verify the running total under year=2026 starts again from 0 (ENG-03
    2.2).
    """
    (january,) = queries.by_month(a_report(), Filters(year=2026))
    assert january.running == d("3.000")


# Shares (ENG-03 2.2)


@pytest.mark.parametrize(
    ("amount", "total", "share"),
    [
        ("1245.000", "10000.000", "0.125"),
        ("1244.999", "10000.000", "0.124"),
        ("-1245.000", "10000.000", "-0.125"),
        ("1.000", "3.000", "0.333"),
        ("2.000", "3.000", "0.667"),
        ("5.000", "5.000", "1.000"),
        ("0.000", "5.000", "0.000"),
    ],
)
def test_a_share_is_rounded_half_up_to_one_decimal_place(amount, total, share) -> None:
    """Verify a share is the fraction rounded half up to 0.001, which is one
    decimal place of a percentage, away from zero on a tie.
    """
    assert queries.share_of(d(amount), d(total)) == d(share)


@pytest.mark.parametrize("total", ["0.000", "-1.000", None])
def test_there_is_no_share_of_a_total_that_is_zero_or_negative(total) -> None:
    """Verify a share of a zero, negative or missing total is None (ENG-03
    2.2, N8).
    """
    assert queries.share_of(d("1.000"), None if total is None else d(total)) is None


def test_groups_have_no_share_when_the_total_shown_nets_to_zero() -> None:
    """Verify a report whose spend and credits net to zero gives its groups no
    share, rather than dividing by zero.
    """
    report = make_report()
    upload = make_upload(report)
    add_posting(upload, JUL, "100.000", gl_code="610100")
    add_posting(upload, JUL, "-100.000", gl_code="620200")
    commit(upload, minute=1)
    assert [group.share for group in queries.by_gl_account(report)] == [None, None]


# The order of a screen's lists (ENG-03 4)


def test_highest_first_puts_the_catch_alls_last_in_their_order() -> None:
    """Verify a list runs by actual spend, highest first, then by code, with
    No budget line or No department, then Outside IDS, last whatever their
    amount.
    """
    groups = [
        Group("outside", "Outside IDS", d("900.000"), 1),
        Group("B", "", d("5.000"), 1),
        Group("none", "No department", d("800.000"), 1),
        Group("A", "", d("5.000"), 1),
        Group("C", "", d("7.000"), 1),
    ]
    assert [g.key for g in queries.highest_first(groups)] == [
        "C",
        "A",
        "B",
        "none",
        "outside",
    ]


# The screens' lists (ENG-03 4)


def test_gl_accounts_lists_each_with_its_budget_line_and_cost_centers() -> None:
    """Verify the GL accounts screen's rows: highest first, each with its
    budget line or No budget line, share, lines and number of cost centers.
    """
    assert queries.gl_accounts(a_report()) == [
        GlAccount(
            "610100", "GL 610100", "2101", "CI 2101", 1, d("170.000"), d("0.944"), 3, 2
        ),
        GlAccount(
            "640200", "", "none", "No budget line", 1, d("7.000"), d("0.039"), 1, 1
        ),
        GlAccount(
            "650200",
            "GL 650200",
            "none",
            "No budget line",
            1,
            d("3.000"),
            d("0.017"),
            1,
            1,
        ),
    ]


def own_upload_report() -> Report:
    """A report under own_upload whose two uploads put 610100 under 2101 and
    2201, and cost center 4510 under APP and INF, each with one posting.
    """
    report = make_report("own_upload")
    for minute, (ci, dept, amount) in enumerate(
        [("2101", "APP", "10.000"), ("2201", "INF", "30.000")], start=1
    ):
        upload = make_upload(report)
        map_gl(upload, "610100", ci)
        map_cc(upload, "4510", dept)
        add_posting(upload, JUL, amount)
        commit(upload, minute=minute, mode=LINE_KEY)
    return report


def test_a_gl_account_under_several_budget_lines_shows_several_and_splits() -> None:
    """Verify own_upload counting a GL account under two budget lines makes
    its row show Several (2), and its budget lines split its spend (ENG-03
    2.3, G19).
    """
    report = own_upload_report()
    (account,) = queries.gl_accounts(report)
    assert (account.budget_line, account.budget_line_name, account.budget_lines) == (
        "",
        "Several (2)",
        2,
    )
    assert queries.by_budget_line(report, ALL, Keys(gl="610100")) == [
        Group("2101", "CI 2101", d("10.000"), 1, d("0.250")),
        Group("2201", "CI 2201", d("30.000"), 1, d("0.750")),
    ]


def test_a_cost_center_under_several_departments_has_a_row_for_each() -> None:
    """Verify own_upload counting a cost center under two departments gives
    each pair a row of its own (ENG-03 2.3).
    """
    rows = queries.by_cost_center(own_upload_report(), ALL, Keys(gl="610100"))
    assert [(r.key, r.dept, r.amount) for r in rows] == [
        ("4510", "INF", d("30.000")),
        ("4510", "APP", d("10.000")),
    ]


def test_by_cost_center_names_each_cost_center_and_its_department() -> None:
    """Verify a GL account's cost centers each come with its department, Outside
    IDS or No department, highest first.
    """
    rows = queries.by_cost_center(a_report())
    assert [(r.key, r.name, r.dept, r.dept_name, r.amount, r.lines) for r in rows] == [
        ("4510", "CC 4510", "APP", "Dept APP", d("157.000"), 3),
        ("3100", "", "outside", "Outside IDS", d("20.000"), 1),
        ("4520", "CC 4520", "none", "No department", d("3.000"), 1),
    ]


def test_the_overview_shows_the_10_largest_and_one_row_for_the_others() -> None:
    """Verify the overview's 10 largest GL accounts, and the others' actual
    spend added up over every posting outside them, with how many they are
    (ENG-03 4).
    """
    report = make_report()
    upload = make_upload(report)
    for number in range(1, 13):
        add_posting(upload, JUL, f"{number}.000", gl_code=f"6{number:05d}")
    commit(upload, minute=1)
    top = queries.top_gl_accounts(report)
    assert [g.amount for g in top.largest] == [d(f"{n}.000") for n in range(12, 2, -1)]
    assert top.others == Group(
        "", "All other GL accounts (2)", d("3.000"), 2, d("0.038")
    )
    assert top.others_count == 2


def test_the_overview_has_no_others_row_with_10_gl_accounts_or_fewer() -> None:
    """Verify there's no row for the others when every GL account is shown."""
    top = queries.top_gl_accounts(a_report())
    assert [g.key for g in top.largest] == ["610100", "640200", "650200"]
    assert top.others is None


def test_budget_lines_lists_each_with_its_gl_accounts() -> None:
    """Verify each budget line with the GL accounts counted under it, and No
    budget line last.
    """
    rows = queries.budget_lines(a_report())
    assert [(r.key, r.name, r.gl_accounts, r.amount, r.lines) for r in rows] == [
        ("2101", "CI 2101", ["610100"], d("170.000"), 3),
        ("none", "No budget line", ["640200", "650200"], d("10.000"), 2),
    ]


def test_gl_budget_lines_gives_each_gl_account_its_budget_line() -> None:
    """Verify Q3's table: each GL account with the budget line it's counted
    under (ENG-03 7.2).
    """
    rows = queries.gl_budget_lines(a_report())
    assert [(r.gl, r.ci, r.ci_name, r.amount) for r in rows] == [
        ("610100", "2101", "CI 2101", d("170.000")),
        ("640200", "none", "No budget line", d("7.000")),
        ("650200", "none", "No budget line", d("3.000")),
    ]


def test_name_of_gives_the_name_of_the_code_the_keys_choose() -> None:
    """Verify a detail screen's name comes from the mapping in force, and is
    empty for a code with none.
    """
    report = a_report()
    assert queries.name_of(report, "gl_name", Keys(gl="610100")) == "GL 610100"
    assert queries.name_of(report, "gl_name", Keys(gl="640200")) == ""
    assert queries.name_of(report, "dept_name", Keys(dept="APP")) == "Dept APP"


# Drill-down keys (ENG-03 6.1)


@pytest.mark.parametrize(
    ("keys", "total", "lines"),
    [
        (Keys(gl="610100"), "170.000", 3),
        (Keys(ci="2101"), "170.000", 3),
        (Keys(ci="none"), "10.000", 2),
        (Keys(cc="3100"), "20.000", 1),
        (Keys(dept="APP"), "157.000", 3),
        (Keys(dept="outside"), "20.000", 1),
        (Keys(dept="none"), "3.000", 1),
        (Keys(month=datetime.date(2025, 8, 1)), "57.000", 2),
        (Keys(through=datetime.date(2025, 8, 1)), "177.000", 4),
        (Keys(upload=1), "180.000", 5),
        (Keys(upload=2), "0.000", 0),
        (Keys(gl="610100", cc="4510", month=datetime.date(2025, 7, 1)), "100.000", 1),
    ],
)
def test_each_key_chooses_the_rows_of_its_group(keys, total, lines) -> None:
    """Verify each drill-down key keeps only its group's rows, and the rows add
    up to the group (ENG-03 6.1). Upload 2 is rolled back, so it has none.
    """
    report = a_report()
    found = queries.totals(report, ALL, keys)
    assert (found.total, found.lines) == (d(total), lines)
    assert queries.source_rows(report, ALL, keys).count() == lines


def test_source_rows_run_by_date_then_upload_then_sheet_and_row() -> None:
    """Verify source rows are sorted by posting date, then upload number, then
    sheet and row number, so a page always holds the same rows (ENG-03 6.2).
    """
    report = make_report()
    first, second = make_upload(report), make_upload(report)
    for upload, row_number, day in [
        (second, 3, AUG),
        (first, 9, AUG),
        (second, 2, JUL),
        (first, 4, AUG),
    ]:
        posting = add_posting(upload, day, "1.000")
        posting.row_number = row_number
        posting.save()
    commit(first, minute=1, mode=LINE_KEY)
    commit(second, minute=2)
    rows = queries.source_rows(report)
    assert [(r.posting_date, r.upload.number, r.row_number) for r in rows] == [
        (JUL, 2, 2),
        (AUG, 1, 4),
        (AUG, 1, 9),
        (AUG, 2, 3),
    ]


# The as-of line and the committed uploads (ENG-03 3.2 and 9.2)


def test_the_as_of_line_names_the_latest_committed_upload() -> None:
    """Verify the as-of line's upload is the most recently committed one, never
    a rolled-back one, and an upload awaiting approval is found.
    """
    report = a_report()
    assert queries.latest_committed(report).number == 1
    assert queries.awaiting_approval(report) is None
    waiting = make_upload(report, status=Upload.Status.AWAITING_APPROVAL)
    assert queries.awaiting_approval(report) == waiting


def test_an_empty_report_has_no_committed_upload() -> None:
    """Verify a report with nothing committed has nothing for the as-of line."""
    assert queries.latest_committed(make_report()) is None


def test_committed_uploads_list_each_with_what_it_still_counts() -> None:
    """Verify every committed upload is listed in the order of commits, with
    the lines and actual spend it still counts: none for one whose months a
    later file replaced, and never a rolled-back one (ENG-03 9.2, G14).
    """
    report = make_report()
    first, second, third = (make_upload(report) for _ in range(3))
    add_posting(first, JUL, "10.000")
    add_posting(second, JUL, "20.000")
    add_posting(second, AUG, "5.000")
    add_posting(third, AUG, "99.000")
    commit(first, minute=1)
    commit(second, minute=2)
    roll_back(commit(third, minute=3))
    listed = queries.committed_uploads(report)
    assert [(c.upload.number, c.lines, c.amount) for c in listed] == [
        (1, 0, d("0.000")),
        (2, 2, d("25.000")),
    ]
    under_august = queries.committed_uploads(report, Filters(year=2025))
    assert [c.lines for c in under_august] == [0, 2]


# The exceptions screen (ENG-03 8)


def test_no_budget_line_lists_missing_then_listed_without_a_commitment_item() -> None:
    """Verify the GL accounts with no budget line, split into missing from the
    GL mapping and listed without a commitment item.
    """
    assert queries.no_budget_line(a_report()) == [
        Unmatched("640200", "", "missing_from_gl_map", d("7.000"), 1, []),
        Unmatched("650200", "GL 650200", "listed_without_ci", d("3.000"), 1, []),
    ]


def test_outside_ids_lists_each_cost_center_with_the_gl_accounts_it_used() -> None:
    """Verify the cost centers outside IDS, with the GL accounts they used."""
    assert queries.outside_ids(a_report()) == [
        Unmatched("3100", "", "", d("20.000"), 1, ["610100"])
    ]


def test_no_department_lists_cost_centers_inside_ids_with_no_department() -> None:
    """Verify a cost center the mapping lists with no department code is in its
    own list, and not outside IDS (ENG-03 8, G20).
    """
    assert queries.no_department(a_report()) == [
        Unmatched("4520", "CC 4520", "", d("3.000"), 1, [])
    ]


def test_unmatched_totals_count_what_is_in_both_once() -> None:
    """Verify Q8's figures: each list's total, the actual spend with no budget
    line that's also outside IDS or in no department, and the actual spend in
    any list, counted once (ENG-03 7.2).
    """
    assert queries.unmatched_totals(a_report()) == UnmatchedTotals(
        no_budget_line=d("10.000"),
        outside_ids=d("20.000"),
        no_department=d("3.000"),
        in_both=d("3.000"),
        in_all=d("30.000"),
    )


def changed_mapping_report(mode: str = LINE_KEY) -> tuple[Report, Upload, Upload]:
    """A report whose second upload moves 610100 from 2101 to 2201, and keeps
    4510 in APP, each upload with a 610100 posting in July.
    """
    report = make_report()
    first, second = make_upload(report), make_upload(report)
    for minute, upload, ci, amount in [
        (1, first, "2101", "10.000"),
        (2, second, "2201", "30.000"),
    ]:
        map_gl(upload, "610100", ci)
        map_cc(upload, "4510", "APP")
        add_posting(upload, JUL, amount)
        commit(upload, minute=minute, mode=mode)
    return report, first, second


def test_a_budget_line_change_lists_each_upload_and_what_it_still_counts() -> None:
    """Verify a GL account moved between files lists both uploads, each with
    the item it gave and its spend that still counts, and a cost center that
    didn't move isn't listed (ENG-03 8).
    """
    report, first, second = changed_mapping_report()
    (change,) = queries.mapping_changes(report, queries.GL_MAP)
    assert change.code == "610100"
    assert [
        (m.upload, m.value, m.value_name, m.amount, m.lines) for m in change.uploads
    ] == [
        (first, "2101", "CI 2101", d("10.000"), 1),
        (second, "2201", "CI 2201", d("30.000"), 1),
    ]
    assert queries.mapping_changes(report, queries.CC_MAP) == []


def test_a_change_is_gone_once_one_of_its_uploads_is_rolled_back() -> None:
    """Verify rolling back the upload that moved a GL account removes the
    change (ENG-03 8).
    """
    report, _, second = changed_mapping_report()
    roll_back(second)
    assert queries.mapping_changes(report, queries.GL_MAP) == []


def test_a_replaced_upload_stays_in_a_change_with_nothing_still_counting() -> None:
    """Verify an upload whose months a later file replaced is still listed in
    the change, with 0.000 still counting (ENG-03 8, G14).
    """
    report, _, _ = changed_mapping_report(Report.DuplicateMode.REPLACE_BY_PERIOD)
    (change,) = queries.mapping_changes(report, queries.GL_MAP)
    assert [(m.amount, m.lines) for m in change.uploads] == [
        (d("0.000"), 0),
        (d("30.000"), 1),
    ]


def test_a_department_change_is_listed_like_a_budget_line_change() -> None:
    """Verify a cost center moved to another department between files is
    listed with both uploads.
    """
    report = make_report()
    for minute, dept in enumerate(["APP", "INF"], start=1):
        upload = make_upload(report)
        map_cc(upload, "4510", dept)
        add_posting(upload, JUL, "1.000")
        commit(upload, minute=minute, mode=LINE_KEY)
    (change,) = queries.mapping_changes(report, queries.CC_MAP)
    assert [(m.value, m.value_name) for m in change.uploads] == [
        ("APP", "Dept APP"),
        ("INF", "Dept INF"),
    ]


def test_skipped_lists_each_committed_upload_s_skipped_rows_by_reason() -> None:
    """Verify the skipped rows of each committed upload, by reason, with their
    number and readable amount kept exactly, and none of a rolled-back upload
    (ENG-03 8).
    """
    report = a_report()
    committed, rolled_back = report.uploads.order_by("number")
    for upload, reason, amount in [
        (committed, "Invalid date", "1.000"),
        (committed, "Invalid date", None),
        (committed, "More than 3 decimals", "12.3456"),
        (rolled_back, "Invalid date", "5.000"),
    ]:
        SkippedRow.objects.create(
            upload=upload,
            row_number=7,
            reason=reason,
            amount=None if amount is None else d(amount),
            raw_values=[],
        )
    assert queries.skipped(report) == [
        Skipped(1, "file1.xlsx", "Invalid date", 2, d("1.000")),
        Skipped(1, "file1.xlsx", "More than 3 decimals", 1, d("12.3456")),
    ]


def test_every_list_is_empty_for_a_report_with_nothing_committed() -> None:
    """Verify an empty report's lists are all empty and its totals 0.000."""
    report = make_report()
    for found in (
        queries.gl_accounts(report),
        queries.budget_lines(report),
        queries.by_cost_center(report),
        queries.no_budget_line(report),
        queries.outside_ids(report),
        queries.no_department(report),
        queries.committed_uploads(report),
        queries.skipped(report),
        queries.years(report),
        list(queries.source_rows(report, ALL, NO_KEYS)),
    ):
        assert found == []
    assert queries.unmatched_totals(report) == UnmatchedTotals(*[d("0.000")] * 5)
