"""Add up: the total and the groups in reports/queries.py, and
manage.py report_totals (ENG-03 2, ENG-01 7.2).

Every row is made by hand, so each expected figure is worked out here.
"""

import datetime
import io
import json
from decimal import Decimal

import pytest
from django.core.management import call_command

from ids_audit.ledger.models import GlCiMap, Posting
from ids_audit.reports import queries
from ids_audit.reports.models import Report
from ids_audit.reports.queries import Group, Totals
from tests.factories import (
    add_posting,
    commit,
    make_report,
    make_upload,
    map_cc,
    map_gl,
    roll_back,
)

pytestmark = pytest.mark.django_db

JUL = datetime.date(2025, 7, 8)
AUG = datetime.date(2025, 8, 20)
JAN = datetime.date(2026, 1, 4)


def a_report() -> Report:
    """A report whose one committed upload covers every case in ENG-02 8.2,
    with a posting that isn't actual spend, a rolled-back upload, and another
    report's posting, none of which may count.

    Actual spend that counts: 610100 at 4510 (100.000 in July, 50.000 in
    August), 610100 at 3100, outside IDS (20.000), 650200 at 4520, listed
    without a commitment item, at a cost center mapped with no department
    (3.000, January), and 640200 at 4510, missing from the GL mapping (7.000,
    August).
    """
    report = make_report()
    upload = make_upload(report)
    map_gl(upload, "610100", "2101")
    map_gl(upload, "650200", "")
    map_cc(upload, "4510", "APP")
    map_cc(upload, "4520", "")
    add_posting(upload, JUL, "100.000")
    add_posting(upload, AUG, "50.000")
    add_posting(upload, JUL, "20.000", cost_center_code="3100")
    add_posting(upload, JAN, "3.000", gl_code="650200", cost_center_code="4520")
    add_posting(upload, AUG, "7.000", gl_code="640200")
    not_actual = add_posting(upload, JUL, "999.000")
    Posting.objects.filter(pk=not_actual.pk).update(is_actual=False)
    commit(upload, minute=1, mode=Report.DuplicateMode.LINE_KEY)

    rolled_back = make_upload(report)
    add_posting(rolled_back, JUL, "5000.000")
    roll_back(commit(rolled_back, minute=2))

    other = make_upload(make_report())
    add_posting(other, JUL, "7000.000")
    commit(other, minute=3)
    return report


def test_totals_count_only_actual_spend_that_counts() -> None:
    """Verify the total, inside and outside IDS and the lines count only the
    report's actual spend that counts (ENG-03 2.1 and 2.2).
    """
    assert queries.totals(a_report()) == Totals(
        total=Decimal("180.000"),
        inside_ids=Decimal("160.000"),
        outside_ids=Decimal("20.000"),
        lines=5,
    )


def test_by_gl_account() -> None:
    """Verify one group per GL account in code order, with its name from the
    mapping in force, empty for one missing from it, and its share of the
    total rounded half up.
    """
    assert queries.by_gl_account(a_report()) == [
        Group("610100", "GL 610100", Decimal("170.000"), 3, Decimal("0.944")),
        Group("640200", "", Decimal("7.000"), 1, Decimal("0.039")),
        Group("650200", "GL 650200", Decimal("3.000"), 1, Decimal("0.017")),
    ]


def test_by_budget_line_puts_no_budget_line_last() -> None:
    """Verify a GL account missing from the mapping and one listed without a
    commitment item both fall into No budget line, keyed none and last.
    """
    assert queries.by_budget_line(a_report()) == [
        Group("2101", "CI 2101", Decimal("170.000"), 3, Decimal("0.944")),
        Group("none", "No budget line", Decimal("10.000"), 2, Decimal("0.056")),
    ]


def test_by_department_keeps_outside_ids_apart_and_last() -> None:
    """Verify Outside IDS is keyed outside and last, and a cost center inside
    IDS mapped with no department code makes No department, keyed none, not
    Outside IDS (ENG-03 2.3).
    """
    assert queries.by_department(a_report()) == [
        Group("APP", "Dept APP", Decimal("157.000"), 3, Decimal("0.872")),
        Group("none", "No department", Decimal("3.000"), 1, Decimal("0.017")),
        Group("outside", "Outside IDS", Decimal("20.000"), 1, Decimal("0.111")),
    ]


