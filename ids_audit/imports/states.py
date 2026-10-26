"""Moving an upload between the states in ENG-02 6.

Each step locks the upload's row, checks the state it starts from, and moves
it with move(), which writes the move's audit event in the same transaction.
So no state changes without its event (ENG-02 2).
"""

import datetime

from django.db import transaction
from django.utils import timezone

from ids_audit import audit
from ids_audit.accounts.models import User
from ids_audit.audit.models import AuditEvent
from ids_audit.imports.models import Upload
from ids_audit.reports.models import Report

Status = Upload.Status

# The states of an upload that's still open: a report has at most one (ENG-02 6).
OPEN = (Status.READING, Status.NEEDS_COLUMNS, Status.AWAITING_APPROVAL)

# An upload still at reading this long after it was received was interrupted
# (ENG-02 6).
READ_TIME_LIMIT = datetime.timedelta(minutes=10)
INTERRUPTED = "Reading was interrupted"


class Refused(Exception):
    """An action that's refused and changes nothing. Its message is what the
    user is told.
    """


class WrongState(Refused):
    """The upload isn't in the state the step starts from, so the step is
    refused and nothing changes (ENG-02 6).
    """


def lock_upload(upload: Upload, *statuses: str) -> Upload:
    """Lock the upload's row until the transaction ends, and return it as
    stored. Refuse it with WrongState unless it's at one of the statuses.
    """
    locked = Upload.objects.select_for_update().get(pk=upload.pk)
    if locked.status not in statuses:
        expected = " or ".join(Status(s).label.lower() for s in statuses)
        raise WrongState(
            f"Upload {locked.number} is {locked.get_status_display().lower()}, "
            f"not {expected}."
        )
    return locked


def open_upload(report: Report) -> Upload | None:
    """Return the report's open upload, if it has one (ENG-02 6)."""
    return report.uploads.filter(status__in=OPEN).order_by("number").first()


def given_reason(reason: str) -> str:
    """Return a reason trimmed, refusing an empty one (ENG-02 6 and 10.3)."""
    reason = reason.strip()
    if not reason:
        raise ValueError("A reason is needed.")
    return reason


def move(
    upload: Upload, status: str, action: str, user: User | None, details: dict
) -> None:
    """Save a locked upload at its new status, with every field the step set
    on it, and write the move's audit event. user is None for a change the
    application makes on its own (ENG-01 6.3).
    """
    upload.status = status
    upload.save()
    audit.record(
        action, user=user, report=upload.report, upload=upload, details=details
    )


def fail_interrupted_reads(report: Report) -> list[Upload]:
    """Mark failed each of the report's uploads still at reading more than 10
    minutes after it was received, and return them (ENG-02 6).

    A read that's still running keeps its upload's row locked, so it's
    skipped, not waited for.
    """
    received_before = timezone.now() - READ_TIME_LIMIT
    with transaction.atomic():
        interrupted = list(
            Upload.objects.select_for_update(skip_locked=True).filter(
                report=report, status=Status.READING, uploaded_at__lt=received_before
            )
        )
        for upload in interrupted:
            upload.error = INTERRUPTED
            move(
                upload,
                Status.FAILED,
                AuditEvent.Action.UPLOAD_FAILED,
                None,
                {"error": INTERRUPTED},
            )
    return interrupted
