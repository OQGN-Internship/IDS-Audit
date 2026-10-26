"""The sum rule (ENG-03 11): for random postings and filters, every list
adds up to its total, every group's drill-down adds up to the group, and the
total equals the generated postings' own sum under the same filters.

Each example runs in a savepoint that's rolled back, so the next one starts
from an empty report.
"""

import datetime
from decimal import Decimal

import pytest
from django.db import transaction
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ids_audit.ledger.models import Posting
from ids_audit.reports import queries
from ids_audit.reports.models import Report
from ids_audit.reports.queries import ALL, Filters, Keys
from tests.factories import (
    add_posting,
    commit,
    make_report,
    make_upload,
    map_cc,
    map_gl,
)

pytestmark = pytest.mark.django_db

# 640200 is missing from the GL mapping and 650200 listed without a
# commitment item. 4530 is listed without a department and 3100 is missing
# from the cost center mapping, so it's outside IDS (ENG-02 8.2).
GL_CODES = ["610100", "610200", "620200", "640200", "650200"]
COST_CENTERS = ["4510", "4520", "4530", "3100"]
MONTHS = [
    datetime.date(year, month, 1) for year in (2025, 2026) for month in (1, 6, 12)
]

postings = st.lists(
    st.tuples(
        st.sampled_from(GL_CODES),
        st.sampled_from(COST_CENTERS),
        st.sampled_from(MONTHS),
        st.integers(min_value=-2_000_000, max_value=9_000_000),
        st.booleans(),
        st.integers(min_value=0, max_value=1),
    ),
    min_size=1,
    max_size=25,
)
filters = st.builds(
    Filters,
    year=st.sampled_from([None, 2024, 2025, 2026]),
    exclude_outside=st.booleans(),
)


def build(rows: list[tuple]) -> tuple[Report, list[tuple]]:
    """Make a report of two committed uploads in line-key mode, so every
    posting counts, holding the rows, each amount in baisa. Return it with
    each row's GL account, cost center, date, amount and whether it's actual.
    """
    report = make_report()
    uploads = [make_upload(report), make_upload(report)]
    for upload in uploads:
        map_gl(upload, "610100", "2101")
        map_gl(upload, "610200", "2101")
        map_gl(upload, "620200", "2201")
        map_gl(upload, "650200", "")
        map_cc(upload, "4510", "APP")
        map_cc(upload, "4520", "INF")
        map_cc(upload, "4530", "")
    made = []
    for gl, cc, day, baisa, actual, which in rows:
        amount = Decimal(baisa) / 1000
        posting = add_posting(uploads[which], day, f"{amount:.3f}", gl, cc)
        if not actual:
            Posting.objects.filter(pk=posting.pk).update(is_actual=False)
        made.append((gl, cc, day, amount, actual))
    for minute, upload in enumerate(uploads, start=1):
        commit(upload, minute=minute, mode=Report.DuplicateMode.LINE_KEY)
    return report, made


def expected_total(made: list[tuple], chosen: Filters) -> Decimal:
    """Add up the generated rows the filters keep, apart from the app."""
    return sum(
        (
            amount
            for _gl, cc, day, amount, actual in made
            if actual
            and (chosen.year is None or day.year == chosen.year)
            and not (chosen.exclude_outside and cc == "3100")
        ),
        Decimal("0.000"),
    )


def drill(group: queries.Group, by: str) -> Keys:
    """The keys a click on the group opens (ENG-03 6.1)."""
    return {
        "gl": Keys(gl=group.key),
        "ci": Keys(ci=group.key),
        "dept": Keys(dept=group.key),
        "month": Keys(month=group.key),
    }[by]


@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(rows=postings, chosen=filters)
def test_every_list_adds_up_and_every_drill_down_adds_up_to_its_figure(
    rows, chosen
) -> None:
    """Verify the groups add up to the total, each group's drill-down rows add
    up to it, the running total ends at the total, and the total is the
    generated rows' own sum (ENG-03 2.3, 6.2 and 11).
    """
    with transaction.atomic():
        report, made = build(rows)
        totals = queries.totals(report, chosen)
        assert totals.total == expected_total(made, chosen)
        assert totals.inside_ids + totals.outside_ids == totals.total

        for by, function in (
            ("gl", queries.by_gl_account),
            ("ci", queries.by_budget_line),
            ("dept", queries.by_department),
            ("month", queries.by_month),
        ):
            groups = function(report, chosen)
            assert sum(g.amount for g in groups) == totals.total, by
            assert sum(g.lines for g in groups) == totals.lines, by
            for group in groups:
                found = queries.totals(report, chosen, drill(group, by))
                assert (found.total, found.lines) == (group.amount, group.lines)

        months = queries.by_month(report, chosen)
        for month in months:
            through = queries.totals(report, chosen, Keys(through=month.key))
            assert month.running == through.total
        if months:
            assert months[-1].running == totals.total

        for center in queries.by_cost_center(report, chosen):
            found = queries.totals(
                report, chosen, Keys(cc=center.key, dept=center.dept)
            )
            assert found.total == center.amount

        accounts = queries.gl_accounts(report, chosen)
        assert sum(a.amount for a in accounts) == totals.total
        top = queries.top_gl_accounts(report, chosen)
        others = top.others.amount if top.others else Decimal(0)
        assert sum(g.amount for g in top.largest) + others == totals.total

        for line in queries.budget_lines(report, chosen):
            assert (
                queries.totals(report, chosen, Keys(ci=line.key)).total == line.amount
            )

        unmatched = queries.unmatched_totals(report, chosen)
        for listed, total in (
            (queries.no_budget_line(report, chosen), unmatched.no_budget_line),
            (queries.outside_ids(report, chosen), unmatched.outside_ids),
            (queries.no_department(report, chosen), unmatched.no_department),
        ):
            assert sum(u.amount for u in listed) == total
        assert unmatched.in_all == (
            unmatched.no_budget_line
            + unmatched.outside_ids
            + unmatched.no_department
            - unmatched.in_both
        )
        transaction.set_rollback(True)


@settings(
    max_examples=15,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(rows=postings)
def test_source_rows_add_up_to_the_total_under_every_page(rows) -> None:
    """Verify the source rows behind the whole report are exactly its lines,
    and add up to its total (ENG-03 6.2).
    """
    with transaction.atomic():
        report, _ = build(rows)
        found = list(queries.source_rows(report, ALL))
        totals = queries.totals(report)
        assert len(found) == totals.lines
        assert sum((r.amount for r in found), Decimal(0)) == totals.total
        transaction.set_rollback(True)
