"""The sidebar and the page header (ENG-03 4): the crumbs above each page,
the report's screens in their groups, Administration for fixed
administrators, the signed-in user's links, and Uploads saying Waiting while
the report has an upload waiting for someone.
"""

import re

import pytest
from django.test import Client
from django.urls import reverse

from ids_audit.imports.models import Upload
from ids_audit.reports.models import Report
from tests.factories import grant, make_report, make_upload, make_user

pytestmark = pytest.mark.django_db

Status = Upload.Status
CRUMBS = re.compile(r'<nav class="crumbs" aria-label="Breadcrumb"><ol>(.*?)</ol></nav>')
SIDEBAR = re.compile(r'<nav class="sidebar"[^>]*>(.*?)</nav>', re.S)


def crumbs(client: Client, address: str) -> list[tuple[str, str]] | None:
    """Return a page's crumbs, each as its address and words, or None for a
    page with no crumbs at all.
    """
    response = client.get(address)
    assert response.status_code == 200, (address, response.status_code)
    found = CRUMBS.search(response.content.decode())
    if found is None:
        return None
    return re.findall(r'<li><a href="([^"]+)">([^<]+)</a></li>', found[1])


def sidebar(client: Client, address: str) -> str:
    return SIDEBAR.search(client.get(address).content.decode())[1]


@pytest.fixture
def admin_client() -> Client:
    client = Client()
    client.force_login(make_user())
    return client


def test_crumbs_link_the_pages_above(admin_client) -> None:
    """Verify each kind of page links the pages above it, from Reports or
    Administration down, and a page at the top has no crumbs.
    """
    report = make_report()
    upload = make_upload(report, status=Status.AWAITING_APPROVAL)
    top = (reverse("reports"), "Reports")
    home = (reverse("overview", args=[report.pk]), report.name)
    uploads = (reverse("uploads", args=[report.pk]), "Uploads")
    one = (reverse("upload", args=[report.pk, upload.number]), "Upload 1")
    admin = (reverse("admin_home"), "Administration")
    users = (reverse("admin_users"), "Users")
    roles = (reverse("admin_roles"), "Roles")
    for address, want in (
        (reverse("reports"), None),
        (reverse("new_report"), [top]),
        (reverse("overview", args=[report.pk]), [top, home]),
        (
            reverse("gl_account", args=[report.pk, "610100"]),
            [top, home, (reverse("gl_accounts", args=[report.pk]), "GL accounts")],
        ),
        (
            reverse("department", args=[report.pk, "APP"]),
            [top, home, (reverse("departments", args=[report.pk]), "Departments")],
        ),
        (
            reverse("budget_line", args=[report.pk, "2101"]),
            [top, home, (reverse("budget_lines", args=[report.pk]), "Budget lines")],
        ),
        (
            reverse("faq_page", args=[report.pk, "total-spend"]),
            [top, home, (reverse("faq", args=[report.pk]), "FAQ")],
        ),
        (reverse("upload", args=[report.pk, upload.number]), [top, home, uploads]),
        (reverse("new_upload", args=[report.pk]), [top, home, uploads]),
        (
            reverse("roll_back", args=[report.pk, upload.number]),
            [top, home, uploads, one],
        ),
        (reverse("admin_home"), None),
        (reverse("admin_users"), [admin]),
        (reverse("admin_new_role"), [admin, roles]),
        (reverse("admin_audit"), [admin]),
        (reverse("admin_new_user"), [admin, users]),
    ):
        assert crumbs(admin_client, address) == want, address


def test_every_page_under_a_report_names_it_in_its_crumbs(admin_client) -> None:
    """Verify each of a report's screens names the report in its crumbs,
    linked to its overview, so the page names it on screen and on paper.
    """
    report = make_report()
    home = (reverse("overview", args=[report.pk]), report.name)
    for name in (
        "overview",
        "gl_accounts",
        "departments",
        "budget_lines",
        "rows",
        "exceptions",
        "faq",
        "uploads",
        "audit",
    ):
        assert home in crumbs(admin_client, reverse(name, args=[report.pk])), name


