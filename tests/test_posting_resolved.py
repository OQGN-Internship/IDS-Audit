"""posting_resolved: each posting that counts, with its mapping in force
(ENG-02 8.4, 10.2, ENG-01 6.3).

Every row is made by hand, so each expected posting, total and mapping is
worked out here, not by the view.
"""

import datetime
from decimal import Decimal

import pytest
from django.db import connection
from django.db.utils import OperationalError

from ids_audit.imports.models import Upload
from ids_audit.ledger.models import Posting
from ids_audit.pipeline.ruleset import DEFAULT_SETTINGS
from ids_audit.reports.models import Report, Ruleset
from tests.factories import (
    add_posting,
    commit,
    make_report,
    make_upload,
    map_cc,
    map_gl,
    roll_back,
)
from tests.roles import as_app

pytestmark = pytest.mark.django_db

REPLACE_BY_PERIOD = Report.DuplicateMode.REPLACE_BY_PERIOD
LINE_KEY = Report.DuplicateMode.LINE_KEY

JAN = datetime.date(2026, 1, 10)
FEB = datetime.date(2026, 2, 10)
MAR = datetime.date(2026, 3, 10)

# PostgreSQL's code for "cannot update view": it can't pass a write through a
# view that joins tables, whatever the rights (ENG-01 6.3).
VIEW_NOT_UPDATABLE = "55000"


def resolved(report: Report) -> dict[int, dict]:
    """The view's rows for a report, by posting id."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT * FROM posting_resolved WHERE report_id = %s", [report.pk]
        )
        names = [column.name for column in cursor.description]
        rows = [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
    return {row["id"]: row for row in rows}


def view_total(report: Report) -> Decimal:
    """The view's total amount for a report, added up by PostgreSQL."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT sum(amount) FROM posting_resolved WHERE report_id = %s",
            [report.pk],
        )
        return cursor.fetchone()[0]


def lesson_2(mode: str, mapping_rule: str = "latest") -> tuple[Report, dict]:
    """The three uploads of Lesson 2, all for 610100 at 4510.

    Upload 1 is committed first, upload 2 next and then rolled back, and
    upload 3 last. Each maps 610100 to its own commitment item.
    """
    report = make_report(mapping_rule)
    first = make_upload(report)
    p1 = add_posting(first, JAN, "100.000")
    p2 = add_posting(first, FEB, "200.000")
    map_gl(first, "610100", "2101")
    map_cc(first, "4510", "APP")
    commit(first, minute=1, mode=mode)

    second = make_upload(report)
    p3 = add_posting(second, FEB, "999.000")
    map_gl(second, "610100", "2301")
    map_cc(second, "4510", "APP")
    roll_back(commit(second, minute=2))

    third = make_upload(report)
    p4 = add_posting(third, FEB, "250.000")
    p5 = add_posting(third, MAR, "300.000")
    map_gl(third, "610100", "2401")
    map_cc(third, "4510", "APP")
    commit(third, minute=3)
    return report, {"P1": p1, "P2": p2, "P3": p3, "P4": p4, "P5": p5, "third": third}


def names(rows: dict[int, dict], made: dict) -> set[str]:
    """The Lesson 2 names of the postings among the view's rows."""
    return {name for name, row in made.items() if row.pk in rows and name != "third"}


# Which postings count (ENG-02 10.2)


@pytest.mark.parametrize(
    ("mode", "counted", "total"),
    [
        (REPLACE_BY_PERIOD, {"P1", "P4", "P5"}, "650.000"),
        (LINE_KEY, {"P1", "P2", "P4", "P5"}, "850.000"),
    ],
)
def test_one_row_per_posting_that_counts_and_the_same_total(
    mode: str, counted: set[str], total: str
) -> None:
    """Verify Lesson 2's example: the view has exactly the postings that count,
    and its total equals theirs. P3's upload was rolled back. In
    replace-by-period mode, upload 3 replaces P2's month.
    """
    report, made = lesson_2(mode)
    rows = resolved(report)
    assert names(rows, made) == counted
    assert len(rows) == len(counted)
    assert view_total(report) == sum(made[name].amount for name in counted)
    assert view_total(report) == Decimal(total)


