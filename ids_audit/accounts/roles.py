"""Changes to roles and report access, each written to the audit log in the
same transaction (ENG-01 6.2 and 6.3).

Only fixed administrators make them, from the account screens, and each
function checks that again itself. A change the rules refuse is a
ValidationError, whose message the screen shows, and nothing is saved.
"""

from django.core.exceptions import ValidationError
from django.db import transaction

from ids_audit import audit
from ids_audit.accounts.models import ReportAccess, Role, User
from ids_audit.accounts.permissions import in_order
from ids_audit.accounts.services import changes, require_fixed_admin
from ids_audit.audit.models import AuditEvent
from ids_audit.reports.models import Report

Action = AuditEvent.Action


def create_role(by: User, name: str, permissions) -> Role:
    """Create a role and write role_created. Its permissions are kept once
    each, in the plan's order.
    """
    require_fixed_admin(by)
    role = Role(name=name.strip(), permissions=in_order(permissions))
    # Checks the name and the permissions against the rules in Role.Meta.
    role.full_clean()
    with transaction.atomic():
        role.save()
        audit.record(
            Action.ROLE_CREATED,
            user=by,
            details={"name": role.name, "permissions": role.permissions},
        )
    return role


def change_role(by: User, role: Role, name: str, permissions) -> Role:
    """Rename a role or change its permissions, and write role_changed with
    each field that changed. A change of nothing writes nothing.
    """
    require_fixed_admin(by)
    with transaction.atomic():
        role = Role.objects.select_for_update().get(pk=role.pk)
        before = {"name": role.name, "permissions": role.permissions}
        role.name, role.permissions = name.strip(), in_order(permissions)
        changed = changes(before, {"name": role.name, "permissions": role.permissions})
        if not changed:
            return role
        role.full_clean()
        role.save()
        audit.record(
            Action.ROLE_CHANGED,
            user=by,
            details={"name": role.name, "changed": changed},
        )
    return role


def remove_role(by: User, role: Role) -> None:
    """Remove a role and write role_removed, refusing one still given to
    anyone on any report (ENG-01 6.2).
    """
    require_fixed_admin(by)
    with transaction.atomic():
        role = Role.objects.select_for_update().get(pk=role.pk)
        given = role.access.count()
        if given:
            raise ValidationError(
                f"The role {role} is still given {given} "
                f"time{'s' if given != 1 else ''}, so it can't be removed. "
                "Change that access first."
            )
        role.delete()
        audit.record(
            Action.ROLE_REMOVED,
            user=by,
            details={"name": role.name, "permissions": role.permissions},
        )


def set_access(by: User, user: User, report: Report, role: Role | None) -> str | None:
    """Give the user this role on the report, or with None take their access
    away, and write access_granted, access_changed or access_removed. Return
    the action written, or None when nothing changed.

    A fixed administrator gets no access rows, since they hold every
    permission on every report already (ENG-01 6.2).
    """
    require_fixed_admin(by)
    if user.is_fixed_admin:
        raise ValidationError(
            f"{user} is a fixed administrator, who holds every permission on "
            "every report already."
        )
    with transaction.atomic():
        # One change to a user's access at a time.
        User.objects.select_for_update().get(pk=user.pk)
        access = (
            ReportAccess.objects.select_related("role")
            .filter(user=user, report=report)
            .first()
        )
        if access is None and role is None:
            return None
        if access is None:
            ReportAccess.objects.create(user=user, report=report, role=role)
            action, details = Action.ACCESS_GRANTED, {"role": role.name}
        elif role is None:
            access.delete()
            action, details = Action.ACCESS_REMOVED, {"role": access.role.name}
        elif access.role_id == role.pk:
            return None
        else:
            before = access.role.name
            access.role = role
            access.save(update_fields=["role"])
            action = Action.ACCESS_CHANGED
            details = {"changed": {"role": {"before": before, "after": role.name}}}
        audit.record(
            action,
            user=by,
            report=report,
            details={"username": user.username, **details},
        )
    return action
