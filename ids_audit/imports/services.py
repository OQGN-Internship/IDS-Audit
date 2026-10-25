"""Receive: creating a report, and receiving a file as a numbered upload
(ENG-01 4, Build order, and 5.1).

Only imports/ writes uploads (ENG-01 4, rule 2). Each function writes its audit
event inside its own transaction, so an action and its event are saved
together or not at all.
"""

import hashlib
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from ids_audit import audit
from ids_audit.accounts.models import User
from ids_audit.audit.models import AuditEvent
from ids_audit.imports.models import Upload
from ids_audit.imports.states import Refused, fail_interrupted_reads, open_upload
from ids_audit.imports.storage import store_file
from ids_audit.pipeline.cleaning import TOO_LARGE, has_more_than_3_decimals
from ids_audit.pipeline.formats import UnknownFormat, detect_format
from ids_audit.pipeline.reconciliation import amount_text
from ids_audit.pipeline.ruleset import DEFAULT_SETTINGS
from ids_audit.reports.models import Report, Ruleset

Status = Upload.Status

# MAX_UPLOAD_MB counts megabytes of this many bytes (ENG-01 6.4).
MEGABYTE = 1024 * 1024


class UploadRefused(Refused):
    """A file refused before an upload exists (ENG-02 6).

    reason is the code upload_refused keeps, and details the other facts it
    keeps (ENG-01 6.3).
    """

    TOO_LARGE = "too_large"
    UNKNOWN_FORMAT = "unknown_format"
    ALREADY_COMMITTED = "already_committed"
    OPEN_UPLOAD = "open_upload"

    def __init__(self, reason: str, message: str, details: dict) -> None:
        super().__init__(message)
        self.reason = reason
        self.details = details


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


def receive_file(
    report: Report,
    user: User,
    original_name: str,
    data: bytes,
    control_total: Decimal | None = None,
) -> Upload:
    """Store a file under its hash, then create its upload at reading, numbered
    after the report's last upload (ENG-01 5.1, step 2).

    control_total is SAP's total for the file, if the uploader typed one on
    the upload form. It's used only if the file has no total row (ENG-02 9.1),
    and a value that isn't an amount numeric(18,3) holds is a ValueError.

    A file over MAX_UPLOAD_MB, of an unknown format, matching a committed
    upload in the report, or sent while the report has an open upload is
    refused with UploadRefused: nothing is stored, no upload is created, and an
    upload_refused event keeps the refusal (ENG-02 6). If the transaction fails
    after the file is stored, the stored file stays: another upload may share
    it, and a new try writes the same bytes.
    """
    check_typed_total(control_total)
    facts = {"original_name": original_name, "size_bytes": len(data)}
    try:
        # Before anything reads the file (ENG-01 6.4).
        if len(data) > settings.MAX_UPLOAD_MB * MEGABYTE:
            raise UploadRefused(
                UploadRefused.TOO_LARGE,
                f"The file is larger than {settings.MAX_UPLOAD_MB} MB, the most a "
                "file may be.",
                facts,
            )
        try:
            detect_format(data)
        except UnknownFormat as error:
            raise UploadRefused(
                UploadRefused.UNKNOWN_FORMAT, str(error), facts
            ) from None

        # So a read that was interrupted doesn't count as an open upload.
        fail_interrupted_reads(report)
        # Over the bytes as received (ENG-02 7.1).
        file_sha256 = hashlib.sha256(data).hexdigest()
        facts["file_sha256"] = file_sha256
        with transaction.atomic():
            # Locks the report's row until the transaction ends. A second file
            # for this report waits here, then sees this one's upload.
            report = Report.objects.select_for_update().get(pk=report.pk)
            refuse_if_blocked(report, file_sha256, facts)
            store_file(file_sha256, data)
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
                control_total=control_total,
                control_total_source=(
                    "" if control_total is None else Upload.ControlTotalSource.ENTERED
                ),
            )
            details = {
                "number": upload.number,
                "original_name": original_name,
                "size_bytes": upload.size_bytes,
                "file_sha256": file_sha256,
            }
            if control_total is not None:
                details["control_total"] = amount_text(control_total)
            audit.record(
                AuditEvent.Action.UPLOAD_RECEIVED,
                user=user,
                report=report,
                upload=upload,
                details=details,
            )
    except UploadRefused as refusal:
        # Written after the refused transaction is undone, so it's kept.
        audit.record(
            AuditEvent.Action.UPLOAD_REFUSED,
            user=user,
            report=report,
            details={"reason": refusal.reason, **refusal.details},
        )
        raise
    return upload


def check_typed_total(control_total: Decimal | None) -> None:
    """Refuse a typed control total that isn't an amount numeric(18,3) holds
    exactly: one with more than 3 decimal places, or not a number at all.
    """
    if control_total is None:
        return
    if not control_total.is_finite() or has_more_than_3_decimals(control_total):
        raise ValueError("A control total is an amount with at most 3 decimals.")
    if abs(control_total) >= TOO_LARGE:
        raise ValueError("A control total must be less than 10^15.")


def refuse_if_blocked(report: Report, file_sha256: str, facts: dict) -> None:
    """Refuse a file that matches a committed upload in the report (ENG-02
    7.1), or that's sent while the report has an open upload (6). The report's
    row must be locked, so no other file can change either answer meanwhile.
    """
    uploads = report.uploads.order_by("number")
    same = uploads.filter(status=Status.COMMITTED, file_sha256=file_sha256).first()
    if same is not None:
        on = timezone.localtime(same.decided_at).strftime("%d.%m.%Y")
        raise UploadRefused(
            UploadRefused.ALREADY_COMMITTED,
            f"This file was already committed on {on} as upload {same.number}.",
            {**facts, "number": same.number},
        )
    still_open = open_upload(report)
    if still_open is not None:
        raise UploadRefused(
            UploadRefused.OPEN_UPLOAD,
            f"Upload {still_open.number} is still open "
            f"({still_open.get_status_display().lower()}). A report takes one "
            "file at a time, so it must be approved, rejected or withdrawn first.",
            {**facts, "number": still_open.number},
        )
