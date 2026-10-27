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
    """Return the log's rows on the page, each as its cells' text; none when
    the page has no table, as when nothing matches."""
    response = client.get(url)
    assert response.status_code == 200
    page = response.content.decode()
    if "<tbody>" not in page:
        return []
    body = page.split("<tbody>")[1].split("</tbody>")[0]
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
    assert '<a class="page-link" href="?page=2">Older events</a>' in (
        client.get(url).content.decode()
    )
    assert client.get(url + "?page=3").status_code == 404
    for query in ("?page=0", "?page=x", "?year=2026", "?page=1&page=2"):
        assert client.get(url + query).status_code == 400, query


def test_the_log_s_only_forms_are_its_search_and_signing_out() -> None:
    """Verify the log offers no form but its search, sent with GET, and
    signing out, and a report's navigation links it for an Approver and not
    for a Viewer.
    """
    report = make_report()
    approver, viewer = make_user(fixed_admin=False), make_user(fixed_admin=False)
    grant(approver, report, "Approver")
    grant(viewer, report, "Viewer")
    log = reverse("audit", args=[report.pk])
    page = signed_in(approver).get(log).content.decode()
    assert page.count("<form") == 2 and reverse("sign_out") in page
    assert f'<form method="get" action="{log}" class="filters audit-search">' in page
    overview = reverse("overview", args=[report.pk])
    assert f'href="{log}"' in signed_in(approver).get(overview).content.decode()
    assert f'href="{log}"' not in signed_in(viewer).get(overview).content.decode()
    assert signed_in(viewer).get(log).status_code == 403


def searched(client: Client, url: str, query: str) -> list[str]:
    """Return the details of each event the search lists, newest first."""
    return [text(row)[-1] for row in listed(client, url + query)]


def test_search_finds_words_in_details_user_report_and_action() -> None:
    """Verify Search finds an event by words in its details, its user's
    username or full name, its report's name or its action in words,
    ignoring case, with % and _ read as themselves (ENG-03 4).
    """
    admin = make_user()
    mona = make_user(fixed_admin=False)
    mona.full_name = "Mona Al-Rashdi"
    mona.save()
    report = make_report()
    report.name = "Fuel spend"
    report.save()
    audit.record(Action.SIGN_IN, user=mona, details={"n": "1"})
    audit.record(
        Action.USER_CREATED, user=admin, details={"n": "2", "file": "a_b%c.xlsx"}
    )
    audit.record(Action.REPORT_CREATED, user=admin, report=report, details={"n": "3"})
    audit.record(
        Action.EXPORT_DOWNLOADED, user=admin, details={"n": "4", "file": "xyz"}
    )
    client, url = signed_in(admin), reverse("admin_audit")
    assert searched(client, url, "?q=A_B%25C") == ["file: a_b%c.xlsx n: 2"]
    assert searched(client, url, "?q=x_z") == []
    assert searched(client, url, "?q=a%25c") == []
    assert searched(client, url, f"?q={mona.username.upper()}") == ["n: 1"]
    assert searched(client, url, "?q=al-rashdi") == ["n: 1"]
    assert searched(client, url, "?q=fuel") == ["n: 3"]
    assert searched(client, url, "?q=Export+downloaded") == ["file: xyz n: 4"]
    page = client.get(url + "?q=nothing+like+it").content.decode()
    assert '<p class="none">No events match.</p>' in page
    assert '<p class="found">0 events match, newest first.' in page


@pytest.mark.parametrize(
    ("query", "kept"),
    [
        ("", ["", "n: 4", "n: 3", "n: 2", "n: 1"]),
        ("?q=&action=&user=&from=&to=&report=", ["", "n: 4", "n: 3", "n: 2", "n: 1"]),
        ("?action=upload_received", ["n: 3", "n: 1"]),
        ("?user=WHO", ["n: 4", "n: 3"]),
        ("?from=2026-10-26", ["", "n: 4", "n: 3"]),
        ("?to=2026-10-25", ["n: 2", "n: 1"]),
        ("?from=2026-10-26&to=2026-10-26", ["n: 3"]),
        ("?report=REPORT", ["n: 3", "n: 1"]),
        ("?report=none", ["", "n: 4", "n: 2"]),
        ("?action=upload_received&user=WHO", ["n: 3"]),
        ("?user=nobody", []),
    ],
)
def test_each_filter_narrows_the_whole_log(query: str, kept: list[str]) -> None:
    """Verify Action, Who, From and To, by Muscat day, and Report or No report
    each narrow the whole log, together too; an empty field is no filter,
    and a username no account has finds nothing (ENG-03 4). The
    administrator's own sign-in, on 28 October with no details, is one of
    the events too.
    """
    admin, who = make_user(), make_user(fixed_admin=False)
    report = make_report()
    # 25 October at 23:30 in Muscat, then 26 and 27 October at 00:30.
    for n, at, user, action, named in (
        (1, AT.replace(hour=19, minute=30), None, Action.UPLOAD_RECEIVED, report),
        (2, AT.replace(hour=19, minute=30), None, Action.SIGN_IN_FAILED, None),
        (3, AT.replace(hour=20, minute=30), who, Action.UPLOAD_RECEIVED, report),
        (4, AT.replace(day=26, hour=20, minute=30), who, Action.SIGN_IN, None),
    ):
        with time_machine.travel(at, tick=False):
            audit.record(action, user=user, report=named, details={"n": str(n)})
    with time_machine.travel(AT.replace(day=28), tick=False):
        client = signed_in(admin)
    query = query.replace("WHO", who.username).replace("REPORT", str(report.pk))
    assert searched(client, reverse("admin_audit"), query) == kept


def test_a_report_s_log_is_searched_within_its_own_events() -> None:
    """Verify a report's log, however searched, lists only its own events,
    refuses report, and its page links keep the search (ENG-03 4).
    """
    approver = make_user(fixed_admin=False)
    report, other = make_report(), make_report()
    grant(approver, report, "Approver")
    audit.record(Action.REPORT_CREATED, report=report, details={"name": "shared"})
    audit.record(Action.REPORT_CREATED, report=other, details={"name": "shared"})
    audit.record(Action.SIGN_IN, user=approver, details={"name": "shared"})
    client, url = signed_in(approver), reverse("audit", args=[report.pk])
    assert searched(client, url, "?q=shared") == ["name: shared"]
    assert client.get(url + f"?report={other.pk}").status_code == 400
    AuditEvent.objects.bulk_create(
        AuditEvent(action=Action.EXPORT_DOWNLOADED, report=report, details={"n": n})
        for n in range(101)
    )
    page = client.get(url + "?action=export_downloaded").content.decode()
    older = "?action=export_downloaded&amp;page=2"
    assert f'<a class="page-link" href="{older}">Older events</a>' in page
    assert searched(client, url, "?action=export_downloaded&page=2") == ["n: 0"]


@pytest.mark.parametrize(
    "query",
    [
        "?action=upload_lost",
        "?from=2026-13-01",
        "?from=26-10-27",
        "?to=27.10.2026",
        "?report=x",
        "?report=0",
        "?q=" + "a" * 101,
        "?q=a&q=b",
        "?who=user1",
    ],
)
def test_the_whole_log_refuses_a_wrong_search(query: str) -> None:
    """Verify an unknown action, a date not written as 2026-10-27, a report
    that isn't an id or none, a search over 100 characters, a key given
    twice and an unknown key each return 400 (ENG-03 4).
    """
    response = signed_in(make_user()).get(reverse("admin_audit") + query)
    assert response.status_code == 400, query
