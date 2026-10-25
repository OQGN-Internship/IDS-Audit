"""The report screens through Django's test client (ENG-03 3, 4, 6 and 8).

The figures themselves are the queries' (tests/test_report_queries.py and
tests/test_report_sample.py). These tests check that each screen shows them,
links each amount to the rows behind it, keeps the filters, and refuses an
address it doesn't take.
"""

import html
import re
from decimal import Decimal
from pathlib import Path

import pytest
from django.test import Client
from django.urls import reverse

from ids_audit.accounts import access
from ids_audit.imports.models import SkippedRow, Upload
from ids_audit.reports.models import Report
from ids_audit.reports.templatetags.figures import amount
from tests.factories import (
    add_posting,
    commit,
    make_report,
    make_upload,
    make_user,
    map_cc,
    map_gl,
)
from tests.factories import roll_back as mark_rolled_back
from tests.test_queries import JUL

pytestmark = pytest.mark.django_db

TEMPLATES = Path(__file__).parent.parent / "ids_audit" / "templates"
# A link to source rows, and the amount it's written on.
ROWS_LINK = re.compile(r'<a href="(/reports/\d+/rows[^"]*)">([-0-9,.]+)</a>')
ROWS_SAID = re.compile(r"(\d+) rows?, adding up to ([-0-9,.]+) OMR\.")


@pytest.fixture
def client() -> Client:
    """A browser signed in as a fixed administrator."""
    signed_in = Client()
    signed_in.force_login(make_user())
    return signed_in


def get(client: Client, name: str, report: Report, *args, query: str = "", **kw):
    """Open a screen of the report and return the response."""
    return client.get(reverse(name, args=[report.pk, *args]) + query, **kw)


def text(response) -> str:
    """Return a response's page, checking it opened."""
    assert response.status_code == 200, response.status_code
    return response.content.decode()


def rows_said(client: Client, url: str) -> tuple[int, str]:
    """Open source rows and return the count and total they say."""
    found = ROWS_SAID.search(text(client.get(html.unescape(url))))
    return int(found[1]), found[2]


# The screens and their figures


def test_a_report_s_address_opens_its_overview(client, sample_report) -> None:
    """Verify /reports/<id>/ opens the overview, whose page links each of the
    report's screens (ENG-03 4).
    """
    response = client.get(reverse("report", args=[sample_report.pk]))
    assert response["Location"] == reverse("overview", args=[sample_report.pk])
    page = text(get(client, "overview", sample_report))
    page = re.search(r'<nav class="sidebar"[^>]*>(.*?)</nav>', page, re.S)[1]
    for name in (
        "overview",
        "gl_accounts",
        "departments",
        "budget_lines",
        "exceptions",
        "faq",
    ):
        assert f'href="{reverse(name, args=[sample_report.pk])}"' in page, name
    assert f'href="{reverse("uploads", args=[sample_report.pk])}"' in page


def test_the_report_list_links_each_report_to_its_own_address(
    client, sample_report
) -> None:
    """Verify the report list opens a report at its own address."""
    page = text(client.get(reverse("reports")))
    assert f'href="{reverse("report", args=[sample_report.pk])}"' in page


def test_the_overview_shows_the_totals_largest_gl_accounts_and_months(
    client, sample_report, sample_expected
) -> None:
    """Verify the overview shows the total inside and outside IDS, the 10
    largest GL accounts and one row for the other 2, and every month with its
    running total.
    """
    page = text(get(client, "overview", sample_report))
    spend = sample_expected["actual_spend"]
    for figure in (spend["total"], spend["inside_ids"], spend["outside_ids"]):
        assert amount(figure) in page
    assert "All other GL accounts (2)" in page
    for running in sample_expected["screens"]["running_total"].values():
        assert amount(running) in page
    assert "Figures as of upload 1, sample.xlsx, approved by " in page