def test_crumbs_leave_out_a_page_without_what_they_need(admin_client) -> None:
    """Verify a refused page, such as a 404 for an upload that doesn't
    exist or an address no page has, shows no crumbs rather than failing.
    """
    report = make_report()
    for address in (reverse("upload", args=[report.pk, 9]), "/no-such-page"):
        response = admin_client.get(address)
        assert response.status_code == 404, address
        assert "crumbs" not in response.content.decode()


def test_the_sidebar_groups_a_report_s_screens_with_no_application_name(
    admin_client,
) -> None:
    """Verify the sidebar names the report under the label Report, lists its
    figures' screens, then Uploads and the Audit log in a group of their
    own, has no "IDS Spend Audit", and links Change password and Sign out.
    """
    report = make_report()
    nav = sidebar(admin_client, reverse("overview", args=[report.pk]))
    assert "IDS Spend Audit" not in nav
    label = r'<p class="group-label">Report</p>\s*<p class="report-name">'
    assert re.search(label + report.name + "</p>", nav)
    groups = [
        re.findall(r"</svg>([^<]+)</a>", group)
        for group in re.findall(r"<ul>(.*?)</ul>", nav, re.S)
    ]
    assert groups == [
        ["Reports"],
        ["Overview", "GL accounts", "Departments", "Budget lines", "Exceptions", "FAQ"],
        ["Uploads", "Audit log"],
        ["Users", "Roles", "Whole audit log"],
    ]
    assert f'href="{reverse("password_change")}"' in nav
    assert "Sign out</button>" in nav


def test_the_sidebar_shows_the_signed_in_user(admin_client) -> None:
    """Verify the sidebar shows the user's full name over their username."""
    user = make_user(fixed_admin=False)
    client = Client()
    client.force_login(user)
    nav = sidebar(client, reverse("reports"))
    assert (
        f'<p class="who-name">{user.full_name} <span>{user.username}</span></p>' in nav
    )
    assert "Administration" not in nav


@pytest.mark.parametrize(
    "status, waits",
    [
        (Status.AWAITING_APPROVAL, True),
        (Status.NEEDS_COLUMNS, True),
        (Status.READING, False),
        (Status.COMMITTED, False),
        (Status.REJECTED, False),
        (Status.FAILED, False),
    ],
)
def test_uploads_says_waiting_while_an_upload_waits_for_someone(status, waits) -> None:
    """Verify a report's Uploads link says Waiting while an upload awaits
    approval or needs its columns picked, and not once it's decided, for a
    Viewer as for anyone.
    """
    report = make_report()
    make_upload(report, status=status)
    viewer = make_user(fixed_admin=False)
    grant(viewer, report, "Viewer")
    client = Client()
    client.force_login(viewer)
    nav = sidebar(client, reverse("overview", args=[report.pk]))
    link = re.search(r"</svg>Uploads(.*?)</a>", nav)[1]
    assert (link == ' <span class="badge open">Waiting</span>') is waits
    assert (link == "") is not waits


def test_uploads_says_waiting_only_on_its_own_report() -> None:
    """Verify another report's waiting upload doesn't mark this one."""
    report, other = make_report(), make_report()
    make_upload(other, status=Status.AWAITING_APPROVAL)
    client = Client()
    client.force_login(make_user())
    assert "Waiting" not in sidebar(client, reverse("overview", args=[report.pk]))
    assert "Waiting" in sidebar(client, reverse("overview", args=[other.pk]))
    assert Report.objects.count() == 2


def test_the_administration_home_links_its_screens_as_cards(admin_client) -> None:
    """Verify the administration home shows Users, Roles and the whole audit
    log as cards, each linking to its screen, as the sidebar's group does.
    """
    page = admin_client.get(reverse("admin_home")).content.decode()
    cards = re.findall(r'<section class="card">\s*<h2><a href="([^"]+)">', page)
    assert cards == [
        reverse("admin_users"),
        reverse("admin_roles"),
        reverse("admin_audit"),
    ]
