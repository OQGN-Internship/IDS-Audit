"""Receive: the files refused before an upload exists, and the interrupted
reads cleared before a new file is checked (ENG-01 5.1 and 6.4, ENG-02 6 and
7.1).
"""

import datetime
import hashlib
import threading

import pytest
from django.db import connection, transaction
from django.utils import timezone

from ids_audit.audit.models import AuditEvent
from ids_audit.imports import services
from ids_audit.imports.models import Upload
from ids_audit.imports.services import UploadRefused, receive_file
from ids_audit.imports.states import INTERRUPTED
from ids_audit.imports.storage import uploads_folder
from ids_audit.reports.models import Report
from tests.factories import commit, make_report, make_upload
from tests.roles import as_app

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("uploads_dir")]

Status = Upload.Status

# A megabyte, as MAX_UPLOAD_MB counts them (ENG-01 6.4).
MEGABYTE = 1_048_576

# Any file of an accepted format: its first bytes make it text (ENG-02 4.1).
FILE = b"G/L Acct;Cost Ctr;Amount in LC\r\n"
FILE_SHA256 = hashlib.sha256(FILE).hexdigest()


def send(report: Report, data: bytes = FILE) -> Upload:
    """Send a file to the report as its creator."""
    return receive_file(report, report.created_by, "spend.csv", data)


def refused(report: Report, data: bytes = FILE) -> UploadRefused:
    """Send a file that must be refused, check that no upload was created and
    nothing was stored for it, and return the refusal.
    """
    uploads = set(Upload.objects.values_list("pk", flat=True))
    stored = sorted(uploads_folder().iterdir())
    with pytest.raises(UploadRefused) as caught:
        send(report, data)
    assert set(Upload.objects.values_list("pk", flat=True)) == uploads
    assert sorted(uploads_folder().iterdir()) == stored
    return caught.value


def refused_details(report: Report) -> dict:
    """Return the details of the one upload_refused event, after checking it
    names the user and the report, and no upload.
    """
    event = AuditEvent.objects.get(action=AuditEvent.Action.UPLOAD_REFUSED)
    assert (event.user, event.report, event.upload) == (
        report.created_by,
        report,
        None,
    )
    return event.details


def with_file(report: Report, status: str) -> Upload:
    """An upload of FILE in the report, at status. A committed one is decided
    at 21:00 UTC on 1 July 2026, which is already 2 July in Muscat.
    """
    upload = make_upload(report, status=status)
    if status == Status.COMMITTED:
        commit(upload, minute=12 * 60)
    Upload.objects.filter(pk=upload.pk).update(file_sha256=FILE_SHA256)
    return upload


def received_ago(upload: Upload, minutes: float) -> None:
    """Make the upload look received that many minutes ago."""
    when = timezone.now() - datetime.timedelta(minutes=minutes)
    Upload.objects.filter(pk=upload.pk).update(uploaded_at=when)


# The size limit (ENG-01 6.4)


def test_a_file_over_the_limit_is_refused_before_it_is_hashed(settings) -> None:
    """Verify a file one byte over MAX_UPLOAD_MB is refused, and its event has
    no hash, since the file was never hashed.
    """
    settings.MAX_UPLOAD_MB = 1
    report = make_report()
    refusal = refused(report, FILE + b" " * (MEGABYTE + 1 - len(FILE)))
    assert refusal.reason == UploadRefused.TOO_LARGE
    assert str(refusal) == "The file is larger than 1 MB, the most a file may be."
    assert refused_details(report) == {
        "reason": "too_large",
        "original_name": "spend.csv",
        "size_bytes": MEGABYTE + 1,
    }


def test_a_file_of_exactly_the_limit_is_received(settings) -> None:
    """Verify a megabyte is 1,048,576 bytes, and a file of exactly the limit
    is received.
    """
    settings.MAX_UPLOAD_MB = 1
    upload = send(make_report(), FILE + b" " * (MEGABYTE - len(FILE)))
    assert (upload.number, upload.size_bytes) == (1, MEGABYTE)


# The format (ENG-02 4.1)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        pytest.param(b"<html><table></table></html>", "HTML", id="html"),
        pytest.param(b"", "The file is empty.", id="empty"),
    ],
)
def test_a_file_of_unknown_format_is_refused(data: bytes, message: str) -> None:
    """Verify a file whose first bytes aren't an accepted format is refused,
    with the reason detect_format gives.
    """
    report = make_report()
    refusal = refused(report, data)
    assert refusal.reason == UploadRefused.UNKNOWN_FORMAT
    assert message in str(refusal)
    assert refused_details(report) == {
        "reason": "unknown_format",
        "original_name": "spend.csv",
        "size_bytes": len(data),
    }