def test_the_overview_shows_its_actual_spend_as_tiles(
    client, sample_report, sample_expected
) -> None:
    """Verify the overview's Actual spend shows as tiles, the total first,
    each amount linking to rows that add up to it, with its lines, and
    without Outside IDS once it's excluded (ENG-03 4).
    """
    tile = re.compile(
        r'<div class="tile"><p>([^<]+)</p><p class="figure"><a href="([^"]+)">'
        r"([-0-9,.]+)</a> OMR</p><p>(\d+) lines?</p></div>"
    )
    tiles = tile.findall(text(get(client, "overview", sample_report)))
    spend = sample_expected["actual_spend"]
    assert [(label, written) for label, _, written, _ in tiles] == [
        ("Actual spend", amount(spend["total"])),
        ("On IDS cost centers", amount(spend["inside_ids"])),
        ("Outside IDS", amount(spend["outside_ids"])),
    ]
    for _, url, written, lines in tiles:
        assert rows_said(client, url) == (int(lines), written)
    page = text(get(client, "overview", sample_report, query="?exclude_outside=1"))
    assert [label for label, *_ in tile.findall(page)] == [
        "Actual spend",
        "On IDS cost centers",
    ]


@pytest.mark.parametrize(
    "name",
    ["overview", "gl_accounts", "departments", "budget_lines"],
)
def test_every_amount_opens_source_rows_that_add_up_to_it(
    client, sample_report, name
) -> None:
    """Verify every amount on a list screen links to source rows, and those
    rows add up to it, except the row for all the other GL accounts, which
    opens the GL accounts screen (ENG-03 6).
    """
    links = ROWS_LINK.findall(text(get(client, name, sample_report)))
    assert links
    for url, written in links:
        assert rows_said(client, url)[1] == written, url


def test_each_gl_account_s_screen_adds_up_to_its_figure(
    client, sample_report, sample_expected
) -> None:
    """Verify each GL account's screen shows its expected amount by cost
    center, and every amount on it opens rows that add up to it.
    """
    for gl, by_center in sample_expected["screens"]["gl_by_cost_center"].items():
        page = text(get(client, "gl_account", sample_report, gl))
        assert amount(sample_expected["by_gl"][gl]) in page
        for figure in by_center.values():
            assert amount(figure) in page
        for url, written in ROWS_LINK.findall(page):
            assert rows_said(client, url)[1] == written, url


@pytest.mark.parametrize("key", ["APP", "SEC", "outside"])
def test_a_department_s_screen_lists_its_gl_accounts(
    client, sample_report, sample_expected, key
) -> None:
    """Verify a department's screen, Outside IDS included, shows its spend by
    GL account, each opening rows that add up to it.
    """
    page = text(get(client, "department", sample_report, key))
    for figure in sample_expected["screens"]["department_by_gl"][key].values():
        assert amount(figure) in page
    for url, written in ROWS_LINK.findall(page):
        assert rows_said(client, url)[1] == written, url
    if key == "outside":
        assert "<h1>Outside IDS</h1>" in page


def test_a_budget_line_s_screen_lists_its_gl_accounts(
    client, sample_report, sample_expected
) -> None:
    """Verify No budget line's screen lists 640200 and 650200."""
    page = text(get(client, "budget_line", sample_report, "none"))
    for figure in sample_expected["screens"]["budget_line_by_gl"]["none"].values():
        assert amount(figure) in page
    assert "<h1>No budget line</h1>" in page


def test_every_list_ends_with_a_total_row_equal_to_its_figure(
    client, sample_report, sample_expected
) -> None:
    """Verify each list screen's tables end with the report's total, and a
    GL account's with the GL account's (ENG-03 4).
    """
    total = amount(sample_expected["actual_spend"]["total"])
    for name in ("overview", "gl_accounts", "departments", "budget_lines"):
        page = text(get(client, name, sample_report))
        footers = re.findall(r"<tfoot>(.*?)</tfoot>", page, re.S)
        assert footers and all(total in footer for footer in footers), name
    page = text(get(client, "gl_account", sample_report, "610100"))
    footers = re.findall(r"<tfoot>(.*?)</tfoot>", page, re.S)
    assert all(amount(sample_expected["by_gl"]["610100"]) in f for f in footers)


