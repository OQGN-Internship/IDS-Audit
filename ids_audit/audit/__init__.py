"""The audit app: the append-only audit log (ENG-01 4 and 6.3). Every other
part writes to it only through record().
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ids_audit.accounts.models import User
    from ids_audit.audit.models import AuditEvent
    from ids_audit.imports.models import Upload
    from ids_audit.reports.models import Report


def record(
    action: str,
    *,
    user: User | None = None,
    report: Report | None = None,
    upload: Upload | None = None,
    details: dict | None = None,
) -> AuditEvent:
    """Write one audit event, inside the caller's transaction, and return it.

    The arguments after action are passed by name, so a report can't take an
    upload's place. The event's time is set when it's saved.
    """
    # Imported here, not at the top: Django imports this package before any
    # model is ready.
    from ids_audit.audit.models import AuditEvent

    # The database doesn't check a model's choices, so this does.
    if action not in AuditEvent.Action.values:
        raise ValueError(f"{action!r} isn't an audit action (ENG-01 6.3).")
    return AuditEvent.objects.create(
        action=action, user=user, report=report, upload=upload, details=details or {}
    )
