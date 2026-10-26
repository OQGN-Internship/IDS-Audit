"""Roles and report access, and the events their changes write (ENG-01 6.2
and 6.3).
"""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from ids_audit.accounts.models import ReportAccess, Role, User
from ids_audit.accounts.roles import (
    change_role,
    create_role,
    remove_role,
    set_access,
)
from ids_audit.audit.models import AuditEvent
from tests.factories import grant, make_report, make_role, make_user

pytestmark = pytest.mark.django_db


def events(action: str) -> list[AuditEvent]:
    return list(AuditEvent.objects.filter(action=action).order_by("id"))


def test_the_preset_roles_hold_the_plan_s_permissions() -> None:
    """Verify the migrations create Viewer, Uploader and Approver with the
    plan's permissions, in its order.
    """
    assert dict(Role.objects.values_list("name", "permissions")) == {
        "Viewer": ["view", "export"],
        "Uploader": ["view", "export", "upload"],
        "Approver": ["view", "export", "approve", "roll_back", "view_audit_log"],
    }


@pytest.mark.parametrize(
    ("name", "permissions", "message"),
    [
        ("Exporter", ["export"], "Every role holds View"),
        ("Editor", ["view", "edit"], "not a valid choice"),
        ("viewer", ["view"], "A role with this name already exists"),
        ("  ", ["view"], "cannot be blank"),
    ],
)
def test_a_role_that_breaks_a_rule_is_refused(name, permissions, message) -> None:
    """Verify a role without view, with a permission that isn't one of the
    six, with another role's name in other capitals, or with no name is
    refused with its rule's message, and nothing is saved.
    """
    with pytest.raises(ValidationError, match=message):
        create_role(make_user(), name, permissions)
    assert Role.objects.count() == 3
    assert not events("role_created")


@pytest.mark.parametrize(
    ("name", "permissions"),
    [("Exporter", ["export"]), ("Editor", ["view", "edit"]), ("VIEWER", ["view"])],
)
def test_the_database_refuses_a_role_that_breaks_a_rule(name, permissions) -> None:
    """Verify the database itself refuses a role without view, with an
    unknown permission, or with another role's name in other capitals.
    """
    with pytest.raises(IntegrityError), transaction.atomic():
        Role.objects.create(name=name, permissions=permissions)


def test_a_second_access_row_for_a_user_and_report_is_refused() -> None:
    """Verify the database keeps at most one ReportAccess row per user and
    report, whatever the role.
    """
    user, report = make_user(fixed_admin=False), make_report()
    grant(user, report, "Viewer")
    with pytest.raises(IntegrityError), transaction.atomic():
        grant(user, report, "Approver")


def test_creating_changing_and_removing_a_role_write_their_events() -> None:
    """Verify each change to a role writes its event, by the administrator,
    with what ENG-01 6.3 says it keeps, and a change of nothing writes
    nothing.
    """
    admin = make_user()
    role = create_role(admin, " Auditor ", ["view_audit_log", "view", "view"])
    assert (role.name, role.permissions) == ("Auditor", ["view", "view_audit_log"])
    (created,) = events("role_created")
    assert created.user == admin and created.report is None
    assert created.details == {
        "name": "Auditor",
        "permissions": ["view", "view_audit_log"],
    }

    change_role(admin, role, "Auditor", ["view", "view_audit_log"])
    assert not events("role_changed")
    change_role(admin, role, "Checker", ["view", "export", "view_audit_log"])
    (changed,) = events("role_changed")
    assert changed.user == admin
    assert changed.details == {
        "name": "Checker",
        "changed": {
            "name": {"before": "Auditor", "after": "Checker"},
            "permissions": {
                "before": ["view", "view_audit_log"],
                "after": ["view", "export", "view_audit_log"],
            },
        },
    }
    change_role(admin, role, "Checker", ["view"])
    assert events("role_changed")[1].details["changed"] == {
        "permissions": {
            "before": ["view", "export", "view_audit_log"],
            "after": ["view"],
        }
    }

    remove_role(admin, role)
    assert not Role.objects.filter(pk=role.pk).exists()
    (removed,) = events("role_removed")
    assert removed.user == admin
    assert removed.details == {"name": "Checker", "permissions": ["view"]}


