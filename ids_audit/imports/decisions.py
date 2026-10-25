"""Rejecting and withdrawing an upload before it counts (ENG-02 6).

Either needs a reason, kept in the audit event, and leaves the report as it
was: no posting was written. The upload's skipped rows, mapping rows and
preview stay, so a rejected file can still be explained.
"""

from django.db import transaction
from django.utils import timezone

from ids_audit.accounts.models import User
from ids_audit.audit.models import AuditEvent
from ids_audit.imports.models import StagingRow, Upload
from ids_audit.imports.states import given_reason, lock_upload, move

Status = Upload.Status
Action = AuditEvent.Action


def reject_upload(upload: Upload, user: User, reason: str) -> Upload:
    """Reject an upload at needs_columns or awaiting_approval, as a user with
    approve (ENG-02 6).
    """
    return close(upload, user, reason, Action.UPLOAD_REJECTED)


def withdraw_upload(upload: Upload, user: User, reason: str) -> Upload:
    """Withdraw an upload at needs_columns or awaiting_approval, as its
    uploader (ENG-02 6, ENG-01 6.2 rule 6).
    """
    return close(upload, user, reason, Action.UPLOAD_WITHDRAWN)


def close(upload: Upload, user: User, reason: str, action: str) -> Upload:
    """Move the upload to rejected with whoever decided, in one transaction,
    and delete its staging rows. An empty reason is a ValueError, and an
    upload that isn't open for a decision is refused with WrongState.
    """
    reason = given_reason(reason)
    with transaction.atomic():
        upload = lock_upload(upload, Status.NEEDS_COLUMNS, Status.AWAITING_APPROVAL)
        upload.decided_by = user
        upload.decided_at = timezone.now()
        move(upload, Status.REJECTED, action, user, {"reason": reason})
        StagingRow.objects.filter(upload=upload).delete()
    return upload
