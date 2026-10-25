"""Sign-ins, sign-outs, failures and lock-outs in the audit log (ENG-01 6.1
and 6.3). AccountsConfig.ready() connects these.
"""

from axes.signals import user_locked_out
from django.contrib.auth.signals import (
    user_logged_in,
    user_logged_out,
    user_login_failed,
)
from django.dispatch import receiver

from ids_audit import audit
from ids_audit.accounts.models import User
from ids_audit.audit.models import AuditEvent

Action = AuditEvent.Action


def account(username: str) -> User | None:
    """Return the account with this username, or None for one no one has."""
    return User.objects.filter(username=username).first()


@receiver(user_logged_in)
def signed_in(sender, request, user, **kwargs) -> None:
    audit.record(Action.SIGN_IN, user=user)


@receiver(user_logged_out)
def signed_out(sender, request, user, **kwargs) -> None:
    # Signing out without being signed in changes nothing.
    if user is not None:
        audit.record(Action.SIGN_OUT, user=user)


@receiver(user_login_failed)
def sign_in_failed(sender, credentials, request=None, **kwargs) -> None:
    # Django has already hidden the password in credentials.
    username = credentials.get("username") or ""
    audit.record(
        Action.SIGN_IN_FAILED, user=account(username), details={"username": username}
    )


@receiver(user_locked_out)
def account_locked(sender, request, username, **kwargs) -> None:
    # django-axes sends this for the failure that locks the account, and again
    # for each try it refuses during the lock. It counts a failure, and sets
    # this, only for the first.
    if getattr(request, "axes_failures_since_start", None) is None:
        return
    audit.record(
        Action.ACCOUNT_LOCKED, user=account(username), details={"username": username}
    )
