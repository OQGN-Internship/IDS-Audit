"""The access matrix (ENG-01 6.2, ENG-03 4 and 11): every address under a
report, export addresses and actions included, called by the five users ENG-01
names and a sixth whose role isn't preset. Each must get 404, 403, or the page
or the action's result. Every preset role holds export, so only the sixth can
show an export refused.

What each address needs is written here from ENG-03 4's tables, apart from
the views, so a view that checks the wrong permission disagrees with it.
Each request runs inside a transaction that's rolled back, so an action that
succeeds leaves the next request the same report.
"""

from dataclasses import dataclass

import pytest
from django.db import transaction
from django.test import Client
from django.urls import reverse

from ids_audit.imports.commit import commit_upload
from ids_audit.imports.models import Upload
from ids_audit.imports.services import create_report, receive_file
from ids_audit.imports.stage import stage_upload
from ids_audit.reports.models import Report
from tests.factories import grant, make_role, make_user
from tests.files import LINE, csv_file, line, total_row

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("uploads_dir")]


@dataclass(frozen=True)
class Needs:
    """What an address needs, as require() takes it: every permission in
    every, one of any_of, and with uploader, to be the upload's uploader
    (ENG-01 6.2, rule 6).
    """

    every: tuple[str, ...]
    any_of: tuple[str, ...] = ()
    uploader: bool = False


VIEW = Needs(("view",))
EXPORT = Needs(("view", "export"))
UPLOAD = Needs(("upload",))
APPROVE = Needs(("approve",))
ROLL_BACK = Needs(("roll_back",))
AUDIT_LOG = Needs(("view_audit_log",))
# An upload never committed, and its exception items (ENG-03 4).
OPEN_PREVIEW = Needs(("view",), any_of=("upload", "approve"))
AS_UPLOADER = Needs(("upload",), uploader=True)

# The screens with an export, and their address arguments after the report.
SCREENS = [
    ("overview", []),
    ("gl_accounts", []),
    ("gl_account", ["610100"]),
    ("departments", []),
    ("department", ["outside"]),
    ("budget_lines", []),
    ("budget_line", ["none"]),
    ("rows", []),
    ("exceptions", []),
    ("faq_page", ["total-spend"]),
]

# What each user holds on the report: the plan's presets, and a role of view
# and roll_back (ENG-01 6.2).
HELD = {
    "no access": frozenset(),
    "Viewer": frozenset({"view", "export"}),
    "Uploader": frozenset({"view", "export", "upload"}),
    "Approver": frozenset({"view", "export", "approve", "roll_back", "view_audit_log"}),
    "fixed administrator": frozenset(
        {"view", "export", "upload", "approve", "roll_back", "view_audit_log"}
    ),
    "view and roll back": frozenset({"view", "roll_back"}),
}


def addresses(report: Report, kind: str) -> list[tuple[str, str, Needs]]:
    """Every address under the report, with its method and what it needs.
    Upload 1 is committed, and upload 2 awaits approval with an exception of
    this kind in its preview.
    """
    base, done, waiting = [report.pk], [report.pk, 1], [report.pk, 2]
    found = [("get", reverse("report", args=base), VIEW)]
    for name, args in SCREENS:
        found.append(("get", reverse(name, args=[*base, *args]), VIEW))
        found.append(("get", reverse(f"{name}_export", args=[*base, *args]), EXPORT))
    found += [
        ("get", reverse("faq", args=base), VIEW),
        ("get", reverse("uploads", args=base), VIEW),
        ("get", reverse("new_upload", args=base), UPLOAD),
        ("post", reverse("new_upload", args=base), UPLOAD),
        ("get", reverse("new_mapping", args=base), UPLOAD),
        ("post", reverse("new_mapping", args=base), UPLOAD),
        ("get", reverse("upload", args=done), VIEW),
        ("get", reverse("upload", args=waiting), OPEN_PREVIEW),
        ("get", reverse("exception_items", args=[*waiting, kind]), OPEN_PREVIEW),
        ("post", reverse("approve", args=waiting), APPROVE),
        ("post", reverse("reject", args=waiting), APPROVE),
        ("post", reverse("withdraw", args=waiting), AS_UPLOADER),
        ("get", reverse("columns", args=waiting), AS_UPLOADER),
        ("post", reverse("columns", args=waiting), AS_UPLOADER),
        ("get", reverse("roll_back", args=done), ROLL_BACK),
        ("post", reverse("roll_back", args=done), ROLL_BACK),
        ("get", reverse("audit", args=base), AUDIT_LOG),
    ]
    return found


def expected(who: str, needs: Needs) -> str:
    """Return what the user must get: 404 with no access, 403 without the
    permission, and otherwise "done", the page (200) or a redirect (302).
    """
    held = HELD[who]
    if not held:
        return "404"
    if not held.issuperset(needs.every):
        return "403"
    if needs.any_of and not held.intersection(needs.any_of):
        return "403"
    if needs.uploader and who not in ("Uploader", "fixed administrator"):
        return "403"
    return "done"


def outcome(client: Client, method: str, url: str) -> str:
    """Call the address in a transaction that's then rolled back, and return
    404, 403 or "done".
    """
    with transaction.atomic():
        status = getattr(client, method)(url).status_code
        transaction.set_rollback(True)
    return "done" if status in (200, 302) else str(status)


def build(name: str) -> tuple[Report, dict, str]:
    """A report with upload 1 committed and upload 2 awaiting approval, both
    from its Uploader, and a user with each role in HELD on it. Return the
    report, the users by who they are, and an exception type in upload 2's
    preview.
    """
    admin = make_user()
    report = create_report(name, admin)
    users = {"fixed administrator": admin, "no access": make_user(fixed_admin=False)}
    for role in ("Viewer", "Uploader", "Approver"):
        users[role] = make_user(fixed_admin=False)
        grant(users[role], report, role)
    users["view and roll back"] = make_user(fixed_admin=False)
    grant(users["view and roll back"], report, make_role("view", "roll_back"))
    uploader = users["Uploader"]
    first = receive_file(
        report, uploader, "first.csv", csv_file(LINE, total_row("1000.000"))
    )
    commit_upload(stage_upload(first, uploader), admin)
    second = receive_file(
        report,
        uploader,
        "second.csv",
        csv_file(line(Amount_in_LC="2,000.000"), total_row("2000.000")),
    )
    second = stage_upload(second, uploader)
    assert second.status == Upload.Status.AWAITING_APPROVAL
    return report, users, second.preview["exceptions"][0]["type"]


@pytest.mark.parametrize("who", list(HELD))
def test_each_user_gets_404_403_or_the_page_on_every_address(who) -> None:
    """Verify every address under a report answers each user as its
    permission says (ENG-01 6.2, ENG-03 4).
    """
    report, users, kind = build("Matrix")
    client = Client()
    client.force_login(users[who])
    wrong = []
    for method, url, needs in addresses(report, kind):
        got, want = outcome(client, method, url), expected(who, needs)
        if got != want:
            wrong.append(f"{method.upper()} {url}: {got}, not {want}")
    assert wrong == []


def test_access_to_one_report_gives_none_to_another() -> None:
    """Verify a user with Approver on report A gets 404 from every address of
    report B, and still opens report A.
    """
    report_a, users, _ = build("Report A")
    report_b, _, kind = build("Report B")
    client = Client()
    client.force_login(users["Approver"])
    for method, url, _needs in addresses(report_b, kind):
        assert outcome(client, method, url) == "404", url
    assert outcome(client, "get", reverse("overview", args=[report_a.pk])) == "done"
