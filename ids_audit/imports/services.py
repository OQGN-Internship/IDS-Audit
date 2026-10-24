"""Receive: creating a report, and receiving a file as a numbered upload
(ENG-01 4, Build order, and 5.1).

Only imports/ writes uploads (ENG-01 4, rule 2). Each function writes its audit
event inside its own transaction, so an action and its event are saved
together or not at all.
"""

import hashlib

from django.db import transaction
from django.db.models import Max

from ids_audit import audit
from ids_audit.accounts.models import User
from ids_audit.audit.models import AuditEvent
from ids_audit.imports.models import Upload
from ids_audit.imports.storage import store_file
from ids_audit.pipeline.ruleset import DEFAULT_SETTINGS
from ids_audit.reports.models import Report, Ruleset


def create_report(name: str, user: User) -> Report:
    """Create a report, its ruleset version 1 and its report_created event, in
    one transaction (ENG-02 3).

    The name is trimmed and can't be empty. The database refuses a name that
    differs from another report's only in case, with an IntegrityError.
    """
    name = name.strip()
    if not name:
        raise ValueError("A report needs a name.")
    with transaction.atomic():
        report = Report.objects.create(name=name, created_by=user)
        Ruleset.objects.create(
            report=report, version=1, settings=DEFAULT_SETTINGS, created_by=user
        )
        audit.record(
            AuditEvent.Action.REPORT_CREATED,
            user=user,
            report=report,
            details={"name": name},
        )
    return report


def receive_file(report: Report, user: User, original_name: str, data: bytes) -> Upload:
    """Store a file under its hash, then create its upload at reading, numbered
    after the report's last upload (ENG-01 5.1, step 2).

    If the transaction fails, the stored file stays: another upload may share
    it, and a new try writes the same bytes.
    """
    # Over the bytes as received, before anything reads them (ENG-02 7.1).
    file_sha256 = hashlib.sha256(data).hexdigest()
    store_file(file_sha256, data)
    with transaction.atomic():
        # Locks the report's row until the transaction ends. A second file for
        # this report waits here, then reads the new highest number.
        report = Report.objects.select_for_update().get(pk=report.pk)
        highest = report.uploads.aggregate(highest=Max("number"))["highest"] or 0
        upload = Upload.objects.create(
            report=report,
            number=highest + 1,
            file_sha256=file_sha256,
            original_name=original_name,
            size_bytes=len(data),
            uploaded_by=user,
            # It will be read with the report's latest settings.
            ruleset=report.rulesets.order_by("-version").first(),
        )
        audit.record(
            AuditEvent.Action.UPLOAD_RECEIVED,
            user=user,
            report=report,
            upload=upload,
            details={
                "number": upload.number,
                "original_name": original_name,
                "size_bytes": upload.size_bytes,
                "file_sha256": file_sha256,
            },
        )
    return upload
