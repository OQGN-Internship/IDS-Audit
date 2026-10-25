"""Changes to accounts, each written to the audit log in the same transaction
(ENG-01 6.1 and 6.3).

The commands in management/commands and the administration screens call
these. An event written from the command line has no user and keeps the
command's name. One written from a screen names the administrator, and each
function a screen calls checks again that they're an active fixed
administrator. A change the rules refuse is a ValidationError, whose message
the screen shows, and nothing is saved.
"""

import datetime

from axes.models import AccessAttempt
from axes.utils import reset
from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max, Sum
from django.utils import timezone

from ids_audit import audit
from ids_audit.accounts.models import User
from ids_audit.audit.models import AuditEvent

Action = AuditEvent.Action


def require_fixed_admin(user: User) -> None:
    """Refuse anyone but an active fixed administrator, who alone creates
    accounts and roles and grants access (the plan, Administrators).
    """
    if not (user.is_active and user.is_fixed_admin):
        raise PermissionDenied


def refuse_fixed_admin(user: User) -> None:
    """Refuse a change from the screens to a fixed administrator, whose
    account is changed only from the command line (ENG-01 6.1).
    """
    if user.is_fixed_admin:
        raise ValidationError(
            f"{user} is a fixed administrator, whose account is changed only "
            "from the command line."
        )


def changes(before: dict, after: dict) -> dict:
    """Return each field whose value differs, with its value before and after,
    as an event keeps it under "changed" (ENG-01 6.3).
    """
    return {
        field: {"before": before[field], "after": after[field]}
        for field in before
        if before[field] != after[field]
    }


def create_fixed_admin(username: str, full_name: str, password: str) -> User:
    """Create a fixed administrator who must change the initial password at
    first sign-in, and write user_created (ENG-01 6.1).

    A username already taken, an empty full name or a password the validators
    refuse is a ValidationError, and nothing is saved.
    """
    user = User(
        username=username,
        full_name=full_name.strip(),
        is_fixed_admin=True,
        must_change_password=True,
    )
    validate_password(password, user)
    user.set_password(password)
    # Checks the username's characters, that no one has it, and the full name.
    user.full_clean()
    with transaction.atomic():
        user.save()
        audit.record(
            Action.USER_CREATED,
            details={
                "username": user.username,
                "full_name": user.full_name,
                "is_fixed_admin": True,
                "command": "create_admin",
            },
        )
    return user


def disable_fixed_admin(user: User) -> None:
    """Disable a fixed administrator and write user_disabled (ENG-01 6.1).

    The row is updated directly, past User.save(), which refuses this change
    from anywhere else. The account's history stays linked to it.
    """
    with transaction.atomic():
        User.objects.filter(pk=user.pk).update(is_active=False)
        audit.record(
            Action.USER_DISABLED,
            details={"username": user.username, "command": "disable_admin"},
        )


def password_was_reset(user: User, command: str) -> None:
    """After someone else set a user's password, make the user choose their
    own at the next sign-in, and write password_reset (ENG-01 6.1).
    """
    user.must_change_password = True
    user.save(update_fields=["must_change_password"])
    audit.record(
        Action.PASSWORD_RESET,
        details={"username": user.username, "command": command},
    )


def password_was_changed(user: User) -> None:
    """After a user chose their own password, stop asking for a new one, and
    write password_changed (ENG-01 6.1).
    """
    user.must_change_password = False
    user.save(update_fields=["must_change_password"])
    audit.record(Action.PASSWORD_CHANGED, user=user)


def unlock(username: str, command: str | None = None, by: User | None = None) -> int:
    """End a lock-out by hand, clearing the username's failed sign-ins, and
    write account_unlocked if django-axes held any (ENG-01 6.1). Return how
    many of its records were removed.

    From the command line, command names the command. From a screen, by is
    the administrator, who can't unlock a fixed administrator.
    """
    if by is not None:
        require_fixed_admin(by)
        if User.objects.filter(username=username, is_fixed_admin=True).exists():
            raise ValidationError(
                f"{username} is a fixed administrator, who is unlocked only from "
                "the command line."
            )
    with transaction.atomic():
        removed = reset(username=username)
        if removed:
            details = {"username": username}
            if command:
                details["command"] = command
            audit.record(Action.ACCOUNT_UNLOCKED, user=by, details=details)
    return removed


