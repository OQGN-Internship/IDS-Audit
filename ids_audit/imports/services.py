"""Receive: creating a report, changing its ruleset, and receiving a file as a
numbered upload (ENG-01 4, Build order, and 5.1; ENG-02 3).

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
from ids_audit.accounts.services import require_fixed_admin
from ids_audit.audit.models import AuditEvent
from ids_audit.imports.models import Upload
from ids_audit.imports.states import (
    OPEN,
    Refused,
    fail_interrupted_reads,
    open_upload,
)
from ids_audit.imports.storage import store_file
from ids_audit.pipeline import ruleset
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


class RulesetRefused(Refused):
    """A ruleset change refused, with nothing written (ENG-02 3). problems
    lists each reason.
    """

    def __init__(self, problems: list[str]) -> None:
        super().__init__(" ".join(problems))
        self.problems = problems


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


def change_ruleset(
    report: Report,
    user: User,
    changes: dict,
    command: str | None = None,
    *,
    screen: str | None = None,
    based_on: int | None = None,
) -> Ruleset:
    """Write the report's next ruleset version, its latest with the changes
    made, and its ruleset_changed event, in one transaction (ENG-02 3).

    user must be an active fixed administrator, or it's PermissionDenied. A
    change check_change() refuses is refused with RulesetRefused, and writes
    nothing. command is the manage.py command it was run with, and screen
    the screen it was sent from, which the event keeps (ENG-01 6.3).
    based_on is the version a change made on screen was made from.
    """
    require_fixed_admin(user)
    with transaction.atomic():
        # Locked, as receiving a file locks it, so no upload opens while a
        # setting that acts on the report's figures changes (ENG-01 5.1).
        report = Report.objects.select_for_update().get(pk=report.pk)
        latest, new_settings = check_change(report, changes, based_on)
        version = Ruleset.objects.create(
            report=report,
            version=latest.version + 1,
            settings=new_settings,
            created_by=user,
        )
        changed = {
            key: {"before": latest.settings[key], "after": new_settings[key]}
            for key in new_settings
            if new_settings[key] != latest.settings[key]
        }
        details = {"version": version.version, "changed": changed}
        if command:
            details["command"] = command
        if screen:
            details["screen"] = screen
        audit.record(
            AuditEvent.Action.RULESET_CHANGED,
            user=user,
            report=report,
            details=details,
        )
    return version


def check_change(
    report: Report, changes: dict, based_on: int | None = None
) -> tuple[Ruleset, dict]:
    """Return the report's latest ruleset version and the settings the change
    would give it, or refuse the change with RulesetRefused, writing nothing
    (ENG-02 3). change_ruleset() calls it with the report's row locked, and a
    change's confirmation on screen without, to show only what would save.

    A change is refused when it was made from a version that's no longer the
    latest, gives a setting that doesn't exist or a value out of its form,
    leaves every setting as it was, or changes outside_ids_rule or
    mapping_rule while the report has an open or a committed upload.
    """
    latest = report.rulesets.order_by("-version").first()
    if based_on is not None and based_on != latest.version:
        raise RulesetRefused(
            [
                f"The rules were changed to version {latest.version} while this "
                f"change was being made from version {based_on}. Look at version "
                f"{latest.version}, then make the change again."
            ]
        )
    new_settings = ruleset.merged(latest.settings, changes)
    problems = ruleset.problems(new_settings)
    if problems:
        raise RulesetRefused(problems)
    changed = [key for key in new_settings if new_settings[key] != latest.settings[key]]
    if not changed:
        raise RulesetRefused(["The change leaves every setting as it was."])
    shown = [key for key in ruleset.SHOWN_SETTINGS if key in changed]
    if shown and shown_settings_locked(report):
        raise RulesetRefused([locked_reason(key) for key in shown])
    return latest, new_settings


def shown_settings_locked(report: Report) -> bool:
    """Return whether outside_ids_rule and mapping_rule can't change now: the
    report has an open or a committed upload (ENG-02 3).
    """
    return report.uploads.filter(status__in=(*OPEN, Status.COMMITTED)).exists()


def locked_reason(key: str) -> str:
    """Say why a setting that acts whenever the report is shown can't change,
    in the words the Rules screen and a refused change share (ENG-02 3).
    """
    return (
        f"{ruleset.SETTING_NAMES[key]}: can't change while the report has an open "
        "or a committed upload, since figures already shown would move."
    )


def receive_file(
    report: Report,
    user: User,
    original_name: str,
    data: bytes,
    control_total: Decimal | None = None,
    kind: str = Upload.Kind.SPEND,
) -> Upload:
    """Store a file under its hash, then create its upload at reading, numbered
    after the report's last upload (ENG-01 5.1, step 2).

    control_total is SAP's total for the file, if the uploader typed one on
    the upload form. It's used only if the file has no total row (ENG-02 9.1),
    and a value that isn't an amount numeric(18,3) holds is a ValueError.
    kind is the form the file was sent with: spend, or mapping for the
    mapping sheets on their own, which have no total (8.5).

    A file over MAX_UPLOAD_MB, of an unknown format, matching a committed
    upload in the report, or sent while the report has an open upload is
    refused with UploadRefused: nothing is stored, no upload is created, and an
    upload_refused event keeps the refusal (ENG-02 6). If the transaction fails
    after the file is stored, the stored file stays: another upload may share
    it, and a new try writes the same bytes.
    """
    kind = Upload.Kind(kind)
    if kind == Upload.Kind.MAPPING and control_total is not None:
        raise ValueError("A mapping has no spend lines, so no control total.")
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
                kind=kind,
                control_total=control_total,
                control_total_source=(
                    "" if control_total is None else Upload.ControlTotalSource.ENTERED
                ),
            )
            details = {
                "number": upload.number,
                "kind": kind.value,
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