def test_rolling_back_brings_back_what_the_upload_replaced() -> None:
    """Verify that after upload 3 is rolled back, P1 and P2 count again under
    upload 1's mapping, as the Lesson 2 check worked out (ENG-02 10.3).
    """
    report, made = lesson_2(REPLACE_BY_PERIOD)
    roll_back(made["third"])
    rows = resolved(report)
    assert names(rows, made) == {"P1", "P2"}
    assert view_total(report) == Decimal("300.000")
    assert {row["ci_code"] for row in rows.values()} == {"2101"}


@pytest.mark.parametrize(
    "status", [status for status in Upload.Status if status != Upload.Status.COMMITTED]
)
def test_postings_count_only_while_their_upload_is_committed(status: str) -> None:
    """Verify a posting whose upload is in any other state is left out, beside
    a committed upload's posting that counts.
    """
    report = make_report()
    committed = make_upload(report)
    counts = add_posting(committed, JAN, "100.000")
    commit(committed, minute=1)
    other = make_upload(report, status=status)
    add_posting(other, FEB, "200.000")
    assert list(resolved(report)) == [counts.pk]


@pytest.mark.parametrize(
    ("mode", "kept"),
    [
        (REPLACE_BY_PERIOD, {"31 Jan", "1 Apr"}),
        (LINE_KEY, {"31 Jan", "1 Feb", "31 Mar", "1 Apr"}),
    ],
)
def test_a_later_upload_replaces_every_day_of_the_months_it_covers(
    mode: str, kept: set[str]
) -> None:
    """Verify a later upload dated 15 Feb to 3 Mar covers all of February and
    March (ENG-02 7.3): in replace-by-period mode the earlier postings dated
    1 Feb and 31 Mar stop counting, and 31 Jan and 1 Apr don't. In line-key
    mode nothing is replaced.
    """
    report = make_report()
    first = make_upload(report)
    earlier = {
        "31 Jan": add_posting(first, datetime.date(2026, 1, 31), "1.000"),
        "1 Feb": add_posting(first, datetime.date(2026, 2, 1), "2.000"),
        "31 Mar": add_posting(first, datetime.date(2026, 3, 31), "3.000"),
        "1 Apr": add_posting(first, datetime.date(2026, 4, 1), "4.000"),
    }
    commit(first, minute=1, mode=mode)
    second = make_upload(report)
    later = [
        add_posting(second, datetime.date(2026, 2, 15), "20.000"),
        add_posting(second, datetime.date(2026, 3, 3), "30.000"),
    ]
    commit(second, minute=2)
    expected = {earlier[day].pk for day in kept} | {p.pk for p in later}
    assert set(resolved(report)) == expected


def test_the_upload_committed_last_wins_whatever_its_number() -> None:
    """Verify upload 1, committed after upload 2, replaces upload 2's month and
    gives the mapping in force (ENG-02 10.2).
    """
    report = make_report()
    one, two = make_upload(report), make_upload(report)
    from_one = add_posting(one, FEB, "100.000")
    add_posting(two, FEB, "200.000")
    map_gl(one, "610100", "2101")
    map_gl(two, "610100", "2401")
    commit(two, minute=1)
    commit(one, minute=2)
    rows = resolved(report)
    assert list(rows) == [from_one.pk]
    assert rows[from_one.pk]["ci_code"] == "2101"


def test_two_commits_at_the_same_time_are_ordered_by_number() -> None:
    """Verify that of two uploads with the same decided_at, upload 2 counts as
    committed after upload 1 (ENG-02 10.2).
    """
    report = make_report()
    one, two = make_upload(report), make_upload(report)
    add_posting(one, FEB, "100.000")
    from_two = add_posting(two, FEB, "200.000")
    map_gl(one, "610100", "2101")
    map_gl(two, "610100", "2401")
    commit(two, minute=1)
    commit(one, minute=1)
    rows = resolved(report)
    assert list(rows) == [from_two.pk]
    assert rows[from_two.pk]["ci_code"] == "2401"


