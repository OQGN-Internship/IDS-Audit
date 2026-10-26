"""The FAQ pages (ENG-03 7): every answer for the sample file against the
expected result its script wrote, each sentence filled from its own table,
the parts a sentence leaves out, and the pickers.
"""

import html
import re
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse

from ids_audit.reports import faq
from ids_audit.reports.models import Report
from ids_audit.reports.queries import ALL, Filters
from tests.factories import (
    add_posting,
    commit,
    make_report,
    make_upload,
    make_user,
    map_cc,
    map_gl,
)
from tests.test_queries import JUL

pytestmark = pytest.mark.django_db

ANSWER = re.compile(r'<p class="answer">(.*?)</p>', re.S)


@pytest.fixture
def client() -> Client:
    signed_in = Client()
    signed_in.force_login(make_user())
    return signed_in


def page(client: Client, report: Report, slug: str, query: str = "") -> str:
    """Open an FAQ page and return its text."""
    response = client.get(reverse("faq_page", args=[report.pk, slug]) + query)
    assert response.status_code == 200, response.status_code
    return response.content.decode()


def answer(client: Client, report: Report, slug: str, query: str = "") -> str:
    """Return the answer sentence an FAQ page shows, as written."""
    found = ANSWER.search(page(client, report, slug, query))
    return found[1].replace("&#x27;", "'")


def test_the_faq_lists_the_eight_default_questions_in_order(client) -> None:
    """Verify the FAQ index lists the plan's eight questions in its order, each
    linking to its page (ENG-03 7.2).
    """
    report = make_report()
    text = client.get(reverse("faq", args=[report.pk])).content.decode()
    questions = re.search(r'<ol class="questions">(.*?)</ol>', text, re.S)[1]
    links = re.findall(r'<li><a href="([^"]+)">([^<]+)</a></li>', questions)
    assert [html.unescape(q) for _, q in links] == [e.question for e in faq.ENTRIES]
    assert [url for url, _ in links] == [
        reverse("faq_page", args=[report.pk, e.slug]) for e in faq.ENTRIES
    ]
    assert [e.slug for e in faq.ENTRIES] == [
        "total-spend",
        "largest-gl-accounts",
        "gl-budget-lines",
        "gl-account-cost-centers",
        "department-spend",
        "department-gl-accounts",
        "spend-over-time",
        "unmatched-spend",
    ]


@pytest.mark.parametrize("number", range(1, 9))
def test_every_answer_for_the_sample_equals_its_expected_answer(
    client, sample_report, sample_expected, number
) -> None:
    """Verify each FAQ page shows the answer the sample's script wrote, with
    the question, what was counted and the check by hand (ENG-03 7).
    """
    entry = faq.ENTRIES[number - 1]
    text = page(client, sample_report, entry.slug)
    assert (
        answer(client, sample_report, entry.slug) == sample_expected["faq"][str(number)]
    )
    assert f'<h1 class="page-title">{entry.question}</h1>'.replace(
        "'", "&#x27;"
    ) in text.replace("'", "&#x27;")
    assert '<p class="counted">Counted: the actual spend' in text
    assert "<h2>How to check it by hand</h2>" in text
    assert "Figures as of upload 1, sample.xlsx" in re.sub(r"<[^>]+>", "", text)


def test_q1_with_outside_ids_excluded_says_so(
    client, sample_report, sample_expected
) -> None:
    """Verify Q1 with exclude_outside=1 leaves out the outside IDS part and says
    it's excluded (ENG-03 7.2, R15).
    """
    assert (
        answer(client, sample_report, "total-spend", "?exclude_outside=1")
        == sample_expected["faq_excluding_outside"]["1"]
    )


def test_q1_s_figures_equal_its_table_s_rows(sample_report) -> None:
    """Verify Q1's sentence takes the total and lines from its table's total
    row, and IDS and outside IDS from its rows, which add up to the total.
    """
    found = faq.total_spend(sample_report, ALL, None)
    (table,) = found.tables
    rows = list(table.rows)
    assert found.figures == {
        "total": table.total[1].value,
        "lines": table.total[2].value,
        "ids": rows[0][1].value,
        "outside": rows[1][1].value,
    }
    assert found.figures["ids"] + found.figures["outside"] == found.figures["total"]