def test_the_same_screen_lists_its_rows_in_the_same_order_every_time(
    client, sample_report
) -> None:
    """Verify two visits to a screen give the same panel. The whole page
    differs only by its CSRF token.
    """
    for name in ("gl_accounts", "rows"):
        first = text(get(client, name, sample_report, headers={"HX-Request": "true"}))
        again = get(client, name, sample_report, headers={"HX-Request": "true"})
        assert text(again) == first


def test_lists_run_highest_first_with_the_catch_alls_last(
    client, sample_report
) -> None:
    """Verify the departments screen lists departments by actual spend, then
    Outside IDS last whatever its amount (ENG-03 4).
    """
    page = text(get(client, "departments", sample_report))
    body = re.search(r"<tbody>(.*?)</tbody>", page, re.S)[1]
    order = [m for m in re.findall(r">(APP|INF|SEC|GOV|Outside IDS)<", body)]
    assert order == ["APP", "INF", "SEC", "GOV", "Outside IDS"]
    # A catch-all has no code, so its name links to its screen.
    outside = reverse("department", args=[sample_report.pk, "outside"])
    assert f'<td class="text"><a href="{outside}">Outside IDS</a></td>' in page
    assert ">outside<" not in page


def test_the_gl_accounts_total_row_puts_its_lines_under_lines(
    client, sample_report
) -> None:
    """Verify the GL accounts screen's total row has the lines under Lines,
    and nothing under Cost centers.
    """
    page = text(get(client, "gl_accounts", sample_report))
    footer = re.search(r"<tfoot>(.*?)</tfoot>", page, re.S)[1]
    assert footer.endswith('<td class="count">35</td><td class="text"></td></tr>')


def own_upload_report() -> Report:
    """A report under own_upload whose two uploads put 610100 under 2101 and
    2201, and cost center 4510 under APP and INF (ENG-03 2.3).
    """
    report = make_report("own_upload")
    for minute, (ci, dept, written) in enumerate(
        [("2101", "APP", "10.000"), ("2201", "INF", "30.000")], start=1
    ):
        upload = make_upload(report)
        map_gl(upload, "610100", ci)
        map_cc(upload, "4510", dept)
        add_posting(upload, JUL, written)
        commit(upload, minute=minute, mode=Report.DuplicateMode.LINE_KEY)
    return report


def test_a_gl_account_under_several_budget_lines_splits_on_its_screens(
    client,
) -> None:
    """Verify under own_upload the GL accounts screen shows Several (2), and
    each part on the GL account's and budget line's screens opens rows that
    add up to it (ENG-03 2.3, G19).
    """
    report = own_upload_report()
    assert "Several (2)" in text(get(client, "gl_accounts", report))
    for name, args in (("gl_account", ["610100"]), ("budget_line", ["2101"])):
        page = text(get(client, name, report, *args))
        links = ROWS_LINK.findall(page)
        assert links
        for url, written in links:
            assert rows_said(client, url)[1] == written, url
    assert "40.000" not in text(get(client, "budget_line", report, "2101"))


# Source rows (ENG-03 6)


def test_source_rows_say_their_keys_count_and_total(client, sample_report) -> None:
    """Verify the source rows screen names its keys, and its count and total
    cover every page.
    """
    page = text(get(client, "rows", sample_report, query="?gl=610100&cc=4510"))
    assert "<h1>GL account 610100, cost center 4510</h1>" in page
    assert "2 rows, adding up to 11,150.000 OMR." in page