def test_a_file_too_large_and_of_unknown_format_is_refused_as_too_large(
    settings,
) -> None:
    """Verify the size is checked before the format (ENG-02 6)."""
    settings.MAX_UPLOAD_MB = 1
    refusal = refused(make_report(), b"%PDF" + b" " * MEGABYTE)
    assert refusal.reason == UploadRefused.TOO_LARGE


# The same file twice (ENG-02 7.1)


def test_a_file_matching_a_committed_upload_is_refused() -> None:
    """Verify the message names the committed upload and the day it was
    committed, and the event keeps its number and the file's hash.
    """
    report = make_report()
    with_file(report, Status.COMMITTED)
    refusal = refused(report)
    assert refusal.reason == UploadRefused.ALREADY_COMMITTED
    assert str(refusal) == "This file was already committed on 02.07.2026 as upload 1."
    assert refused_details(report) == {
        "reason": "already_committed",
        "original_name": "spend.csv",
        "size_bytes": len(FILE),
        "file_sha256": FILE_SHA256,
        "number": 1,
    }


def test_a_file_committed_in_another_report_is_received() -> None:
    """Verify the same file can go into a different report."""
    with_file(make_report(), Status.COMMITTED)
    assert send(make_report()).number == 1


@pytest.mark.parametrize("status", [Status.ROLLED_BACK, Status.REJECTED, Status.FAILED])
def test_a_file_whose_upload_didn_t_stay_committed_is_received(status) -> None:
    """Verify a file whose upload was rolled back, rejected or failed can be
    uploaded again into the same report.
    """
    report = make_report()
    earlier = with_file(report, status)
    upload = send(report)
    assert (upload.number, upload.file_sha256) == (2, FILE_SHA256)
    earlier.refresh_from_db()
    assert earlier.status == status


# One open upload per report (ENG-02 6)


@pytest.mark.parametrize(
    "status", [Status.READING, Status.NEEDS_COLUMNS, Status.AWAITING_APPROVAL]
)
def test_a_file_for_a_report_with_an_open_upload_is_refused(status) -> None:
    """Verify a new file, even a different one, is refused while an upload is
    open, with a message naming it.
    """
    report = make_report()
    make_upload(report, status=status)
    refusal = refused(report)
    assert refusal.reason == UploadRefused.OPEN_UPLOAD
    label = Status(status).label.lower()
    assert str(refusal).startswith(f"Upload 1 is still open ({label}).")
    assert refused_details(report) == {
        "reason": "open_upload",
        "original_name": "spend.csv",
        "size_bytes": len(FILE),
        "file_sha256": FILE_SHA256,
        "number": 1,
    }


@pytest.mark.parametrize(
    "status",
    [Status.COMMITTED, Status.REJECTED, Status.FAILED, Status.ROLLED_BACK],
)
def test_a_closed_upload_doesn_t_block_a_new_file(status) -> None:
    """Verify only reading, needs_columns and awaiting_approval are open."""
    report = make_report()
    make_upload(report, status=status)
    assert send(report).number == 2


def test_an_open_upload_in_another_report_doesn_t_block_a_new_file() -> None:
    """Verify one open upload is allowed per report, not in all of them."""
    make_upload(make_report(), status=Status.AWAITING_APPROVAL)
    assert send(make_report()).number == 1


def test_a_committed_file_sent_during_an_open_upload_is_refused_as_committed() -> None:
    """Verify the committed file is checked before the open upload (ENG-02 6)."""
    report = make_report()
    with_file(report, Status.COMMITTED)
    make_upload(report, status=Status.AWAITING_APPROVAL)
    assert refused(report).reason == UploadRefused.ALREADY_COMMITTED


@pytest.mark.django_db(transaction=True)
def test_of_two_files_sent_at_once_one_is_received_and_one_refused(
    monkeypatch,
) -> None:
    """Verify the open-upload check runs with the report's row locked (ENG-01
    5.1, step 2). File A stops after it's stored, inside its transaction, with
    the lock held. File B waits for the lock, then finds A's upload open.
    Without the lock B would pass the check before A's upload exists.

    Each thread has its own database connection, so this test commits for real
    and Django empties the tables after it.
    """
    report = make_report()
    inside, release = threading.Event(), threading.Event()
    store_file = services.store_file

    def store_and_wait(file_sha256: str, data: bytes) -> None:
        store_file(file_sha256, data)
        if not inside.is_set():
            inside.set()
            release.wait(timeout=10)

    monkeypatch.setattr(services, "store_file", store_and_wait)
    results = {}

    def request(name: str, data: bytes) -> None:
        try:
            results[name] = receive_file(report, report.created_by, name, data)
        except Exception as error:
            results[name] = error
        finally:
            connection.close()

    a = threading.Thread(target=request, args=("a.csv", FILE))
    a.start()
    assert inside.wait(timeout=10)
    b = threading.Thread(target=request, args=("b.csv", FILE + b"\r\n"))
    b.start()
    b.join(timeout=1)
    waited = b.is_alive()
    release.set()
    a.join(timeout=10)
    b.join(timeout=10)
    assert waited, "file B didn't wait for file A's lock"
    assert isinstance(results["a.csv"], Upload), results["a.csv"]
    assert isinstance(results["b.csv"], UploadRefused), results["b.csv"]
    assert results["b.csv"].reason == UploadRefused.OPEN_UPLOAD