def test_q2_leaves_out_the_share_when_the_total_shown_isn_t_spend() -> None:
    """Verify Q2 leaves out its share when there isn't one (ENG-03 2.2)."""
    assert faq.largest_gl_accounts_answer(
        {"code": "610100", "name": "Software", "amount": Decimal("5"), "share": None}
    ) == ("The largest is Software (610100), with 5.000 OMR.")


def own_upload_report() -> Report:
    """A report under own_upload with 610100 under two budget lines, and every
    other GL account mapped.
    """
    report = make_report("own_upload")
    for minute, ci in enumerate(["2101", "2201"], start=1):
        upload = make_upload(report)
        map_gl(upload, "610100", ci)
        map_gl(upload, "620100", "2201")
        add_posting(upload, JUL, "10.000")
        add_posting(upload, JUL, "1.000", gl_code="620100")
        commit(upload, minute=minute, mode=Report.DuplicateMode.LINE_KEY)
    return report


def test_q3_names_gl_accounts_under_several_budget_lines(client) -> None:
    """Verify Q3 counts GL accounts and budget lines among its rows, adds the
    accounts with several budget lines, and leaves out "and 0 have no budget
    line" (ENG-03 7.2).
    """
    assert answer(client, own_upload_report(), "gl-budget-lines") == (
        "2 GL accounts map to 2 budget lines. "
        "1 GL account has more than one budget line."
    )


def test_q4_shows_the_gl_account_picked_and_leaves_out_outside_ids(
    client, sample_report
) -> None:
    """Verify Q4's picker chooses the GL account in the address, and the
    sentence leaves out outside IDS when no cost center outside IDS used it.
    """
    assert answer(client, sample_report, "gl-account-cost-centers", "?gl=620200") == (
        "Hardware maintenance (620200) was used by 1 cost center in 1 department."
    )
    text = page(client, sample_report, "gl-account-cost-centers", "?gl=620200")
    assert '<option value="620200" selected>620200 Hardware maintenance</option>' in (
        text
    )
    # The largest comes first, and is the one shown without a pick.
    options = re.findall(r'<option value="(\d{6})"', text)
    assert options[0] == "610100"
    assert "for GL account 620200, by cost center.</p>" in text


def test_q4_counts_a_cost_center_with_no_department_inside_ids(client) -> None:
    """Verify Q4 counts a cost center with no department as one inside IDS but
    in no department, apart from one outside IDS (ENG-03 7.2).
    """
    report = make_report()
    upload = make_upload(report)
    map_cc(upload, "4510", "APP")
    map_cc(upload, "4520", "")
    for cost_center in ("4510", "4520", "3100"):
        add_posting(upload, JUL, "1.000", cost_center_code=cost_center)
    commit(upload, minute=1)
    assert answer(client, report, "gl-account-cost-centers") == (
        "610100 was used by 2 cost centers in 1 department, and by 1 cost center "
        "outside IDS."
    )


def test_q4_with_a_gl_account_with_no_spend_says_nothing_matched(
    client, sample_report
) -> None:
    """Verify a GL account in the right form with no actual spend shows "No
    actual spend for these filters.", and keeps it in the picker.
    """
    text = page(client, sample_report, "gl-account-cost-centers", "?gl=999999")
    assert "No actual spend for these filters." in text
    assert '<option value="999999" selected>999999</option>' in text