def test_source_rows_come_100_to_a_page(client) -> None:
    """Verify 101 rows make two pages, the second with one row, and a page
    past the last is 404 (ENG-03 6.1).
    """
    report = make_report()
    upload = make_upload(report)
    for _ in range(101):
        add_posting(upload, JUL, "1.000")
    commit(upload, minute=1)
    first = text(get(client, "rows", report))
    assert first.count('<td class="amount">1.000</td>') == 100
    assert "101 rows, adding up to 101.000 OMR. Page 1 of 2." in first
    second = text(get(client, "rows", report, query="?page=2"))
    assert second.count('<td class="amount">1.000</td>') == 1
    assert ">Previous</a>" in second
    assert get(client, "rows", report, query="?page=3").status_code == 404


def test_a_source_row_names_its_catch_alls(client, sample_report) -> None:
    """Verify a source row with no budget line, or outside IDS, says so."""
    page = text(get(client, "rows", sample_report, query="?gl=640200&cc=3200"))
    assert '<td class="text">No budget line</td>' in page
    assert '<td class="text">Outside IDS</td>' in page


def test_the_last_page_of_source_rows_has_no_next_page(client) -> None:
    """Verify the last page links to the one before it and to no later one."""
    report = make_report()
    upload = make_upload(report)
    for _ in range(101):
        add_posting(upload, JUL, "1.000")
    commit(upload, minute=1)
    assert ">Next</a>" in text(get(client, "rows", report))
    last = text(get(client, "rows", report, query="?page=2"))
    assert ">Previous</a>" in last and ">Next</a>" not in last


def no_department_report() -> Report:
    """A report with cost center 4520 listed in the mapping with no
    department code, beside 4510 in APP.
    """
    report = make_report()
    upload = make_upload(report)
    map_cc(upload, "4510", "APP")
    map_cc(upload, "4520", "")
    add_posting(upload, JUL, "5.000", cost_center_code="4510")
    add_posting(upload, JUL, "7.000", cost_center_code="4520")
    commit(upload, minute=1)
    return report


def test_no_department_has_its_screen_rows_and_list(client) -> None:
    """Verify a cost center inside IDS with no department code shows as No
    department, opens its own screen, its rows with dept=none, and its own
    list on the exceptions screen (ENG-03 2.3 and 8, G20).
    """
    report = no_department_report()
    page = text(get(client, "department", report, "none"))
    assert "<h1>No department</h1>" in page and "7.000" in page
    url = reverse("rows", args=[report.pk]) + "?dept=none"
    assert rows_said(client, url) == (1, "7.000")
    assert '<td class="text">No department</td>' in text(client.get(url))
    page = text(get(client, "exceptions", report))
    table = re.search(r"Cost centers with no department</h2>(.*?)</table>", page, re.S)
    assert "4520" in table[1] and "7.000" in table[1]


def test_arabic_item_text_is_shown_right_to_left_unchanged(
    client, sample_report
) -> None:
    """Verify an Arabic item text is shown exactly as written, with its
    direction set automatically (ENG-03 6.2).
    """
    page = text(get(client, "rows", sample_report, query="?gl=620200"))
    assert '<td class="item" dir="auto">صيانة أجهزة الشبكة</td>' in page


def test_the_filter_form_keeps_the_source_rows_keys(client, sample_report) -> None:
    """Verify changing a filter on source rows keeps the keys that chose them,
    since the form sends them again.
    """
    page = text(get(client, "rows", sample_report, query="?gl=610100&month=2025-07"))
    assert '<input type="hidden" name="gl" value="610100">' in page
    assert '<input type="hidden" name="month" value="2025-07">' in page


# Filters (ENG-03 3)


def test_the_year_filter_shows_only_that_year_and_says_so(
    client, sample_report, sample_expected
) -> None:
    """Verify year=2025 shows that year's total, the filter line says so, and
    the year list offers the years with actual spend.
    """
    page = text(get(client, "overview", sample_report, query="?year=2025"))
    assert amount(sample_expected["screens"]["by_year"]["2025"]) in page
    assert "Year: 2025. Outside IDS: included." in page
    assert '<option value="2025" selected>2025</option>' in page
    assert '<option value="2026">2026</option>' in page