# The mapping in force (ENG-02 8.1 and 8.2)


@pytest.mark.parametrize(
    ("mapping_rule", "expected"),
    [
        ("latest", {"P1": "2401", "P2": "2401", "P4": "2401", "P5": "2401"}),
        ("own_upload", {"P1": "2101", "P2": "2101", "P4": "2401", "P5": "2401"}),
    ],
)
def test_latest_and_own_upload_give_different_mappings_after_a_move(
    mapping_rule: str, expected: dict[str, str]
) -> None:
    """Verify Lesson 2's mappings, in line-key mode so P2 counts. The
    rolled-back upload's 2301 is never used.
    """
    report, made = lesson_2(LINE_KEY, mapping_rule)
    rows = resolved(report)
    shown = {name: rows[made[name].pk]["ci_code"] for name in expected}
    assert shown == expected


def test_own_upload_uses_the_latest_mapping_for_an_upload_without_one() -> None:
    """Verify a posting whose upload brought no GL mapping, such as a CSV,
    falls back to the latest rule under own_upload (ENG-02 8.1).
    """
    report = make_report("own_upload")
    first = make_upload(report)
    from_first = add_posting(first, JAN, "100.000")
    map_gl(first, "610100", "2101")
    commit(first, minute=1)
    second = make_upload(report)
    map_gl(second, "610100", "2401")
    add_posting(second, FEB, "200.000")
    commit(second, minute=2)
    csv = make_upload(report)
    from_csv = add_posting(csv, MAR, "300.000")
    commit(csv, minute=3)
    rows = resolved(report)
    assert rows[from_first.pk]["ci_code"] == "2101"
    assert rows[from_csv.pk]["ci_code"] == "2401"


def test_the_view_reads_the_latest_ruleset_version() -> None:
    """Verify a report whose version 1 says latest and version 2 says
    own_upload follows version 2 (ENG-02 3).
    """
    report, made = lesson_2(LINE_KEY, "latest")
    Ruleset.objects.create(
        report=report,
        version=2,
        settings={**DEFAULT_SETTINGS, "mapping_rule": "own_upload"},
        created_by=report.created_by,
    )
    assert resolved(report)[made["P1"].pk]["ci_code"] == "2101"


def test_ci_status_tells_the_three_gl_account_cases_apart() -> None:
    """Verify mapped, listed_without_ci and missing_from_gl_map, with ci_code
    and ci_name empty unless mapped, and empty text never NULL (ENG-02 8.4).
    """
    report = make_report()
    upload = make_upload(report)
    map_gl(upload, "610100", "2101")
    map_gl(upload, "650200", "", ci_name="a name with no code")
    postings = {
        gl: add_posting(upload, JAN, "1.000", gl_code=gl)
        for gl in ("610100", "650200", "640200")
    }
    commit(upload, minute=1)
    rows = resolved(report)
    shown = {
        gl: tuple(rows[p.pk][c] for c in ("gl_name", "ci_code", "ci_name", "ci_status"))
        for gl, p in postings.items()
    }
    assert shown == {
        "610100": ("GL 610100", "2101", "CI 2101", "mapped"),
        "650200": ("GL 650200", "", "", "listed_without_ci"),
        "640200": ("", "", "", "missing_from_gl_map"),
    }


def test_a_cost_center_missing_from_the_mapping_is_outside_ids() -> None:
    """Verify 3100, missing from the cost center mapping in force, is outside
    IDS with its name and department empty, and 4510 isn't (ENG-02 8.2).
    """
    report = make_report()
    upload = make_upload(report)
    map_cc(upload, "4510", "APP")
    inside = add_posting(upload, JAN, "1.000", cost_center_code="4510")
    outside = add_posting(upload, JAN, "2.000", cost_center_code="3100")
    commit(upload, minute=1)
    rows = resolved(report)
    columns = ("cc_name", "dept_code", "dept_name", "is_outside_ids")
    assert tuple(rows[inside.pk][c] for c in columns) == (
        "CC 4510",
        "APP",
        "Dept APP",
        False,
    )
    assert tuple(rows[outside.pk][c] for c in columns) == ("", "", "", True)