def test_by_month_in_date_order() -> None:
    """Verify one group per month of posting date, keyed by its first day and
    named as ENG-03 2.4 writes it, with the running total (ENG-03 2.2).
    """
    july, august, january = (
        datetime.date(2025, 7, 1),
        datetime.date(2025, 8, 1),
        datetime.date(2026, 1, 1),
    )
    assert queries.by_month(a_report()) == [
        Group(july, "Jul 2025", Decimal("120.000"), 2, None, Decimal("120.000")),
        Group(august, "Aug 2025", Decimal("57.000"), 2, None, Decimal("177.000")),
        Group(january, "Jan 2026", Decimal("3.000"), 1, None, Decimal("180.000")),
    ]


@pytest.mark.parametrize(
    "by",
    [
        queries.by_gl_account,
        queries.by_budget_line,
        queries.by_department,
        queries.by_month,
    ],
)
def test_every_grouping_adds_up_to_the_total(by) -> None:
    """Verify every posting falls into exactly one group, so the groups add up
    to the total and its lines (ENG-03 2.3).
    """
    report = a_report()
    totals = queries.totals(report)
    groups = by(report)
    assert sum(group.amount for group in groups) == totals.total
    assert sum(group.lines for group in groups) == totals.lines


def test_a_report_with_nothing_committed_has_zero_totals_and_no_groups() -> None:
    """Verify an empty report's totals are 0.000, not empty, and it has no
    groups (ENG-03 2).
    """
    report = make_report()
    make_upload(report)
    zero = Decimal("0.000")
    assert queries.totals(report) == Totals(zero, zero, zero, 0)
    assert str(queries.totals(report).total) == "0.000"
    for by in (
        queries.by_gl_account,
        queries.by_budget_line,
        queries.by_department,
        queries.by_month,
    ):
        assert by(report) == []


def test_a_code_given_two_names_by_own_upload_is_one_group() -> None:
    """Verify a GL account that own_upload gives a different name in each
    upload's mapping stays one group, with the name that sorts last
    (ENG-03 2.3).
    """
    report = make_report("own_upload")
    for minute, name in enumerate(["Software", "Software licences"], start=1):
        upload = make_upload(report)
        GlCiMap.objects.create(
            upload=upload, report=report, gl_code="610100", gl_name=name
        )
        add_posting(upload, JUL, "1.000")
        commit(upload, minute=minute, mode=Report.DuplicateMode.LINE_KEY)
    assert queries.by_gl_account(report) == [
        Group("610100", "Software licences", Decimal("2.000"), 2, Decimal("1.000"))
    ]


# manage.py report_totals (ENG-01 7.2)


def test_report_totals_prints_every_report_s_figures_as_text() -> None:
    """Verify the command prints each report's totals and groups in the
    golden files' names, every amount a string with three decimal places, in
    the order reports were created, with sorted keys.
    """
    report = a_report()
    out = io.StringIO()
    call_command("report_totals", stdout=out)
    printed = json.loads(out.getvalue())
    first, other = printed["reports"]
    assert first == {
        "report": report.pk,
        "name": report.name,
        "actual_spend": {
            "total": "180.000",
            "inside_ids": "160.000",
            "outside_ids": "20.000",
        },
        "lines": 5,
        "by_gl": {"610100": "170.000", "640200": "7.000", "650200": "3.000"},
        "by_ci": {"2101": "170.000", "none": "10.000"},
        "by_department": {"APP": "157.000", "none": "3.000", "outside": "20.000"},
        "by_month": {"2025-07": "120.000", "2025-08": "57.000", "2026-01": "3.000"},
    }
    assert other["report"] > report.pk
    assert other["actual_spend"]["total"] == "7000.000"
    assert out.getvalue().rstrip("\n") == json.dumps(printed, indent=2, sort_keys=True)


def test_report_totals_writes_an_empty_report_s_totals_as_0_000() -> None:
    """Verify a report with nothing committed prints 0.000, never null."""
    make_report()
    out = io.StringIO()
    call_command("report_totals", stdout=out)
    (printed,) = json.loads(out.getvalue())["reports"]
    assert printed["actual_spend"] == {
        "total": "0.000",
        "inside_ids": "0.000",
        "outside_ids": "0.000",
    }
    assert printed["by_month"] == {}