def test_excluding_outside_ids_drops_its_row_and_its_amount(
    client, sample_report, sample_expected
) -> None:
    """Verify exclude_outside=1 drops Outside IDS from the overview and
    departments, and every link keeps the filter.
    """
    excluding = sample_expected["screens"]["excluding_outside"]
    for name in ("overview", "departments"):
        page = text(get(client, name, sample_report, query="?exclude_outside=1"))
        assert "Year: all. Outside IDS: excluded." in page
        assert ">Outside IDS<" not in page
        assert amount(excluding["total"]) in page
        links = ROWS_LINK.findall(page)
        assert links and all("exclude_outside=1" in url for url, _ in links)


def test_a_year_with_no_postings_says_nothing_matched(client, sample_report) -> None:
    """Verify a year with no postings is valid, shows "No actual spend for
    these filters.", and is offered in the year list as chosen (ENG-03 3.1).
    """
    page = text(get(client, "overview", sample_report, query="?year=2024"))
    assert "No actual spend for these filters." in page
    assert "<table" not in page
    assert '<option value="2024" selected>2024</option>' in page


def test_a_code_with_no_actual_spend_opens_its_screen_saying_so(
    client, sample_report
) -> None:
    """Verify a GL account in the right form with no spend opens its screen
    with "No actual spend for these filters.", not 404 (ENG-03 6.1).
    """
    page = text(get(client, "gl_account", sample_report, "999999"))
    assert "No actual spend for these filters." in page


@pytest.mark.parametrize(
    ("name", "args", "query"),
    [
        ("overview", [], "?year=abc"),
        ("overview", [], "?year=26"),
        ("overview", [], "?exclude_outside=2"),
        ("overview", [], "?year=2025&year=2026"),
        ("overview", [], "?gl=610100"),
        ("overview", [], "?page=1"),
        ("gl_accounts", [], "?colour=red"),
        ("gl_account", ["0610100"], ""),
        ("gl_account", ["none"], ""),
        ("gl_account", ["abc"], ""),
        ("department", ["app"], ""),
        ("budget_line", ["outside"], ""),
        ("rows", [], "?gl=0610100"),
        ("rows", [], "?month=2026-3"),
        ("rows", [], "?month=2026-13"),
        ("rows", [], "?page=0"),
        ("rows", [], "?upload=x"),
        ("rows", [], "?dept=Outside"),
        ("rows", [], "?month=2026-03&through=2026-03"),
        ("rows", [], "?cc=4510&cc=4520"),
        ("exceptions", [], "?dept=outside"),
    ],
)
def test_an_address_in_the_wrong_form_is_refused_with_400(
    client, sample_report, name, args, query
) -> None:
    """Verify a wrong value, a key given twice, a key the screen doesn't take,
    or month with through returns 400 (ENG-03 3.1 and 6.1).
    """
    assert get(client, name, sample_report, *args, query=query).status_code == 400


# The panel (ENG-03 4, ENG-01 5.2)


def test_an_htmx_request_gets_the_panel_with_the_new_filters(
    client, sample_report
) -> None:
    """Verify a filter change from HTMX gets the panel alone, holding the
    filter line and links with the new filters (N5).
    """
    panel = text(
        get(
            client,
            "overview",
            sample_report,
            query="?year=2026",
            headers={"HX-Request": "true"},
        )
    )
    assert "<html" not in panel
    assert '<option value="2026" selected>2026</option>' in panel
    assert "Year: 2026. Outside IDS: included." in panel
    assert all("year=2026" in url for url, _ in ROWS_LINK.findall(panel))


