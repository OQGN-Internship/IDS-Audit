"""The charts' data (ENG-03 5): each chart gets exactly its table's figures,
already written by the server, through json_script, and a name from a
mapping sheet stays text. Drawing them is the browser tests' part.
"""

import json
import re
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse

from ids_audit.ledger.models import GlCiMap
from ids_audit.reports.models import Report
from ids_audit.reports.templatetags.figures import amount
from tests.factories import add_posting, commit, make_report, make_upload, make_user
from tests.test_queries import JUL

pytestmark = pytest.mark.django_db

SCRIPT = '<img src=x onerror="alert(1)">'


@pytest.fixture
def client() -> Client:
    signed_in = Client()
    signed_in.force_login(make_user())
    return signed_in


def open_page(client: Client, name: str, report: Report, *args) -> str:
    response = client.get(reverse(name, args=[report.pk, *args]))
    assert response.status_code == 200
    return response.content.decode()


def chart(text: str, chart_id: str) -> list[dict]:
    """Return the points a page gives one chart, read as charts.js reads them."""
    found = re.search(
        rf'<script id="{chart_id}" type="application/json">(.*?)</script>', text, re.S
    )
    assert found, chart_id
    return json.loads(found[1])


def table_rows(text: str, title: str) -> list[list[str]]:
    """Return the cells of a table's body, as text, with their links."""
    body = re.search(rf"<h2>{title}</h2>.*?<tbody>(.*?)</tbody>", text, re.S)[1]
    return [
        re.findall(r"<td[^>]*>(.*?)</td>", row)
        for row in re.findall(r"<tr>(.*?)</tr>", body, re.S)
    ]


def link_and_text(cell: str) -> tuple[str, str]:
    """A cell's link and its text."""
    found = re.match(r'<a href="([^"]*)">(.*)</a>', cell)
    return (found[1].replace("&amp;", "&"), found[2]) if found else ("", cell)


def test_the_gl_chart_gets_exactly_its_table_s_rows(client, sample_report) -> None:
    """Verify the overview's GL chart has one point per row of its table, in
    the same order, each with the row's code, amount and link, the others
    included (ENG-03 5).
    """
    text = open_page(client, "overview", sample_report)
    points = chart(text, "chart-gl")
    rows = table_rows(text, "Largest GL accounts")
    assert len(points) == len(rows) == 11
    for point, row in zip(points, rows, strict=True):
        code = link_and_text(row[0])[1] or link_and_text(row[1])[1]
        link, written = link_and_text(row[2])
        assert (point["label"], point["text"], point["link"]) == (code, written, link)
        assert amount(point["value"]) == written
    assert points[-1]["label"] == "All other GL accounts (2)"
    assert points[-1]["link"] == reverse("gl_accounts", args=[sample_report.pk])


def test_the_running_total_chart_gets_the_running_total_column(
    client, sample_report, sample_expected
) -> None:
    """Verify the running total chart's points are the running total by month,
    each opening every month up to it.
    """
    text = open_page(client, "overview", sample_report)
    points = chart(text, "chart-running")
    assert [p["value"] for p in points] == list(
        sample_expected["screens"]["running_total"].values()
    )
    assert points[0]["label"] == "Jul 2025"
    assert points[0]["link"].endswith("rows?through=2025-07")
    assert "chart-monthly" not in text


def test_a_gl_account_has_a_running_total_and_a_monthly_chart(
    client, sample_report
) -> None:
    """Verify a GL account's screen pictures its months twice, each chart from
    its own column.
    """
    text = open_page(client, "gl_account", sample_report, "610100")
    rows = table_rows(text, "By month")
    monthly = chart(text, "chart-monthly")
    running = chart(text, "chart-running")
    assert [p["text"] for p in monthly] == [link_and_text(r[1])[1] for r in rows]
    assert [p["text"] for p in running] == [link_and_text(r[2])[1] for r in rows]


def test_outside_ids_stands_out_on_the_department_chart(client, sample_report) -> None:
    """Verify the departments chart marks Outside IDS, last, and the legend
    names it.
    """
    text = open_page(client, "departments", sample_report)
    points = chart(text, "chart-departments")
    assert [p["outside"] for p in points] == [False] * 4 + [True]
    assert points[-1]["label"] == "Outside IDS"
    assert '<span class="swatch outside"></span>Outside IDS' in text


def test_a_negative_gl_account_reaches_its_chart_below_zero(client) -> None:
    """Verify a GL account whose credits exceed its spend reaches the chart as
    a negative value, which the value axis draws to the left of zero.
    """
    report = make_report()
    upload = make_upload(report)
    add_posting(upload, JUL, "2000.000", gl_code="620200")
    add_posting(upload, JUL, "-3500.000", gl_code="620200")
    add_posting(upload, JUL, "100.000", gl_code="610100")
    commit(upload, minute=1)
    points = chart(open_page(client, "overview", report), "chart-gl")
    assert [(p["label"], p["value"]) for p in points] == [
        ("610100", "100.000"),
        ("620200", "-1500.000"),
    ]


def test_a_name_with_a_script_stays_text_in_the_chart_and_table(client) -> None:
    """Verify a GL account named with a script tag in the mapping reaches the
    chart's data escaped inside its json_script, and the table as text, so it
    can't run (ENG-03 5, N7).
    """
    report = make_report()
    upload = make_upload(report)
    GlCiMap.objects.create(
        upload=upload, report=report, gl_code="610100", gl_name=SCRIPT, ci_code="2101"
    )
    add_posting(upload, JUL, "1.000")
    commit(upload, minute=1)
    text = open_page(client, "overview", report)
    assert SCRIPT not in text
    # json_script writes < and > as \u003C and \u003E, so no tag can end the script.
    assert r"\u003Cimg src=x onerror=\"alert(1)\"\u003E" in text
    (point,) = chart(text, "chart-gl")
    assert point["name"] == SCRIPT
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in text


def test_echarts_loads_only_on_a_page_with_a_chart(client, sample_report) -> None:
    """Verify ECharts, kept in the repository, loads on the overview and not
    on a screen without a chart, such as GL accounts.
    """
    assert "js/echarts-5.6.0.min.js" in open_page(client, "overview", sample_report)
    assert "js/charts.js" in open_page(client, "overview", sample_report)
    assert "echarts" not in open_page(client, "gl_accounts", sample_report)


def test_every_chart_value_is_text_for_drawing_only(client, sample_report) -> None:
    """Verify each point carries its amount as exact text, never a number the
    browser could add, and its label already written by the server.
    """
    text = open_page(client, "departments", sample_report)
    for point in chart(text, "chart-departments"):
        assert isinstance(point["value"], str)
        assert point["text"] == amount(Decimal(point["value"]))
