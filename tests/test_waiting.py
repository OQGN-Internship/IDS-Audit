"""What's waiting, and for whom (ENG-03 4): the report list's cards with each
report's actual spend, its as-of upload and its waiting upload, Uploads
saying what waits for the signed-in user, and each upload's state as a
badge.
"""

import re

import pytest
from django.test import Client
from django.urls import reverse

from ids_audit.imports.models import Upload
from ids_audit.reports.templatetags.figures import amount
from tests.factories import (
    add_posting,
    commit,
    grant,
    make_report,
    make_upload,
    make_user,
)
from tests.test_queries import JUL
from tests.test_report_screens import ROWS_SAID

pytestmark = pytest.mark.django_db

Status = Upload.Status
CARD = re.compile(r'<section class="card report-card">(.*?)</section>', re.S)


def signed_in(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


def cards(client: Client) -> list[str]:
    response = client.get(reverse("reports"))
    assert response.status_code == 200
    return CARD.findall(response.content.decode())


def test_a_card_shows_the_report_s_actual_spend_and_its_as_of_upload(
    sample_report, sample_expected
) -> None:
    """Verify a report's card links its name to the report, shows its actual
    spend, all years with outside IDS included, linking to source rows that
    add up to it, and the upload its figures are as of.
    """
    client = signed_in(make_user())
    [card] = cards(client)
    report = reverse("report", args=[sample_report.pk])
    assert (
        f'<h2 class="card-title"><a href="{report}">{sample_report.name}</a></h2>'
        in card
    )
    total = amount(sample_expected["actual_spend"]["total"])
    link = re.search(rf'<p class="figure"><a href="([^"]+)">{total}</a>', card)[1]
    assert link == reverse("rows", args=[sample_report.pk])
    page = client.get(link).content.decode()
    assert ROWS_SAID.search(page).groups() == ("35", total)
    assert re.search(r"35 lines\. As of upload 1, approved on \d\d\.\d\d\.\d{4}", card)


def test_a_card_without_an_approved_upload_says_so() -> None:
    """Verify a report with nothing committed shows no amount on its card."""
    make_report()
    [card] = cards(signed_in(make_user()))
    assert "No approved uploads yet." in card
    assert 'class="figure"' not in card


@pytest.mark.parametrize(
    "role, linked",
    [("Viewer", False), ("Uploader", True), ("Approver", True)],
)
@pytest.mark.parametrize(
    "status, words",
    [
        (Status.AWAITING_APPROVAL, "is awaiting approval"),
        (Status.NEEDS_COLUMNS, "needs its columns picked"),
    ],
)
def test_a_card_names_the_waiting_upload_linked_for_who_may_open_it(
    role, linked, status, words
) -> None:
    """Verify a report's card names its waiting upload, linked only for a
    user with upload or approve, who may open it.
    """
    report = make_report()
    first = make_upload(report)
    add_posting(first, JUL, "1.000")
    commit(first, minute=1)
    make_upload(report, status=status)
    user = make_user(fixed_admin=False)
    grant(user, report, role)
    [card] = cards(signed_in(user))
    notice = re.search(r'<p class="alert notice">(.*?)</p>', card)[1]
    assert f"Upload 2 {words}." in notice
    link = f'<a href="{reverse("upload", args=[report.pk, 2])}">Open upload 2</a>'
    assert (link in notice) is linked


@pytest.mark.parametrize("status", [Status.COMMITTED, Status.REJECTED, Status.READING])
def test_a_card_names_no_upload_that_waits_for_no_one(status) -> None:
    """Verify a decided or reading upload isn't named as waiting."""
    make_upload(make_report(), status=status)
    [card] = cards(signed_in(make_user()))
    assert "notice" not in card


def for_you(client: Client, report) -> dict[int, str]:
    """Return what Uploads says waits for the user, by upload number."""
    page = client.get(reverse("uploads", args=[report.pk])).content.decode()
    body = re.search(r"<tbody>(.*?)</tbody>", page, re.S)[1]
    said = {}
    for row in re.findall(r"<tr>(.*?)</tr>", body, re.S):
        cells = re.findall(r"<td>(.*?)</td>", row, re.S)
        number = int(re.search(r">Upload (\d+)</a>", cells[0])[1])
        found = re.search(r'<span class="for-you">([^<]+)</span>', cells[2])
        said[number] = found[1] if found else ""
    return said


@pytest.mark.parametrize(
    "role, said",
    [
        ("Viewer", ""),
        ("Uploader", ""),
        ("Approver", "Waiting for your approval"),
    ],
)
def test_uploads_says_an_upload_waits_for_your_approval(role, said) -> None:
    """Verify an upload awaiting approval says it waits for the user only to
    a user who may approve it.
    """
    report = make_report()
    make_upload(report, status=Status.AWAITING_APPROVAL)
    user = make_user(fixed_admin=False)
    grant(user, report, role)
    assert for_you(signed_in(user), report) == {1: said}


def test_uploads_says_an_upload_waits_for_its_uploader_to_pick_columns() -> None:
    """Verify an upload that needs its columns picked says it waits for the
    user only to its uploader, who may pick them, not to another uploader
    or an approver (ENG-01 6.2, rule 6).
    """
    report = make_report()
    upload = make_upload(report, status=Status.NEEDS_COLUMNS)
    uploader, other, approver = (make_user(fixed_admin=False) for _ in range(3))
    grant(uploader, report, "Uploader")
    grant(other, report, "Uploader")
    grant(approver, report, "Approver")
    upload.uploaded_by = uploader
    upload.save()
    assert for_you(signed_in(uploader), report) == {
        1: "Waiting for you to pick columns"
    }
    assert for_you(signed_in(other), report) == {1: ""}
    assert for_you(signed_in(approver), report) == {1: ""}


@pytest.mark.parametrize(
    "status, tone",
    [
        (Status.READING, "open"),
        (Status.NEEDS_COLUMNS, "open"),
        (Status.AWAITING_APPROVAL, "open"),
        (Status.COMMITTED, "good"),
        (Status.FAILED, "bad"),
        (Status.REJECTED, ""),
        (Status.ROLLED_BACK, ""),
    ],
)
def test_an_upload_s_state_is_a_badge_with_its_bar(status, tone) -> None:
    """Verify Uploads and the upload's page show its state as a badge in
    words, with the bar its state has (ENG-03 4).
    """
    report = make_report()
    upload = make_upload(report, status=status)
    client = signed_in(make_user())
    badge = f'<span class="badge {tone}">{upload.get_status_display()}</span>'
    for name, args in (("uploads", []), ("upload", [1])):
        page = client.get(reverse(name, args=[report.pk, *args])).content.decode()
        assert badge in page, name