def test_htmx_s_request_after_going_back_gets_the_whole_page(
    client, sample_report
) -> None:
    """Verify HTMX's history-restore request gets the whole page, so going
    back never lands a panel in place of the page.
    """
    page = text(
        get(
            client,
            "overview",
            sample_report,
            headers={"HX-Request": "true", "HX-History-Restore-Request": "true"},
        )
    )
    assert "<html" in page


def test_the_filter_form_swaps_the_panel_and_works_without_javascript(
    client, sample_report
) -> None:
    """Verify the filter form sends HTMX to the panel and updates the address,
    and is a plain GET form with an unnamed button, so it works without
    JavaScript and sends no key the screen refuses.
    """
    page = text(get(client, "gl_accounts", sample_report))
    url = reverse("gl_accounts", args=[sample_report.pk])
    assert f'method="get" action="{url}"' in page
    assert f'hx-get="{url}" hx-target="#panel" hx-push-url="true"' in page
    assert '<button type="submit" class="quiet">Show</button>' in page
    assert '<div id="panel">' in page


# The as-of line and an empty report (ENG-03 3.2)


def test_the_as_of_line_names_the_upload_before_a_rolled_back_one(client) -> None:
    """Verify after a rollback the as-of line names the upload before it, and
    an upload awaiting approval is named as not included.
    """
    report = make_report()
    first, second = make_upload(report), make_upload(report)
    add_posting(first, JUL, "1.000")
    add_posting(second, JUL, "2.000")
    commit(first, minute=1)
    commit(second, minute=2)
    assert "Figures as of upload 2, file2.xlsx" in text(get(client, "overview", report))
    mark_rolled_back(second)
    make_upload(report, status=Upload.Status.AWAITING_APPROVAL)
    page = text(get(client, "overview", report))
    assert "Figures as of upload 1, file1.xlsx, approved by Test User" in page
    assert "Upload 3 is awaiting approval and isn't included." in page


@pytest.mark.parametrize(
    "name", ["overview", "gl_accounts", "departments", "budget_lines", "exceptions"]
)
def test_a_report_with_no_approved_upload_shows_no_figures(client, name) -> None:
    """Verify a report with nothing committed says "No approved uploads yet"
    in place of figures, with a link to Uploads for a user with upload.
    """
    report = make_report()
    page = text(get(client, name, report))
    assert "No approved uploads yet." in page
    assert "<table" not in page
    assert f'<a href="{reverse("uploads", args=[report.pk])}">Go to Uploads</a>' in page


def test_no_uploads_link_for_a_user_without_upload(monkeypatch) -> None:
    """Verify a user who may view but not upload sees no link to Uploads in
    the empty report's message.
    """
    report = make_report()
    viewer = make_user(fixed_admin=False)
    monkeypatch.setattr(
        access,
        "held",
        lambda who, _: frozenset({access.VIEW}) if who == viewer else frozenset(),
    )
    browser = Client()
    browser.force_login(viewer)
    page = text(get(browser, "overview", report))
    assert "No approved uploads yet." in page
    assert "Go to Uploads" not in page


# The exceptions screen (ENG-03 8)


def test_the_exceptions_screen_lists_the_sample_s_exceptions(
    client, sample_report, sample_expected
) -> None:
    """Verify the exceptions screen lists 640200, 650200, 3100 and 3200 with
    their amounts, says None for the empty lists, and says which filters
    don't apply.
    """
    page = text(get(client, "exceptions", sample_report))
    exceptions = sample_expected["exceptions"]
    for found in (
        exceptions["missing_from_gl_mapping"]["640200"],
        exceptions["listed_without_ci"]["650200"],
        exceptions["outside_ids"]["3100"],
        exceptions["outside_ids"]["3200"],
    ):
        assert amount(found["amount"]) in page
    assert "Missing from the GL mapping" in page
    assert "Listed without a commitment item" in page
    assert page.count("<p>None</p>") == 4
    assert "Outside IDS: included, since this filter doesn" in page


