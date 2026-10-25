"""The access check, require() (ENG-01 6.2).

Until Phase 4 adds roles, only fixed administrators hold permissions. The
tests of what a role allows give a user permissions by replacing held(), the
one answer Phase 4 changes.
"""

import pytest
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.test import RequestFactory

from ids_audit.accounts import access
from ids_audit.accounts.access import (
    PERMISSIONS,
    allowed,
    may_create_reports,
    report_permission,
    require,
    visible_reports,
)
from ids_audit.accounts.models import User
from ids_audit.audit.models import AuditEvent
from ids_audit.imports.commit import commit_upload
from ids_audit.imports.models import Upload
from ids_audit.imports.services import receive_file
from ids_audit.imports.stage import stage_upload
from ids_audit.ledger.models import Posting
from tests.factories import make_report, make_upload, make_user
from tests.files import LINE, csv_file, total_row

pytestmark = pytest.mark.django_db


@pytest.fixture
def holds(monkeypatch):
    """Give one user these permissions on every report, as a role would."""

    def give(user, *permissions):
        monkeypatch.setattr(
            access,
            "held",
            lambda who, report: frozenset(permissions) if who == user else frozenset(),
        )

    return give


def test_a_fixed_admin_passes_every_check() -> None:
    """Verify a fixed administrator holds all six permissions on a report
    they never touched, and passes the uploader check on someone else's
    upload.
    """
    admin, report = make_user(), make_report()
    upload = make_upload(report)
    require(admin, report, *PERMISSIONS)
    require(admin, report, "upload", upload=upload)
    require(admin, report, "view", any_of=("upload", "approve"))
    assert set(PERMISSIONS) == {
        "view",
        "export",
        "upload",
        "approve",
        "roll_back",
        "view_audit_log",
    }


def test_until_phase_4_anyone_else_gets_404_on_every_report() -> None:
    """Verify a user who isn't a fixed administrator holds no permission, so
    every check is 404, and sees no report.
    """
    user, report = make_user(fixed_admin=False), make_report()
    for permission in sorted(PERMISSIONS):
        with pytest.raises(Http404):
            require(user, report, permission)
    with pytest.raises(Http404):
        require(user, report)
    assert list(visible_reports(user)) == []
    assert list(visible_reports(make_user())) == [report]
    assert not may_create_reports(user)
    assert may_create_reports(make_user())


def test_a_disabled_fixed_admin_holds_nothing() -> None:
    """Verify a fixed administrator disabled by disable_admin holds no
    permission, whatever session they still have.
    """
    admin, report = make_user(), make_report()
    User.objects.filter(pk=admin.pk).update(is_active=False)
    admin.refresh_from_db()
    with pytest.raises(Http404):
        require(admin, report, "view")


def test_allowed_says_what_require_would_do(holds) -> None:
    """Verify allowed() is True where require() passes and False where it
    refuses with 403 or 404, so a screen shows only what the user may do.
    """
    user, report = make_user(fixed_admin=False), make_report()
    assert not allowed(user, report, "view")
    holds(user, "view")
    assert allowed(user, report, "view")
    assert not allowed(user, report, "view", "approve")


def test_a_missing_permission_is_403(holds) -> None:
    """Verify a user with some permissions passes for those, and gets 403 for
    any other, alone or with one they hold.
    """
    user, report = make_user(fixed_admin=False), make_report()
    holds(user, "view", "upload")
    require(user, report, "view")
    require(user, report, "view", "upload")
    for missing in (("approve",), ("view", "export")):
        with pytest.raises(PermissionDenied):
            require(user, report, *missing)


def test_any_of_needs_one_of_them(holds) -> None:
    """Verify any_of passes with one of its permissions and refuses with
    none, as the preview of an upload never committed needs (ENG-03 4).
    """
    user, report = make_user(fixed_admin=False), make_report()
    holds(user, "view", "upload")
    require(user, report, "view", any_of=("upload", "approve"))
    with pytest.raises(PermissionDenied):
        require(user, report, "view", any_of=("approve", "roll_back"))


def test_only_the_uploader_passes_with_the_upload(holds) -> None:
    """Verify the check with an upload passes its uploader and refuses
    another user who holds upload too (rule 6).
    """
    report = make_report()
    uploader, other = make_user(fixed_admin=False), make_user(fixed_admin=False)
    upload = make_upload(report)
    upload.uploaded_by = uploader
    holds(uploader, "upload")
    require(uploader, report, "upload", upload=upload)
    holds(other, "upload")
    with pytest.raises(PermissionDenied):
        require(other, report, "upload", upload=upload)


def test_a_name_that_isnt_a_permission_is_refused() -> None:
    """Verify a misspelt permission is refused, even for a fixed
    administrator, so it can't pass as a check.
    """
    with pytest.raises(ValueError, match="aprove"):
        require(make_user(), make_report(), "aprove")
    with pytest.raises(ValueError, match="edit"):
        require(make_user(), make_report(), any_of=("view", "edit"))


def test_the_decorator_loads_the_report_and_checks_it() -> None:
    """Verify the decorator passes the report to the view, refuses a user
    without access with 404, and answers 404 for a report that doesn't exist.
    """

    @report_permission("view")
    def view(request, report):
        return HttpResponse(report.name)

    report = make_report()
    request = RequestFactory().get("/")
    request.user = make_user()
    assert view(request, report_id=report.pk).content == report.name.encode()
    with pytest.raises(Http404):
        view(request, report_id=report.pk + 1000)
    request.user = make_user(fixed_admin=False)
    with pytest.raises(Http404):
        view(request, report_id=report.pk)


def test_commit_checks_approve_inside_its_transaction(uploads_dir, holds) -> None:
    """Verify an approval by a user without approve is refused with 403 from
    commit_upload itself, commits nothing and writes no approval_refused.
    """
    report = make_report()
    upload = receive_file(
        report, report.created_by, "s.csv", csv_file(LINE, total_row("1000.000"))
    )
    upload = stage_upload(upload, upload.uploaded_by)
    user = make_user(fixed_admin=False)
    holds(user, "view", "upload")
    with pytest.raises(PermissionDenied):
        commit_upload(upload, user)
    upload.refresh_from_db()
    assert upload.status == Upload.Status.AWAITING_APPROVAL
    assert not Posting.objects.exists()
    assert not AuditEvent.objects.filter(action="approval_refused").exists()
    holds(user, "approve")
    assert commit_upload(upload, user).status == Upload.Status.COMMITTED
