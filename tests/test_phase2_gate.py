"""The Phase 2 gate (the plan, Phase 2, Done when), with the sample files:

1. Uploading the same file twice leaves the totals unchanged.
2. A file whose control total doesn't match can't be approved.
3. Rolling back an upload restores the earlier totals exactly.
4. Every one of these actions appears in the audit log, with the user who
   did it.

The totals are every figure manage.py report_totals prints (ENG-01 7.2), and
every step goes through the imports/ functions, as the screens will.
"""

import io
import json
from pathlib import Path

import pytest
from django.core.management import call_command

from ids_audit.audit.models import AuditEvent
from ids_audit.imports.commit import ApprovalRefused, commit_upload
from ids_audit.imports.decisions import reject_upload
from ids_audit.imports.models import Upload
from ids_audit.imports.rollback import roll_back_upload
from ids_audit.imports.services import UploadRefused, create_report, receive_file
from ids_audit.imports.stage import stage_upload
from ids_audit.reports.models import Report
from tests.factories import make_user
from tests.files import HEADER, line, total_row, xlsx_file

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("uploads_dir")]

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_XLSX = (FIXTURES_DIR / "sample.xlsx").read_bytes()
SAMPLE_CSV = (FIXTURES_DIR / "sample_semicolon.csv").read_bytes()
GL = ("G/L Account", "G/L Account Name", "Commitment Item", "Commitment Item Name")
# A file for July 2026, after the sample's year, that moves 610100 to the
# Professional services budget line.
JULY = xlsx_file(
    {
        "Spend": [
            HEADER,
            line(Pstng_Date="05.07.2026", Year="2026", Cost_Ctr="0000004510"),
            total_row("1000.000"),
        ],
        "GL": [GL, ("610100", "Software licences", "2401", "Professional services")],
    }
)


@pytest.fixture
def people() -> tuple:
    """An uploader and an approver, as two named users."""
    return make_user(), make_user()


def report_totals(report: Report) -> dict:
    """Return every figure report_totals prints for the report."""
    out = io.StringIO()
    call_command("report_totals", stdout=out)
    (entry,) = [
        e for e in json.loads(out.getvalue())["reports"] if e["report"] == report.pk
    ]
    return entry


def staged(report: Report, uploader, data: bytes, name: str) -> Upload:
    """Receive and stage a file as the uploader."""
    upload = receive_file(report, uploader, name, data)
    upload = stage_upload(upload, uploader)
    upload.refresh_from_db()
    return upload


def logged(report: Report) -> list[tuple[str, str | None]]:
    """Return the report's audit events in order, each action with the
    username of whoever did it.
    """
    return [
        (event.action, event.user.username if event.user else None)
        for event in AuditEvent.objects.filter(report=report).order_by("pk")
    ]


def test_1_the_same_file_twice_leaves_the_totals_unchanged(people) -> None:
    """Verify the sample sent again is refused and nothing moves; and its
    semicolon copy, the same lines in another file, replaces the workbook's
    postings and leaves every figure as it was.
    """
    uploader, approver = people
    report = create_report("IDS 2026", approver)
    commit_upload(staged(report, uploader, SAMPLE_XLSX, "sample.xlsx"), approver)
    before = report_totals(report)

    with pytest.raises(UploadRefused, match="already committed"):
        receive_file(report, uploader, "sample again.xlsx", SAMPLE_XLSX)
    assert report_totals(report) == before

    assert logged(report)[-1] == ("upload_refused", uploader.username)

    commit_upload(staged(report, uploader, SAMPLE_CSV, "sample.csv"), approver)
    assert report_totals(report) == before