# Interrupted reads (ENG-02 6)


def test_a_read_interrupted_over_10_minutes_ago_fails_and_the_file_is_received() -> (
    None
):
    """Verify an upload left at reading more than 10 minutes after it was
    received is marked failed with no user, and no longer blocks the report.
    """
    report = make_report()
    stuck = make_upload(report)
    received_ago(stuck, 10.05)
    assert send(report).number == 2
    stuck.refresh_from_db()
    assert (stuck.status, stuck.error) == (Status.FAILED, INTERRUPTED)
    event = AuditEvent.objects.get(action=AuditEvent.Action.UPLOAD_FAILED)
    assert (event.user, event.report, event.upload, event.details) == (
        None,
        report,
        stuck,
        {"error": "Reading was interrupted"},
    )


def test_an_upload_received_under_10_minutes_ago_still_blocks() -> None:
    """Verify an upload received 9 minutes 57 seconds ago is left at reading,
    so the new file is refused.
    """
    report = make_report()
    reading = make_upload(report)
    received_ago(reading, 9.95)
    assert refused(report).reason == UploadRefused.OPEN_UPLOAD
    reading.refresh_from_db()
    assert reading.status == Status.READING
    assert not AuditEvent.objects.filter(action=AuditEvent.Action.UPLOAD_FAILED)


@pytest.mark.parametrize("status", [Status.NEEDS_COLUMNS, Status.AWAITING_APPROVAL])
def test_only_an_upload_at_reading_is_cleared(status) -> None:
    """Verify an old upload at needs_columns or awaiting_approval isn't an
    interrupted read, and still blocks the report.
    """
    report = make_report()
    received_ago(make_upload(report, status=status), 60)
    assert refused(report).reason == UploadRefused.OPEN_UPLOAD


def test_another_report_s_interrupted_read_is_left_alone() -> None:
    """Verify only the report the file is sent to is cleared."""
    other = make_upload()
    received_ago(other, 60)
    send(make_report())
    other.refresh_from_db()
    assert other.status == Status.READING


@pytest.mark.django_db(transaction=True)
def test_a_read_still_running_is_skipped_not_waited_for() -> None:
    """Verify an upload whose row another transaction holds locked, as a read
    in progress does, stays at reading however old it is, and the new file is
    refused at once rather than after the read.
    """
    report = make_report()
    reading = make_upload(report)
    received_ago(reading, 60)
    locked, release = threading.Event(), threading.Event()

    def read_in_progress() -> None:
        try:
            with transaction.atomic():
                Upload.objects.select_for_update().get(pk=reading.pk)
                locked.set()
                release.wait(timeout=10)
        finally:
            connection.close()

    results = {}

    def request() -> None:
        try:
            results["file"] = send(report)
        except Exception as error:
            results["file"] = error
        finally:
            connection.close()

    a = threading.Thread(target=read_in_progress)
    a.start()
    assert locked.wait(timeout=10)
    b = threading.Thread(target=request)
    b.start()
    b.join(timeout=5)
    answered = not b.is_alive()
    release.set()
    a.join(timeout=10)
    b.join(timeout=10)
    assert answered, "the new file waited for the read in progress"
    assert isinstance(results["file"], UploadRefused), results["file"]
    assert results["file"].reason == UploadRefused.OPEN_UPLOAD
    reading.refresh_from_db()
    assert reading.status == Status.READING


# As the application's role (ENG-01 6.3)


def test_refusing_and_clearing_work_as_ids_app() -> None:
    """Verify ids_app has every right a refusal and the sweep need: writing
    upload_refused, and locking and failing an interrupted read.
    """
    report = make_report()
    stuck = make_upload(report)
    received_ago(stuck, 60)
    with as_app():
        upload = send(report)
        with pytest.raises(UploadRefused):
            send(report, FILE + b"\r\n")
    stuck.refresh_from_db()
    assert (upload.number, stuck.status) == (2, Status.FAILED)
    assert AuditEvent.objects.filter(action="upload_refused").count() == 1