def test_q6_picks_any_department_outside_ids_included(client, sample_report) -> None:
    """Verify Q6's picker offers every row of the departments screen, and
    dept=outside answers for Outside IDS (ENG-03 7.2).
    """
    assert answer(client, sample_report, "department-gl-accounts", "?dept=outside") == (
        "Outside IDS spent most on Software licences (610100): 11,090.750 OMR."
    )
    text = page(client, sample_report, "department-gl-accounts")
    picker = re.search(
        r'<select class="form-select" name="dept">(.*?)</select>', text, re.S
    )[1]
    assert '<option value="outside">Outside IDS</option>' in picker
    assert '<option value="APP" selected>APP Applications</option>' in picker
    assert re.findall(r'<option value="([A-Za-z]+)"', picker) == [
        "APP",
        "INF",
        "SEC",
        "GOV",
        "outside",
    ]


def test_q8_names_no_department_and_leaves_out_in_both(client) -> None:
    """Verify Q8 adds the spend on IDS cost centers with no department, and
    says the total in all when nothing is in both lists (ENG-03 7.2, G20).
    """
    report = make_report()
    upload = make_upload(report)
    map_gl(upload, "610100", "2101")
    map_cc(upload, "4510", "APP")
    map_cc(upload, "4520", "")
    add_posting(upload, JUL, "5.000", gl_code="640200", cost_center_code="4510")
    add_posting(upload, JUL, "7.000", cost_center_code="4520")
    add_posting(upload, JUL, "3.000", cost_center_code="3100")
    commit(upload, minute=1)
    assert answer(client, report, "unmatched-spend") == (
        "5.000 OMR has no budget line, and 3.000 OMR is outside IDS. "
        "7.000 OMR is on IDS cost centers with no department. "
        "In all, 15.000 OMR couldn't be matched."
    )


def test_q8_ignores_the_outside_ids_filter_and_says_so(
    client, sample_report, sample_expected
) -> None:
    """Verify Q8 gives the same answer with outside IDS excluded, and says the
    filter doesn't apply (ENG-03 8).
    """
    text = page(client, sample_report, "unmatched-spend", "?exclude_outside=1")
    assert ANSWER.search(text)[1].replace("&#x27;", "'") == sample_expected["faq"]["8"]
    assert "Outside IDS: included, since this filter doesn" in text


def test_an_faq_page_with_no_data_or_no_match_shows_no_figures(
    client, sample_report
) -> None:
    """Verify an FAQ page says "No approved uploads yet" with no data, and "No
    actual spend for these filters." when the filters match nothing, never a
    zero that reads like a finding.
    """
    empty = make_report()
    for entry in faq.ENTRIES:
        text = page(client, empty, entry.slug)
        assert "No approved uploads yet." in text
        assert 'class="answer"' not in text
        text = page(client, sample_report, entry.slug, "?year=2024")
        assert "No actual spend for these filters." in text
        assert 'class="answer"' not in text


@pytest.mark.parametrize(
    ("slug", "query", "status"),
    [
        ("", "?year=abc", 400),
        ("", "?year=2025", 200),
        ("", "?gl=610100", 400),
        ("no-such-question", "", 404),
        ("total-spend", "?gl=610100", 400),
        ("gl-account-cost-centers", "?dept=APP", 400),
        ("gl-account-cost-centers", "?gl=0610100", 400),
        ("department-gl-accounts", "?dept=app", 400),
        ("department-gl-accounts", "?dept=none", 200),
    ],
)
def test_an_faq_address_takes_only_its_own_pick(
    client, sample_report, slug, query, status
) -> None:
    """Verify an unknown question is 404, and a page, or the FAQ list, refuses
    a key it doesn't take, a filter or a pick in the wrong form with 400.
    """
    if slug:
        url = reverse("faq_page", args=[sample_report.pk, slug]) + query
    else:
        url = reverse("faq", args=[sample_report.pk]) + query
    assert client.get(url).status_code == status


def test_every_entry_s_answer_is_filled_from_its_table(sample_report) -> None:
    """Verify each entry's sentence is made only from the figures its query
    found, so a figure that changes in the table changes in the sentence.
    """
    for entry in faq.ENTRIES:
        found = entry.query(sample_report, Filters(), None)
        assert found.lines and found.tables
        assert (
            entry.answer(found.figures)
            == faq.page(sample_report, Filters(), entry, None).answer
        )
