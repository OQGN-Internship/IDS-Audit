"""Approval: what blocks it, a preview that's out of date, and the audit
event a refused approval leaves (ENG-02 9.3, 10.1 and 11.2, ENG-01 6.3).
"""

import pytest

from ids_audit.audit.models import AuditEvent
from ids_audit.imports.commit import ApprovalRefused, CommitCheckFailed, commit_upload
from ids_audit.imports.models import StagingRow, Upload
from ids_audit.imports.services import receive_file
from ids_audit.imports.stage import stage_upload
from ids_audit.imports.states import WrongState
from ids_audit.ledger.models import Posting
from ids_audit.pipeline.ruleset import DEFAULT_SETTINGS
from ids_audit.reports.models import Report, Ruleset
from tests.factories import commit, make_report, make_upload, make_user, roll_back
from tests.files import HEADER, LINE, csv_file, line, total_row, xlsx_file
from tests.roles import as_app

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("uploads_dir")]

Status = Upload.Status
GL = ("G/L Account", "G/L Account Name", "Commitment Item", "Commitment Item Name")
RULE = {"actual": ["Actual"], "not_actual": ["Commitment"]}


def staged(data: bytes, report: Report | None = None) -> Upload:
    """Receive and stage a file, in a new report unless given one."""
    report = report or make_report()
    upload = receive_file(report, report.created_by, "spend.csv", data)
    return stage_upload(upload, upload.uploaded_by)


def refused(upload: Upload, **chosen) -> ApprovalRefused:
    """Approve an upload that must be refused, as a new user, check nothing
    was committed, and return the refusal.
    """
    with pytest.raises(ApprovalRefused) as caught:
        commit_upload(upload, make_user(), **chosen)
    upload.refresh_from_db()
    assert upload.status == Status.AWAITING_APPROVAL
    assert upload.decided_by is None
    assert not Posting.objects.filter(upload=upload).exists()
    assert StagingRow.objects.filter(upload=upload).exists()
    assert not AuditEvent.objects.filter(action="upload_approved", upload=upload)
    return caught.value


def refusal_event(upload: Upload, refusal: ApprovalRefused) -> AuditEvent:
    """Return the one approval_refused event, after checking it names the
    upload and its report, and keeps the reasons and message.
    """
    event = AuditEvent.objects.get(action=AuditEvent.Action.APPROVAL_REFUSED)
    assert (event.report, event.upload) == (upload.report, upload)
    assert event.details == {"reasons": refusal.reasons, "message": str(refusal)}
    return event


def with_rule() -> Report:
    """A report whose latest ruleset has an actual-spend rule."""
    report = make_report()
    Ruleset.objects.create(
        report=report,
        version=2,
        settings={**DEFAULT_SETTINGS, "actual_spend_rule": RULE},
        created_by=report.created_by,
    )
    return report


# What blocks approval (ENG-02 9.3)


def blocked_by(kind: str) -> Upload:
    """Stage a file built so that the exception type blocks it."""
    if kind == "difference_not_zero":
        return staged(csv_file(LINE, total_row("999.999")))
    if kind == "no_control_total":
        return staged(csv_file(LINE))
    if kind == "another_currency":
        usd = line(DocumentNo="2", LCurr="USD")
        return staged(csv_file(LINE, usd, total_row("1000.000")))
    if kind == "unclassified_value_type":
        data = csv_file(
            (*LINE, "Plan"),
            (*total_row("1000.000"), ""),
            header=(*HEADER, "Value Type"),
        )
        return staged(data, with_rule())
    gl_sheet = [GL, ("610100", "", "2101", ""), ("610100", "", "2201", "")]
    spend = [HEADER, LINE, total_row("1000.000")]
    return staged(xlsx_file({"Spend": spend, "GL": gl_sheet}))


@pytest.mark.parametrize(
    "kind",
    [
        "difference_not_zero",
        "no_control_total",
        "another_currency",
        "unclassified_value_type",
        "listed_with_different_values",
    ],
)
def test_each_condition_in_9_3_blocks_approval_and_is_on_record(kind) -> None:
    """Verify a file built to cause each blocking type is refused with it as
    the reason, commits nothing, and leaves one approval_refused event naming
    the user who tried.
    """
    upload = blocked_by(kind)
    refusal = refused(upload)
    assert refusal.reasons == [kind]
    assert kind in str(refusal)
    event = refusal_event(upload, refusal)
    assert event.user.username != upload.uploaded_by.username


