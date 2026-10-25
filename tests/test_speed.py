"""Speed (ENG-03 10): with 200,000 generated postings over 10 uploads, every
screen answers within 2 seconds, and runs as many queries as with 100
postings. An export of every row fits Gunicorn's timeout, at a rate that
fits Excel's row limit too, and its memory stays flat as its rows grow.

The reports are built once for the module, in a transaction rolled back at
its end. PostgreSQL's autovacuum analyzes a table after a large insert; the
fixture runs ANALYZE at once instead of waiting for it.
"""

import datetime
import time
import tracemalloc

import pytest
from django.db import connection, transaction
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from config import gunicorn
from ids_audit.reports import export, queries
from ids_audit.reports.queries import ALL, Keys
from tests.factories import make_user
from tests.speed_data import UPLOADS, build

pytestmark = pytest.mark.django_db

LARGE = 200_000
SMALL = 100

# Every screen, as (address name, arguments, query string).
SCREENS = [
    ("overview", [], ""),
    ("gl_accounts", [], ""),
    ("gl_account", ["600001"], ""),
    ("departments", [], ""),
    ("department", ["APP"], ""),
    ("department", ["outside"], ""),
    ("department", ["none"], ""),
    ("budget_lines", [], ""),
    ("budget_line", ["2001"], ""),
    ("budget_line", ["none"], ""),
    ("rows", [], ""),
    ("rows", [], "?gl=600001&cc=4001"),
    ("rows", [], "?dept=outside&year=2025"),
    ("exceptions", [], ""),
    ("faq", [], ""),
    *[
        ("faq_page", [slug], "")
        for slug in (
            "total-spend",
            "largest-gl-accounts",
            "gl-budget-lines",
            "gl-account-cost-centers",
            "department-spend",
            "department-gl-accounts",
            "spend-over-time",
            "unmatched-spend",
        )
    ],
]


@pytest.fixture(scope="module")
def reports(django_db_setup, django_db_blocker):
    """A report of 100 postings and one of 200,000, with the same uploads,
    codes and mappings, so their screens run the same queries.
    """
    with django_db_blocker.unblock(), transaction.atomic():
        small, large = build(SMALL // UPLOADS), build(LARGE // UPLOADS)
        with connection.cursor() as cursor:
            cursor.execute("ANALYZE")
        yield small, large
        transaction.set_rollback(True)


@pytest.fixture
def client() -> Client:
    signed_in = Client()
    signed_in.force_login(make_user())
    return signed_in


def address(report, name: str, args: list, query: str) -> str:
    return reverse(name, args=[report.pk, *args]) + query


def test_every_screen_answers_within_2_seconds(reports, client) -> None:
    """Verify each screen of the 200,000-posting report answers within 2
    seconds on the developer's machine (ENG-03 10).
    """
    _, large = reports
    slow = {}
    for name, args, query in SCREENS:
        started = time.perf_counter()
        response = client.get(address(large, name, args, query))
        took = time.perf_counter() - started
        assert response.status_code == 200, (name, args)
        if took >= 2:
            slow[f"{name} {args} {query}"] = round(took, 2)
    assert slow == {}


def test_every_screen_runs_as_many_queries_at_any_size(reports, client) -> None:
    """Verify each screen runs the same number of queries with 100 postings as
    with 200,000, so no query is run per posting (ENG-03 10).
    """
    small, large = reports
    for name, args, query in SCREENS:
        counts = []
        for report in (small, large):
            with CaptureQueriesContext(connection) as captured:
                assert client.get(address(report, name, args, query)).status_code == 200
            counts.append(len(captured))
        assert counts[0] == counts[1], (name, args, query, counts)


def test_an_export_of_every_row_fits_gunicorn_s_timeout(reports, client) -> None:
    """Verify an export of the 200,000-posting report's source rows finishes
    at a rate that would write Excel's row limit within Gunicorn's timeout,
    so exports never need the background worker (ENG-03 9.4, R16).
    """
    _, large = reports
    started = time.perf_counter()
    response = client.get(reverse("rows_export", args=[large.pk]))
    data = b"".join(response.streaming_content)
    took = time.perf_counter() - started
    assert response.status_code == 200 and data
    rows = queries.totals(large).lines
    seconds_at_the_limit = took * export.MAX_DATA_ROWS / rows
    assert seconds_at_the_limit < gunicorn.timeout, round(seconds_at_the_limit)


def peak_memory(client: Client, url: str) -> int:
    """Return the most memory Python held while exporting url, in bytes."""
    tracemalloc.start()
    try:
        response = client.get(url)
        b"".join(response.streaming_content)
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def test_an_export_s_memory_stays_flat_as_its_rows_grow(reports, client) -> None:
    """Verify an export of about 20,000 rows holds no more memory than one of
    about 4,000, give or take 5 MB, since XlsxWriter writes rows as they come
    (ENG-03 9.4).
    """
    _, large = reports
    url = reverse("rows_export", args=[large.pk])
    fewer = peak_memory(client, url + "?gl=600001")
    more = peak_memory(client, url + "?through=2025-01")
    assert more < fewer + 5_000_000, (fewer, more)


@pytest.mark.parametrize(
    ("keys", "index"),
    [
        (Keys(gl="600001"), "posting_report_gl"),
        (Keys(cc="4001"), "posting_report_cc"),
        (Keys(month=datetime.date(2025, 3, 1)), "posting_report_date"),
    ],
)
def test_each_drill_down_uses_its_posting_index(reports, keys, index) -> None:
    """Verify PostgreSQL reads a drill-down by GL account, cost center or
    month through its own index on posting (ENG-03 10).
    """
    _, large = reports
    sql, params = queries.source_rows(large, ALL, keys).query.sql_with_params()
    with connection.cursor() as cursor:
        cursor.execute("EXPLAIN " + sql, params)
        plan = "\n".join(row[0] for row in cursor.fetchall())
    assert index in plan, plan