def test_a_role_change_that_breaks_a_rule_is_refused() -> None:
    """Verify a change to a preset role that drops view is refused, and the
    role and the log stay as they were.
    """
    viewer = Role.objects.get(name="Viewer")
    with pytest.raises(ValidationError, match="Every role holds View"):
        change_role(make_user(), viewer, "Viewer", ["export"])
    viewer.refresh_from_db()
    assert viewer.permissions == ["view", "export"]
    assert not events("role_changed")


def test_a_role_still_given_to_someone_can_t_be_removed() -> None:
    """Verify removing a role still given on a report is refused with no
    event, by the database too, and works once that access is gone.
    """
    admin, user, report = make_user(), make_user(fixed_admin=False), make_report()
    role = make_role("view")
    grant(user, report, role)
    with pytest.raises(ValidationError, match="still given 1 time,"):
        remove_role(admin, role)
    with pytest.raises(ProtectedError):
        role.delete()
    assert Role.objects.filter(pk=role.pk).exists()
    assert not events("role_removed")
    set_access(admin, user, report, None)
    remove_role(admin, role)
    assert not Role.objects.filter(pk=role.pk).exists()


def test_granting_changing_and_removing_access_write_their_events() -> None:
    """Verify each change to a user's access writes its event, naming the
    report, and a change of nothing writes nothing.
    """
    admin, user, report = make_user(), make_user(fixed_admin=False), make_report()
    viewer, approver = (
        Role.objects.get(name="Viewer"),
        Role.objects.get(name="Approver"),
    )

    assert set_access(admin, user, report, None) is None
    assert set_access(admin, user, report, viewer) == "access_granted"
    assert ReportAccess.objects.get(user=user, report=report).role == viewer
    assert set_access(admin, user, report, viewer) is None
    assert set_access(admin, user, report, approver) == "access_changed"
    assert ReportAccess.objects.get(user=user, report=report).role == approver
    assert set_access(admin, user, report, None) == "access_removed"
    assert not ReportAccess.objects.exists()

    granted, changed, removed = AuditEvent.objects.filter(
        action__startswith="access_"
    ).order_by("id")
    for event in (granted, changed, removed):
        assert (event.user, event.report) == (admin, report)
    assert granted.details == {"username": user.username, "role": "Viewer"}
    assert changed.details == {
        "username": user.username,
        "changed": {"role": {"before": "Viewer", "after": "Approver"}},
    }
    assert removed.details == {"username": user.username, "role": "Approver"}


def test_a_fixed_admin_gets_no_access_row() -> None:
    """Verify access can't be given to a fixed administrator, who holds
    every permission already.
    """
    with pytest.raises(ValidationError, match="fixed administrator"):
        set_access(make_user(), make_user(), make_report(), Role.objects.first())
    assert not ReportAccess.objects.exists()


def test_only_an_active_fixed_admin_changes_roles_and_access() -> None:
    """Verify each change is refused for a user who isn't a fixed
    administrator, and for a fixed administrator who was disabled.
    """
    disabled = make_user()
    User.objects.filter(pk=disabled.pk).update(is_active=False)
    disabled.refresh_from_db()
    role, report, user = make_role("view"), make_report(), make_user(fixed_admin=False)
    for by in (make_user(fixed_admin=False), disabled):
        with pytest.raises(PermissionDenied):
            create_role(by, "New", ["view"])
        with pytest.raises(PermissionDenied):
            change_role(by, role, "Renamed", ["view"])
        with pytest.raises(PermissionDenied):
            remove_role(by, role)
        with pytest.raises(PermissionDenied):
            set_access(by, user, report, role)
    assert not AuditEvent.objects.exists()