def test_every_blocking_type_is_a_reason_in_the_table_s_order() -> None:
    """Verify a file with no control total and a USD row gives both reasons."""
    usd = line(DocumentNo="2", LCurr="USD")
    refusal = refused(staged(csv_file(LINE, usd)))
    assert refusal.reasons == ["no_control_total", "another_currency"]


def test_an_exception_that_doesn_t_block_still_lets_the_file_through() -> None:
    """Verify a file whose only exceptions don't block is approved."""
    upload = staged(csv_file(LINE, total_row("1000.000")))
    assert not any(e["blocks_approval"] for e in upload.preview["exceptions"])
    assert commit_upload(upload, make_user()).status == Status.COMMITTED


# A preview that's out of date (ENG-02 10.1, step 3)


def test_an_upload_committed_since_the_preview_makes_it_out_of_date() -> None:
    """Verify an upload committed after the preview was built refuses the
    approval. The rules in ENG-02 6 prevent it, so the commit is made by hand.
    """
    report = make_report()
    upload = staged(csv_file(LINE, total_row("1000.000")), report)
    commit(make_upload(report), minute=0)
    refusal = refused(upload)
    assert refusal.reasons == ["preview_out_of_date"]
    refusal_event(upload, refusal)


def test_an_upload_rolled_back_since_the_preview_makes_it_out_of_date() -> None:
    """Verify rolling back the upload the preview was built against refuses
    the approval.
    """
    report = make_report()
    first = staged(csv_file(LINE, total_row("1000.000")), report)
    commit_upload(first, first.uploaded_by)
    upload = staged(csv_file(line(DocumentNo="2"), total_row("1000.000")), report)
    assert upload.preview["committed_uploads"] == [1]
    roll_back(Upload.objects.get(pk=first.pk))
    assert refused(upload).reasons == ["preview_out_of_date"]


def test_a_commit_in_another_report_leaves_the_preview_current() -> None:
    """Verify only the report's own commits matter."""
    upload = staged(csv_file(LINE, total_row("1000.000")))
    commit(make_upload(), minute=0)
    assert commit_upload(upload, make_user()).status == Status.COMMITTED


def test_the_preview_keeps_the_committed_uploads_in_the_order_of_commits() -> None:
    """Verify committed_uploads follows decided_at, not number, and leaves out
    uploads that aren't committed.
    """
    report = make_report()
    commit(make_upload(report), minute=5)
    commit(make_upload(report), minute=1)
    roll_back(commit(make_upload(report), minute=3))
    upload = staged(csv_file(LINE, total_row("1000.000")), report)
    assert upload.preview["committed_uploads"] == [2, 1]


# Every other refused approval (ENG-02 10.1)


def test_a_mode_that_can_t_be_chosen_is_on_record() -> None:
    """Verify choosing line-key mode for a file not offered it writes the
    event with the reason mode_not_allowed.
    """
    upload = staged(csv_file(line(DocumentNo=""), total_row("1000.000")))
    refusal = refused(upload, duplicate_mode="line_key")
    assert refusal.reasons == ["mode_not_allowed"]
    refusal_event(upload, refusal)


def test_postings_that_don_t_match_the_preview_are_on_record() -> None:
    """Verify a failed count and total check writes the event with the
    reason commit_check_failed.
    """
    upload = staged(csv_file(LINE, total_row("1000.000")))
    Upload.objects.filter(pk=upload.pk).update(rows_new=5)
    with pytest.raises(CommitCheckFailed) as caught:
        commit_upload(upload, make_user())
    assert caught.value.reasons == ["commit_check_failed"]
    refusal_event(upload, caught.value)
    assert not Posting.objects.exists()


def test_an_upload_not_awaiting_approval_leaves_no_refusal_event() -> None:
    """Verify approving an upload that's already committed is refused with
    WrongState, and there's nothing to record.
    """
    upload = staged(csv_file(LINE, total_row("1000.000")))
    commit_upload(upload, make_user())
    with pytest.raises(WrongState):
        commit_upload(upload, make_user())
    assert not AuditEvent.objects.filter(action="approval_refused").exists()


def test_a_refused_approval_is_on_record_as_ids_app() -> None:
    """Verify ids_app can write approval_refused, and the event survives the
    undone transaction.
    """
    upload = staged(csv_file(LINE))
    with as_app():
        with pytest.raises(ApprovalRefused):
            commit_upload(upload, make_user())
        events = AuditEvent.objects.filter(action="approval_refused").count()
    assert events == 1