def test_latest_takes_the_newest_mapping_whole() -> None:
    """Verify that once a later file's mapping leaves out 4510, 4510 is outside
    IDS under latest, even though the first file mapped it (ENG-02 8.1).
    """
    report = make_report()
    first = make_upload(report)
    from_first = add_posting(first, JAN, "1.000", cost_center_code="4510")
    map_cc(first, "4510", "APP")
    commit(first, minute=1)
    second = make_upload(report)
    add_posting(second, FEB, "2.000", cost_center_code="4520")
    map_cc(second, "4520", "APP")
    commit(second, minute=2)
    assert resolved(report)[from_first.pk]["is_outside_ids"] is True


# The view's columns and rights (ENG-02 8.4, ENG-01 6.3)

MAPPING_COLUMNS = [
    ("gl_name", "text"),
    ("ci_code", "text"),
    ("ci_name", "text"),
    ("ci_status", "text"),
    ("cc_name", "text"),
    ("dept_code", "text"),
    ("dept_name", "text"),
    ("is_outside_ids", "boolean"),
]


def columns(table: str) -> list[tuple[str, str]]:
    """A table's or view's columns and their types, in order."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_name, data_type FROM information_schema.columns"
            " WHERE table_name = %s ORDER BY ordinal_position",
            [table],
        )
        return cursor.fetchall()


def test_the_view_has_every_posting_column_then_the_mapping_columns() -> None:
    """Verify the view's columns are posting's, unchanged and in order, then the
    eight mapping columns of ENG-02 8.4.
    """
    assert columns("posting_resolved") == columns("posting") + MAPPING_COLUMNS


def test_ids_app_can_read_the_view(upload: Upload) -> None:
    """Verify SELECT through the view works as ids_app, the pair of the refusals
    below.
    """
    add_posting(upload, JAN, "1.000")
    commit(upload, minute=1)
    with as_app() as cursor:
        cursor.execute("SELECT count(*) FROM posting_resolved")
        assert cursor.fetchone()[0] == 1


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO posting_resolved (id) VALUES (999)",
        "UPDATE posting_resolved SET amount = 0",
        "DELETE FROM posting_resolved",
    ],
)
def test_ids_app_cannot_write_through_the_view(statement: str, upload: Upload) -> None:
    """Verify PostgreSQL refuses INSERT, UPDATE and DELETE through the view as
    ids_app, and posting is unchanged (ENG-01 6.3).
    """
    posting = add_posting(upload, JAN, "1.000")
    commit(upload, minute=1)
    with pytest.raises(OperationalError) as refused, as_app() as cursor:
        cursor.execute(statement)
    assert refused.value.__cause__.sqlstate == VIEW_NOT_UPDATABLE
    assert list(Posting.objects.values_list("pk", "amount")) == [
        (posting.pk, Decimal("1.000"))
    ]


def test_ids_app_holds_only_select_on_every_view() -> None:
    """Verify sql/grants.sql leaves ids_app only SELECT on each view (ENG-01
    6.3). This is the test that guards the grants, since a view that joins
    tables refuses writes on its own.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT viewname,"
            " has_table_privilege('ids_app', viewname, 'SELECT'),"
            " has_table_privilege('ids_app', viewname, 'INSERT'),"
            " has_table_privilege('ids_app', viewname, 'UPDATE'),"
            " has_table_privilege('ids_app', viewname, 'DELETE')"
            " FROM pg_views WHERE schemaname = 'public'"
        )
        rights = cursor.fetchall()
    assert rights == [("posting_resolved", True, False, False, False)]
