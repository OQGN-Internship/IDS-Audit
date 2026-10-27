"""Receive: creating a report, and receiving a file as a numbered upload
(ENG-01 4, 5.1 and 7.5, ENG-02 3 and 6).
"""

import hashlib
import threading
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, connection, transaction

from ids_audit import audit
from ids_audit.audit.models import AuditEvent
from ids_audit.imports.models import Upload
from ids_audit.imports.services import create_report, receive_file
from ids_audit.pipeline.ruleset import DEFAULT_SETTINGS
from ids_audit.reports.models import Report, Ruleset
from tests.factories import make_report, make_upload, make_user
from tests.roles import as_app

pytestmark = pytest.mark.django_db

# A real file: the sample export (ENG-02 12.3).
SAMPLE = (Path(__file__).parent / "fixtures" / "sample.xlsx").read_bytes()
SAMPLE_SHA256 = hashlib.sha256(SAMPLE).hexdigest()


def fail(*args, **kwargs) -> None:
    """Stand in for audit.record, to make a step inside a transaction fail."""
    raise RuntimeError("the audit log is down")


# create_report() (ENG-02 3)


def test_create_report_writes_the_report_version_1_and_one_event() -> None:
    """Verify one call writes the report, its ruleset version 1 holding exactly
    DEFAULT_SETTINGS, and exactly one report_created event keeping the name.
    """
    user = make_user()
    report = create_report("IDS 2026", user)
    assert (report.name, report.created_by) == ("IDS 2026", user)
    ruleset = Ruleset.objects.get()
    assert (ruleset.report, ruleset.version, ruleset.created_by) == (report, 1, user)
    assert ruleset.settings == DEFAULT_SETTINGS
    event = AuditEvent.objects.get()
    assert (event.action, event.user, event.report, event.upload, event.details) == (
        "report_created",
        user,
        report,
        None,
        {"name": "IDS 2026"},
    )


def test_create_report_trims_the_name() -> None:
    """Verify spaces around the name are removed, in the report and its event."""
    report = create_report("  IDS 2026  ", make_user())
    assert report.name == "IDS 2026"
    assert AuditEvent.objects.get().details == {"name": "IDS 2026"}


@pytest.mark.parametrize("name", ["", "   "])
def test_create_report_refuses_an_empty_name(name: str) -> None:
    """Verify an empty or blank name is refused, and nothing is written."""
    with pytest.raises(ValueError, match="needs a name"):
        create_report(name, make_user())
    assert not Report.objects.exists()
    assert not AuditEvent.objects.exists()


def test_a_name_that_differs_only_in_case_is_refused() -> None:
    """Verify that once IDS 2026 exists, "ids 2026 " is refused by the database
    and leaves no row behind, while IDS 2027 is allowed.
    """
    user = make_user()
    create_report("IDS 2026", user)
    with pytest.raises(IntegrityError):
        create_report("ids 2026 ", user)
    assert Report.objects.count() == Ruleset.objects.count() == 1
    assert AuditEvent.objects.count() == 1
    create_report("IDS 2027", user)
    assert Report.objects.count() == 2


def test_a_failure_inside_create_report_leaves_nothing(monkeypatch) -> None:
    """Verify that if the event can't be written, the report and its ruleset
    aren't either (ENG-02 3).
    """
    monkeypatch.setattr(audit, "record", fail)
    with pytest.raises(RuntimeError):
        create_report("IDS 2026", make_user())
    assert not Report.objects.exists()
    assert not Ruleset.objects.exists()


# receive_file() (ENG-01 5.1, ENG-02 6)


def test_receive_file_creates_upload_1_at_reading_with_its_event(
    uploads_dir: Path,
) -> None:
    """Verify the first file becomes upload 1 at reading, with the file's hash,
    name and size, read with version 1, and exactly one upload_received event
    keeping those facts.
    """
    report = make_report()
    user = make_user()
    upload = receive_file(report, user, "FAGLL03_Jan.xlsx", SAMPLE)
    upload.refresh_from_db()
    assert (upload.number, upload.status) == (1, Upload.Status.READING)
    assert (upload.file_sha256, upload.original_name, upload.size_bytes) == (
        SAMPLE_SHA256,
        "FAGLL03_Jan.xlsx",
        len(SAMPLE),
    )
    assert (upload.uploaded_by, upload.ruleset.version) == (user, 1)
    event = AuditEvent.objects.get()
    assert (event.action, event.user, event.report, event.upload) == (
        "upload_received",
        user,
        report,
        upload,
    )
    assert event.details == {
        "number": 1,
        "kind": "spend",
        "original_name": "FAGLL03_Jan.xlsx",
        "size_bytes": len(SAMPLE),
        "file_sha256": SAMPLE_SHA256,
    }


def test_the_stored_file_is_byte_identical_under_its_hash(uploads_dir: Path) -> None:
    """Verify the file is stored unchanged, named by the SHA-256 of its bytes,
    with no temporary file left beside it.
    """
    receive_file(make_report(), make_user(), "FAGLL03_Jan.xlsx", SAMPLE)
    assert [path.name for path in uploads_dir.iterdir()] == [SAMPLE_SHA256]
    assert (uploads_dir / SAMPLE_SHA256).read_bytes() == SAMPLE


