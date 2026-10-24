"""Moving an upload between the states in ENG-02 6.

Each step locks the upload's row, checks the state it starts from, and moves
it with move(), which writes the move's audit event in the same transaction.
So no state changes without its event (ENG-02 2).
"""

from ids_audit import audit
from ids_audit.accounts.models import User
from ids_audit.imports.models import Upload


class WrongState(Exception):
    """The upload isn't in the state the step starts from, so the step is
    refused and nothing changes (ENG-02 6).
    """


def lock_upload(upload: Upload, status: str) -> Upload:
    """Lock the upload's row until the transaction ends, and return it as
    stored. Refuse it with WrongState unless it's at status.
    """
    locked = Upload.objects.select_for_update().get(pk=upload.pk)
    if locked.status != status:
        raise WrongState(
            f"Upload {locked.number} is {locked.get_status_display().lower()}, "
            f"not {Upload.Status(status).label.lower()}."
        )
    return locked


def move(upload: Upload, status: str, action: str, user: User, details: dict) -> None:
    """Save a locked upload at its new status, with every field the step set
    on it, and write the move's audit event.
    """
    upload.status = status
    upload.save()
    audit.record(
        action, user=user, report=upload.report, upload=upload, details=details
    )