def test_exceptions_links_add_up_even_with_outside_ids_excluded(
    client, sample_report, sample_expected
) -> None:
    """Verify with exclude_outside=1, the 3100 amount on the exceptions screen
    opens source rows that add up to it, since its links never exclude
    outside IDS (ENG-03 8).
    """
    page = text(get(client, "exceptions", sample_report, query="?exclude_outside=1"))
    links = dict((written, url) for url, written in ROWS_LINK.findall(page))
    written = amount(sample_expected["exceptions"]["outside_ids"]["3100"]["amount"])
    assert "exclude_outside" not in links[written]
    assert rows_said(client, links[written]) == (2, written)


def test_a_budget_line_change_is_listed_with_both_uploads(client) -> None:
    """Verify a second file moving a GL account to another budget line shows
    on the exceptions screen with both uploads and the spend still counting.
    """
    report = make_report()
    for minute, ci in enumerate(["2101", "2201"], start=1):
        upload = make_upload(report)
        map_gl(upload, "610100", ci)
        add_posting(upload, JUL, f"{minute}.000")
        commit(upload, minute=minute, mode=Report.DuplicateMode.LINE_KEY)
    # A third file with no GL mapping isn't in the change, nor its spend.
    third = make_upload(report)
    add_posting(third, JUL, "4.000")
    commit(third, minute=3)
    page = text(get(client, "exceptions", report))
    table = re.search(
        r"Budget-line changes between files</h2>(.*?)</table>", page, re.S
    )
    assert "CI 2101" in table[1] and "CI 2201" in table[1]
    links = ROWS_LINK.findall(table[1])
    assert [written for _, written in links] == ["1.000", "2.000"]
    for url, written in links:
        assert rows_said(client, url) == (1, written)
    footer = re.search(r"<tfoot>(.*?)</tfoot>", table[1], re.S)[1]
    assert '<td class="amount">3.000</td><td class="count">2</td>' in footer


def test_skipped_rows_are_listed_by_upload_and_reason_as_read(client) -> None:
    """Verify the exceptions screen lists each committed upload's skipped rows
    by reason, each amount as read, with their total, and none of a
    rolled-back upload (ENG-03 8).
    """
    report = make_report()
    kept, gone = make_upload(report), make_upload(report)
    add_posting(kept, JUL, "1.000")
    commit(kept, minute=1)
    mark_rolled_back(commit(gone, minute=2))
    for upload, reason, written in [
        (kept, "Invalid date", "12.3456"),
        (kept, "Invalid date", "1.000"),
        (gone, "Invalid date", "99.000"),
    ]:
        SkippedRow.objects.create(
            upload=upload,
            row_number=5,
            reason=reason,
            amount=Decimal(written),
            raw_values=[],
        )
    page = text(get(client, "exceptions", report))
    table = re.search(r"Skipped rows</h2>(.*?)</table>", page, re.S)[1]
    preview = reverse("upload", args=[report.pk, 1])
    assert f'<td class="count"><a href="{preview}">1</a></td>' in table
    assert '<td class="count">2</td><td class="exact">13.3456</td>' in table
    assert "99.000" not in table
    footer = re.search(r"<tfoot>(.*?)</tfoot>", table, re.S)[1]
    assert footer.endswith(
        '<td class="count">2</td><td class="exact">13.3456</td></tr>'
    )


# One look for every screen (ENG-03 4)


def test_no_template_holds_a_style_or_loads_a_file_from_outside() -> None:
    """Verify no template has a style attribute or a <style> block, and none
    loads a script, stylesheet or image from outside the application, so
    OQGN's theme changes only the base template and stylesheet (ENG-03 4).
    """
    for path in TEMPLATES.rglob("*.html"):
        source = path.read_text(encoding="utf-8")
        assert not re.search(r"<style|\sstyle=", source, re.I), path
        assert not re.search(r"(src|href)=[\"']?(https?:)?//", source, re.I), path
