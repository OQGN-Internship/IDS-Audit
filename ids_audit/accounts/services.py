"""Changes to accounts, each written to the audit log in the same transaction
(ENG-01 6.1 and 6.3).

The commands in management/commands call these. An event written from the
command line has no user and keeps the command's name.
"""

from axes.utils import reset
from django.contrib.auth.password_validation import validate_password
from django.db import transaction

from ids_audit import audit
from ids_audit.accounts.models import User
from ids_audit.audit.models import AuditEvent

Action = AuditEvent.Action


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


def unlock(username: str, command: str) -> int:
    """End a lock-out by hand, clearing the username's failed sign-ins, and
    write account_unlocked if django-axes held any (ENG-01 6.1). Return how
    many of its records were removed.
    """
    with transaction.atomic():
        removed = reset(username=username)
        if removed:
            audit.record(
                Action.ACCOUNT_UNLOCKED,
                details={"username": username, "command": command},
            )
    return removed