def test_1_in_line_key_mode_the_same_lines_are_duplicates(people) -> None:
    """Verify in line-key mode the semicolon copy's lines are all duplicates,
    so approving it adds nothing and every figure stays.
    """
    uploader, approver = people
    report = create_report("IDS 2026", approver)
    first = staged(report, uploader, SAMPLE_XLSX, "sample.xlsx")
    assert first.preview["line_key_offered"] is True
    commit_upload(first, approver, duplicate_mode="line_key")
    before = report_totals(report)

    copy = staged(report, uploader, SAMPLE_CSV, "sample.csv")
    assert (copy.rows_new, copy.rows_duplicate) == (0, 35)
    assert copy.preview["amounts"]["to_commit"] == "0.000"
    assert (
        copy.preview["report_actual_spend"]["after"] == before["actual_spend"]["total"]
    )
    commit_upload(copy, approver)
    assert report_totals(report) == before


def test_2_a_file_whose_control_total_doesn_t_match_can_t_be_approved(
    people,
) -> None:
    """Verify the semicolon sample with its total 0.001 too high is blocked
    with a difference of 0.001, its approval is refused and on record with
    the approver, and the report doesn't change.
    """
    uploader, approver = people
    report = create_report("IDS 2026", approver)
    wrong = SAMPLE_CSV.replace(b"1,627,363.451;OMR", b"1,627,363.452;OMR")
    upload = staged(report, uploader, wrong, "sample.csv")
    assert str(upload.reconciliation_difference) == "0.001"
    before = report_totals(report)

    with pytest.raises(ApprovalRefused) as caught:
        commit_upload(upload, approver)
    assert caught.value.reasons == ["difference_not_zero"]
    upload.refresh_from_db()
    assert upload.status == Upload.Status.AWAITING_APPROVAL
    assert report_totals(report) == before
    assert logged(report)[-1] == ("approval_refused", approver.username)


def test_3_rolling_back_restores_the_earlier_totals_exactly(people) -> None:
    """Verify a July 2026 file that adds spend and moves 610100's budget line
    changes the figures, and rolling it back restores every one of them.
    """
    uploader, approver = people
    report = create_report("IDS 2026", approver)
    commit_upload(staged(report, uploader, SAMPLE_XLSX, "sample.xlsx"), approver)
    before = report_totals(report)

    july = staged(report, uploader, JULY, "july.xlsx")
    commit_upload(july, approver)
    after = report_totals(report)
    assert after["actual_spend"] != before["actual_spend"]
    assert after["by_ci"] != before["by_ci"]

    roll_back_upload(july, approver, "Sent before the period closed")
    assert report_totals(report) == before


def test_4_every_action_is_in_the_audit_log_with_its_user(people) -> None:
    """Verify the gate's whole story, in order, each event naming the user who
    did it: the sample received and approved, sent again and refused, a file
    that doesn't reconcile refused at approval and rejected, and a later file
    approved and rolled back.
    """
    uploader, approver = people
    report = create_report("IDS 2026", approver)
    commit_upload(staged(report, uploader, SAMPLE_XLSX, "sample.xlsx"), approver)
    with pytest.raises(UploadRefused):
        receive_file(report, uploader, "sample again.xlsx", SAMPLE_XLSX)
    wrong = SAMPLE_CSV.replace(b"1,627,363.451;OMR", b"1,627,363.452;OMR")
    blocked = staged(report, uploader, wrong, "sample.csv")
    with pytest.raises(ApprovalRefused):
        commit_upload(blocked, approver)
    reject_upload(blocked, approver, "Doesn't reconcile")
    july = staged(report, uploader, JULY, "july.xlsx")
    commit_upload(july, approver)
    roll_back_upload(july, approver, "Sent before the period closed")

    u, a = uploader.username, approver.username
    assert logged(report) == [
        ("report_created", a),
        ("upload_received", u),
        ("upload_awaiting_approval", u),
        ("upload_approved", a),
        ("upload_refused", u),
        ("upload_received", u),
        ("upload_awaiting_approval", u),
        ("approval_refused", a),
        ("upload_rejected", a),
        ("upload_received", u),
        ("upload_awaiting_approval", u),
        ("upload_approved", a),
        ("upload_rolled_back", a),
    ]