def test_uploads_are_numbered_in_the_order_they_are_created(
    uploads_dir: Path,
) -> None:
    """Verify a report's uploads are numbered 1, 2, 3, failed and rejected ones
    included (ENG-02 6), and another report starts again at 1.
    """
    report, user = make_report(), make_user()
    first = receive_file(report, user, "a.xlsx", b"a")
    Upload.objects.filter(pk=first.pk).update(status=Upload.Status.FAILED)
    second = receive_file(report, user, "b.xlsx", b"b")
    Upload.objects.filter(pk=second.pk).update(status=Upload.Status.REJECTED)
    third = receive_file(report, user, "c.xlsx", b"c")
    assert [first.number, second.number, third.number] == [1, 2, 3]
    assert receive_file(make_report(), user, "d.xlsx", b"d").number == 1


def test_an_upload_is_read_with_the_latest_ruleset_version(uploads_dir: Path) -> None:
    """Verify a file received after version 2 records version 2 (ENG-02 2)."""
    report = make_report()
    Ruleset.objects.create(
        report=report,
        version=2,
        settings={**DEFAULT_SETTINGS, "date_order": "month_first"},
        created_by=report.created_by,
    )
    upload = receive_file(report, make_user(), "a.xlsx", b"a")
    assert upload.ruleset.version == 2


def test_the_same_bytes_under_two_names_are_one_stored_file(
    uploads_dir: Path,
) -> None:
    """Verify Lesson 7: the same bytes, renamed and sent to another report,
    give the same hash, one stored file and two uploads with their own names.
    """
    user = make_user()
    january = receive_file(make_report(), user, "FAGLL03_Jan.xlsx", SAMPLE)
    renamed = receive_file(make_report(), user, "FAGLL03_January.xlsx", SAMPLE)
    assert january.file_sha256 == renamed.file_sha256 == SAMPLE_SHA256
    assert [path.name for path in uploads_dir.iterdir()] == [SAMPLE_SHA256]
    assert (january.original_name, renamed.original_name) == (
        "FAGLL03_Jan.xlsx",
        "FAGLL03_January.xlsx",
    )


def test_a_failure_inside_the_transaction_keeps_only_the_stored_file(
    uploads_dir: Path, monkeypatch
) -> None:
    """Verify that if the event can't be written, no upload and no event exist,
    and the stored file stays (ENG-01 5.1, step 2).
    """
    monkeypatch.setattr(audit, "record", fail)
    with pytest.raises(RuntimeError):
        receive_file(make_report(), make_user(), "a.xlsx", SAMPLE)
    assert not Upload.objects.exists()
    assert not AuditEvent.objects.exists()
    assert (uploads_dir / SAMPLE_SHA256).read_bytes() == SAMPLE


def test_receiving_is_refused_without_uploads_dir(settings) -> None:
    """Verify that with UPLOADS_DIR unset, receive_file stops before storing or
    writing anything (ENG-01 7.5).
    """
    settings.UPLOADS_DIR = ""
    with pytest.raises(ImproperlyConfigured, match="UPLOADS_DIR is not set"):
        receive_file(make_report(), make_user(), "a.xlsx", SAMPLE)
    assert not Upload.objects.exists()
    assert not AuditEvent.objects.exists()


def test_both_functions_work_as_ids_app(uploads_dir: Path) -> None:
    """Verify ids_app has every right the two functions need, including UPDATE
    on report, which locking its row requires (Lesson 6).
    """
    user = make_user()
    with as_app():
        report = create_report("IDS 2026", user)
        upload = receive_file(report, user, "FAGLL03_Jan.xlsx", SAMPLE)
    assert upload.number == 1
    assert AuditEvent.objects.count() == 2


@pytest.mark.django_db(transaction=True)
def test_a_second_file_waits_for_the_lock_and_takes_the_next_number(
    uploads_dir: Path,
) -> None:
    """Verify Lesson 6's table (ENG-01 5.1, step 2). Request A locks the
    report's row, makes upload 1 and holds the lock. A second file waits, then
    gets number 2 once A commits. Without the lock it would read the highest
    number as 0, and be refused for repeating A's number 1.

    Each thread has its own database connection, so this test commits for real
    and Django empties the tables after it.
    """
    report = make_report()
    locked, release = threading.Event(), threading.Event()
    received = {}

    def request_a() -> None:
        try:
            with transaction.atomic():
                Report.objects.select_for_update().get(pk=report.pk)
                # Closed, so the second file isn't refused for it (ENG-02 6).
                make_upload(report, status=Upload.Status.REJECTED)
                locked.set()
                release.wait(timeout=10)
        finally:
            connection.close()

    def request_b() -> None:
        try:
            received["upload"] = receive_file(report, report.created_by, "b.xlsx", b"b")
        except Exception as error:
            # Kept for the assert below, which shows what went wrong.
            received["error"] = error
        finally:
            connection.close()

    a = threading.Thread(target=request_a)
    a.start()
    assert locked.wait(timeout=10)
    b = threading.Thread(target=request_b)
    b.start()
    b.join(timeout=1)
    waited = b.is_alive()
    release.set()
    a.join(timeout=10)
    b.join(timeout=10)
    assert waited, "the second file didn't wait for request A"
    assert "error" not in received, received.get("error")
    assert received["upload"].number == 2
