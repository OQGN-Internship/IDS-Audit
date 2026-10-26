"""The audit log screens (ENG-03 4, ENG-01 6.3): a report's events for a
user with view_audit_log, and the whole log for fixed administrators.
"""

import datetime
import re

import pytest
import time_machine
from django.test import Client
from django.urls import reverse

from ids_audit import audit
from ids_audit.audit.models import AuditEvent
from tests.factories import grant, make_report, make_upload, make_user

pytestmark = pytest.mark.django_db

Action = AuditEvent.Action
# 14:05:31 in Muscat.
AT = datetime.datetime(2026, 10, 25, 10, 5, 31, tzinfo=datetime.UTC)


def signed_in(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


def listed(client: Client, url: str) -> list[list[str]]:
    """Return the log's rows on the page, each as its cells' text."""
    response = client.get(url)
    assert response.status_code == 200
    body = response.content.decode().split("<tbody>")[1].split("</tbody>")[0]
    return [
        [
            re.sub(r"<[^>]+>", " ", cell).split()
            for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        ]
        for row in re.findall(r"<tr>(.*?)</tr>", body, re.S)
    ]


def text(cells: list[list[str]]) -> list[str]:
    return [" ".join(words) for words in cells]


def test_a_report_s_log_lists_only_its_events_newest_first() -> None:
    """Verify an Approver on report A sees A's events, newest first, and no
    sign-in, account change or event of report B.
    """
    approver = make_user(fixed_admin=False)
    report, other = make_report(), make_report()
    grant(approver, report, "Approver")
    audit.record(Action.REPORT_CREATED, report=report, details={"name": "A"})
    audit.record(Action.REPORT_CREATED, report=other, details={"name": "B"})
    audit.record(Action.SIGN_IN_FAILED, details={"username": "nobody"})
    upload = make_upload(report)
    audit.record(Action.UPLOAD_RECEIVED, user=approver, report=report, upload=upload)
    client = signed_in(approver)
    rows = [text(row) for row in listed(client, reverse("audit", args=[report.pk]))]
    assert [row[2] for row in rows] == ["Upload received", "Report created"]
    assert rows[0][1] == str(approver) and rows[0][3] == str(upload.number)
    assert rows[1][4] == "name: A"


def test_the_log_names_who_acted_and_when_to_the_second() -> None:
    """Verify each event shows its time to the second in Muscat time, and
    who acted: the user, the command line, an unknown username or the
    application.
    """
    report = make_report()
    user = make_user()
    with time_machine.travel(AT, tick=False):
        audit.record(Action.UPLOAD_FAILED, report=report, details={"error": "x"})
        audit.record(Action.SIGN_IN_FAILED, report=report, details={"username": "who"})
        audit.record(
            Action.USER_CREATED, report=report, details={"command": "create_admin"}
        )
        audit.record(Action.EXPORT_DOWNLOADED, user=user, report=report)
    rows = [
        text(row) for row in listed(signed_in(user), reverse("audit", args=[report.pk]))
    ]
    assert [row[:3] for row in rows] == [
        ["25.10.2026 14:05:31", str(user), "Export downloaded"],
        ["25.10.2026 14:05:31", "Command line", "User created"],
        ["25.10.2026 14:05:31", "Unknown username", "Sign in failed"],
        ["25.10.2026 14:05:31", "The application", "Upload failed"],
    ]


def test_details_are_listed_by_name_and_escaped() -> None:
    """Verify an event's details show each name and value by name, a value
    holding more as JSON, and text as plain text, never markup.
    """
    report, user = make_report(), make_user()
    audit.record(
        Action.EXPORT_DOWNLOADED,
        user=user,
        report=report,
        details={
            "view": "<script>alert(1)</script>",
            "filters": {"year": "all", "exclude_outside": "0"},
            "rows": 12,
        },
    )
    response = signed_in(user).get(reverse("audit", args=[report.pk]))
    page = response.content.decode()
    assert "<script>alert(1)</script>" not in page
    assert "view: &lt;script&gt;alert(1)&lt;/script&gt;" in page
    (row,) = listed(signed_in(user), reverse("audit", args=[report.pk]))
    assert text(row)[4] == (
        "filters: {&quot;exclude_outside&quot;: &quot;0&quot;, &quot;year&quot;: "
        "&quot;all&quot;} rows: 12 view: &lt;script&gt;alert(1)&lt;/script&gt;"
    )


def test_the_whole_log_is_only_for_fixed_admins() -> None:
    """Verify /admin/audit lists every event, both reports' and the two
    browsers' sign-ins included, with each event's report, for a fixed
    administrator only.
    """
    admin, approver = make_user(), make_user(fixed_admin=False)
    report, other = make_report(), make_report()
    grant(approver, report, "Approver")
    audit.record(Action.REPORT_CREATED, report=report, details={"name": "A"})
    audit.record(Action.REPORT_CREATED, report=other, details={"name": "B"})
    assert signed_in(approver).get(reverse("admin_audit")).status_code == 403
    rows = [text(row) for row in listed(signed_in(admin), reverse("admin_audit"))]
    assert [row[2:4] for row in rows] == [
        ["Sign in", ""],
        ["Sign in", ""],
        ["Report created", other.name],
        ["Report created", report.name],
    ]


def test_the_log_comes_100_to_a_page_and_refuses_a_wrong_address() -> None:
    """Verify 101 events make two pages, a page past the last is 404, and any
    key but page, or a page that isn't a whole number from 1, is 400.
    """
    report, user = make_report(), make_user()
    AuditEvent.objects.bulk_create(
        AuditEvent(action=Action.EXPORT_DOWNLOADED, report=report, details={"n": n})
        for n in range(101)
    )
    client = signed_in(user)
    url = reverse("audit", args=[report.pk])
    first = listed(client, url)
    assert len(first) == 100 and text(first[0])[4] == "n: 100"
    assert text(listed(client, url + "?page=2")[0])[4] == "n: 0"
    assert "Older events" in client.get(url).content.decode()
    assert client.get(url + "?page=3").status_code == 404
    for query in ("?page=0", "?page=x", "?year=2026", "?page=1&page=2"):
        assert client.get(url + query).status_code == 400, query


def test_the_log_has_no_form_and_the_navigation_links_it_by_permission() -> None:
    """Verify the log offers no form but signing out, and a report's
    navigation links it for an Approver and not for a Viewer.
    """
    report = make_report()
    approver, viewer = make_user(fixed_admin=False), make_user(fixed_admin=False)
    grant(approver, report, "Approver")
    grant(viewer, report, "Viewer")
    log = reverse("audit", args=[report.pk])
    page = signed_in(approver).get(log).content.decode()
    assert page.count("<form") == 1 and reverse("sign_out") in page
    overview = reverse("overview", args=[report.pk])
    assert f'href="{log}"' in signed_in(approver).get(overview).content.decode()
    assert f'href="{log}"' not in signed_in(viewer).get(overview).content.decode()
    assert signed_in(viewer).get(log).status_code == 403