def locked_until(username: str) -> datetime.datetime | None:
    """Return when the username's lock-out ends, or None if it isn't locked
    out (ENG-01 6.1). django-axes counts the failures within the cool-off,
    and the lock lasts from the failure that started it.
    """
    since = timezone.now() - settings.AXES_COOLOFF_TIME
    found = AccessAttempt.objects.filter(
        username=username, attempt_time__gte=since
    ).aggregate(failures=Sum("failures_since_start"), last=Max("attempt_time"))
    if (found["failures"] or 0) < settings.AXES_FAILURE_LIMIT:
        return None
    return found["last"] + settings.AXES_COOLOFF_TIME


def create_user(
    by: User, username: str, full_name: str, email: str, password: str
) -> User:
    """Create an account that isn't a fixed administrator, with a temporary
    password the user must change at first sign-in, and write user_created
    (ENG-01 6.1).
    """
    require_fixed_admin(by)
    user = User(
        username=username.strip(),
        full_name=full_name.strip(),
        email=email.strip(),
        must_change_password=True,
    )
    validate_password(password, user)
    user.set_password(password)
    # Checks the username's characters, that no one has it, and the full name.
    user.full_clean()
    with transaction.atomic():
        user.save()
        audit.record(
            Action.USER_CREATED,
            user=by,
            details={
                "username": user.username,
                "full_name": user.full_name,
                "is_fixed_admin": False,
            },
        )
    return user


def change_user(by: User, user: User, full_name: str, email: str) -> User:
    """Correct an account's full name or email, and write user_changed with
    each field that changed. A change of nothing writes nothing.
    """
    require_fixed_admin(by)
    refuse_fixed_admin(user)
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=user.pk)
        before = {"full_name": user.full_name, "email": user.email}
        user.full_name, user.email = full_name.strip(), email.strip()
        changed = changes(before, {"full_name": user.full_name, "email": user.email})
        if not changed:
            return user
        user.full_clean()
        user.save(update_fields=["full_name", "email"])
        audit.record(
            Action.USER_CHANGED,
            user=by,
            details={"username": user.username, "changed": changed},
        )
    return user


def reset_password(by: User, user: User, password: str) -> None:
    """Set a new temporary password, which the user must change at the next
    sign-in, and write password_reset (ENG-01 6.1). A session the user still
    has ends, since its password changed.
    """
    require_fixed_admin(by)
    refuse_fixed_admin(user)
    validate_password(password, user)
    with transaction.atomic():
        user.set_password(password)
        user.must_change_password = True
        user.save(update_fields=["password", "must_change_password"])
        audit.record(
            Action.PASSWORD_RESET, user=by, details={"username": user.username}
        )


def disable_user(by: User, user: User) -> None:
    """Disable an account and write user_disabled. Its history stays linked
    to it, and a session it still has ends at its next request (ENG-01 6.1).
    """
    require_fixed_admin(by)
    refuse_fixed_admin(user)
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=user.pk)
        if not user.is_active:
            return
        user.is_active = False
        user.save(update_fields=["is_active"])
        audit.record(Action.USER_DISABLED, user=by, details={"username": user.username})


def enable_user(by: User, user: User) -> None:
    """Enable a disabled account again, and write user_changed (ENG-01 6.3)."""
    require_fixed_admin(by)
    refuse_fixed_admin(user)
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=user.pk)
        if user.is_active:
            return
        user.is_active = True
        user.save(update_fields=["is_active"])
        audit.record(
            Action.USER_CHANGED,
            user=by,
            details={
                "username": user.username,
                "changed": changes({"is_active": False}, {"is_active": True}),
            },
        )
