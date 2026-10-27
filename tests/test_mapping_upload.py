"""A mapping on its own (ENG-02 8.5, ENG-03 4): reading, approving and rolling
back a mapping upload, and its screens.
"""

import re
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from ids_audit.audit.models import AuditEvent
from ids_audit.imports.commit import ApprovalRefused, commit_upload
from ids_audit.imports.models import Upload
from ids_audit.imports.rollback import roll_back_upload
from ids_audit.imports.services import create_report, receive_file
from ids_audit.imports.stage import pick_columns, stage_upload
from ids_audit.ledger.models import PostingResolved
from ids_audit.reports.models import Report
from tests.factories import make_user
from tests.files import HEADER, LINE, csv_file, total_row, xlsx_file

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("uploads_dir")]

Status = Upload.Status
MAPPING = Upload.Kind.MAPPING
GL = ("G/L Account", "G/L Account Name", "Commitment Item", "Commitment Item Name")
CC = ("Cost Center", "Cost Center Name", "Department Code", "Department Name")
# LINE is 1,000.000 on GL account 610100 and cost center 4520.
GL_ROW = ("610100", "Software licences", "2101", "Software and licences")
CC_ROW = ("4520", "Enterprise applications", "APP", "Applications")
BOTH = xlsx_file(
    {
        "README": [("The mappings on their own. Every value is made up.",)],
        "GL Mapping": [
            GL,
            GL_ROW,
            ("610200", "Software support", "2101", "Software and licences"),
        ],
        "Cost Center Mapping": [CC, CC_ROW],
    }
)
ONE_LINE = csv_file(LINE, total_row("1000.000"))


@pytest.fixture
def admin():
    """A fixed administrator, who holds every permission on every report."""
    return make_user()


@pytest.fixture
def report(admin) -> Report:
    return create_report("Mapped", admin)


def staged(report: Report, user, data: bytes, kind=Upload.Kind.SPEND) -> Upload:
    """Receive and read a file, as the form of its kind would."""
    upload = stage_upload(
        receive_file(report, user, "file.xlsx", data, kind=kind), user
    )
    upload.refresh_from_db()
    return upload


def resolved(report: Report) -> list[tuple[str, str]]:
    """Each posting that counts, as its commitment item and department."""
    return list(
        PostingResolved.objects.filter(report=report)
        .order_by("pk")
        .values_list("ci_code", "dept_code")
    )


def test_a_mapping_upload_has_no_rows_and_nothing_to_reconcile(admin, report) -> None:
    """Verify a mapping upload with both mapping sheets reaches
    awaiting_approval with no rows, no control total, no exception, and its
    own two mappings; and once approved, a CSV uploaded after it takes its
    budget lines and departments (ENG-02 8.5).
    """
    upload = staged(report, admin, BOTH, MAPPING)
    assert upload.status == Status.AWAITING_APPROVAL, upload.error
    rows = (
        upload.rows_read,
        upload.rows_new,
        upload.rows_duplicate,
        upload.rows_skipped,
        upload.rows_replaced,
    )
    assert rows == (0, 0, 0, 0, 0)
    assert upload.control_total is None
    assert upload.reconciliation_difference is None
    assert (upload.period_start, upload.period_end) == (None, None)
    assert upload.preview["exceptions"] == []
    assert upload.preview["mappings"] == {"gl": 1, "cc": 1}
    assert upload.preview["months_replaced"] == []
    assert upload.preview["line_key_offered"] is False
    assert set(upload.preview["amounts"].values()) == {"0.000"}
    commit_upload(upload, admin)

    spend = staged(report, admin, ONE_LINE)
    assert spend.preview["mappings"] == {"gl": 1, "cc": 1}
    assert spend.preview["exceptions"] == []
    commit_upload(spend, admin)
    assert resolved(report) == [("2101", "APP")]


def test_a_mapping_upload_with_one_sheet_changes_only_that_mapping(
    admin, report
) -> None:
    """Verify a mapping upload with only the GL mapping, moving 610100 to
    another budget line, lists the change with the committed spend it moves,
    and its summary says so; leaves the cost center mapping in force; and is
    undone by its rollback, whose confirmation says its mapping stops being
    in force (ENG-02 8.5, ENG-03 4).
    """
    first = xlsx_file(
        {
            "Spend": [HEADER, LINE, total_row("1000.000")],
            "GL": [GL, GL_ROW],
            "CC": [CC, CC_ROW],
        }
    )
    commit_upload(staged(report, admin, first), admin)
    moved = ("610100", "Software licences", "2401", "Professional services")
    upload = staged(report, admin, xlsx_file({"GL Mapping": [GL, moved]}), MAPPING)
    assert upload.status == Status.AWAITING_APPROVAL, upload.error
    assert upload.preview["mappings"] == {"gl": 2, "cc": 1}
    (change,) = upload.preview["exceptions"]
    assert change["type"] == "mapping_change"
    assert change["items"] == [
        {
            "rows": [],
            "amount": "1000.000",
            "mapping": "gl",
            "code": "610100",
            "before": "2101",
            "after": "2401",
            "committed": "1000.000",
        }
    ]
    assert change["blocks_approval"] is False
    client = signed_in(admin)
    page = client.get(reverse("upload", args=[report.pk, 2])).content.decode()
    assert 'GL mapping</p><p class="figure">1</p>' in page
    assert 'Cost center mapping</p><p class="figure">None</p>' in page
    assert "Upload 1's stays in force." in page
    assert 'moves</p><p class="figure">1,000.000 <span class="unit">OMR' in page
    assert "by 1 changed code." in page

    commit_upload(upload, admin)
    assert resolved(report) == [("2401", "APP")]
    page = client.get(reverse("roll_back", args=[report.pk, 2])).content.decode()
    assert "Its mapping stops being in force" in page
    roll_back_upload(upload, admin, "Not yet")
    assert resolved(report) == [("2101", "APP")]


@pytest.mark.parametrize(
    ("data", "error"),
    [
        (
            xlsx_file({"Spend": [HEADER, LINE], "GL Mapping": [GL, GL_ROW]}),
            "The file has a spend sheet, Spend, so it isn't a mapping on its own.",
        ),
        (
            xlsx_file({"README": [("Notes only",)]}),
            "The file has no mapping sheet",
        ),
        (ONE_LINE, "A mapping is uploaded as an Excel file"),
    ],
    ids=["spend sheet", "no mapping sheet", "CSV"],
)
def test_a_mapping_upload_without_mapping_sheets_alone_fails(
    admin, report, data: bytes, error: str
) -> None:
    """Verify a mapping upload whose file has a spend sheet, one with no
    mapping sheet, and a CSV each end at failed, with their message, and
    stage nothing (ENG-02 8.5).
    """
    upload = staged(report, admin, data, MAPPING)
    assert upload.status == Status.FAILED
    assert upload.error.startswith(error), upload.error
    assert upload.preview is None


def test_a_mapping_upload_never_sets_the_duplicate_mode(admin, report) -> None:
    """Verify choosing a mode when approving a mapping upload is refused as
    mode_not_allowed and kept in the audit log; approving it leaves the
    report's mode unset; and the next spend upload is offered line key
    (ENG-02 7.3 and 8.5).
    """
    upload = staged(report, admin, BOTH, MAPPING)
    for mode in ("line_key", "replace_by_period"):
        with pytest.raises(ApprovalRefused) as refusal:
            commit_upload(upload, admin, mode)
        assert refusal.value.reasons == ["mode_not_allowed"]
    refused = AuditEvent.objects.filter(action="approval_refused", upload=upload)
    assert refused.count() == 2
    upload.refresh_from_db()
    assert upload.status == Status.AWAITING_APPROVAL

    commit_upload(upload, admin)
    report.refresh_from_db()
    assert not report.duplicate_mode
    approved = AuditEvent.objects.get(action="upload_approved", upload=upload)
    assert "duplicate_mode" not in approved.details

    spend = staged(report, admin, ONE_LINE)
    assert spend.preview["line_key_offered"] is True
    commit_upload(spend, admin, "line_key")
    report.refresh_from_db()
    assert report.duplicate_mode == Report.DuplicateMode.LINE_KEY


def test_a_mapping_upload_s_columns_are_picked_like_any_upload_s(admin, report) -> None:
    """Verify a mapping upload whose commitment item header isn't recognised
    stops at needs_columns, and reads once the column is picked (ENG-02 4.5
    and 8.5).
    """
    header = ("G/L Account", "G/L Account Name", "Budget Code", "Budget Title")
    data = xlsx_file({"GL Mapping": [header, GL_ROW]})
    upload = staged(report, admin, data, MAPPING)
    assert upload.status == Status.NEEDS_COLUMNS
    upload = pick_columns(upload, admin, {"GL Mapping": {"ci_code": "C"}})
    assert upload.status == Status.AWAITING_APPROVAL, upload.error
    assert upload.preview["mappings"] == {"gl": 1, "cc": None}


def test_a_mapping_upload_takes_no_control_total_and_keeps_its_kind(
    admin, report
) -> None:
    """Verify a mapping upload with a typed total is refused before anything
    is stored, and upload_received keeps each upload's kind (ENG-01 6.3,
    ENG-02 8.5).
    """
    with pytest.raises(ValueError, match="no control total"):
        receive_file(report, admin, "map.xlsx", BOTH, Decimal("1.000"), MAPPING)
    assert not report.uploads.exists()
    receive_file(report, admin, "map.xlsx", BOTH, kind=MAPPING)
    event = AuditEvent.objects.get(action="upload_received")
    assert (event.details["kind"], event.upload.kind) == ("mapping", MAPPING)


# --- The screens (ENG-03 4) --------------------------------------------------


def signed_in(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


def test_the_mapping_form_reads_a_mapping_upload_and_opens_it(admin, report) -> None:
    """Verify Upload a mapping is offered on Uploads, its form receives the
    file as a mapping upload and opens it, and Uploads then names its kind,
    Mapping (ENG-03 4).
    """
    client = signed_in(admin)
    uploads = client.get(reverse("uploads", args=[report.pk])).content.decode()
    form = reverse("new_mapping", args=[report.pk])
    assert f'href="{form}"' in uploads
    page = client.get(form).content.decode()
    assert 'name="file"' in page and "control_total" not in page

    sent = client.post(form, {"file": SimpleUploadedFile("map.xlsx", BOTH)})
    upload = report.uploads.get()
    assert sent.url == reverse("upload", args=[report.pk, upload.number])
    assert (upload.kind, upload.status) == (MAPPING, Status.AWAITING_APPROVAL)
    uploads = client.get(reverse("uploads", args=[report.pk])).content.decode()
    assert "<th>Kind</th>" in uploads
    assert re.search(r"<td>map\.xlsx</td>\s*<td>Mapping</td>", uploads)


def test_a_mapping_upload_s_page_shows_its_mappings_not_rows(admin, report) -> None:
    """Verify a mapping upload's summary shows the codes in each mapping, the
    spend it moves and its exception types, with no difference, rows or
    reconciliation, and its approval offers no duplicate mode though the
    report's isn't set (ENG-03 4).
    """
    upload = staged(report, admin, BOTH, MAPPING)
    page = (
        signed_in(admin)
        .get(reverse("upload", args=[report.pk, upload.number]))
        .content.decode()
    )
    assert 'GL mapping</p><p class="figure">2</p>' in page
    assert "GL accounts listed." in page
    assert 'Cost center mapping</p><p class="figure">1</p>' in page
    assert "Cost center listed." in page
    assert 'moves</p><p class="figure">0.000 <span class="unit">OMR' in page
    assert "No code changes budget line or department." in page
    assert "Exception types" in page
    for gone in (
        "Difference",
        "Rows to commit",
        "<h3>Reconciliation</h3>",
        "<h3>Rows</h3>",
    ):
        assert gone not in page, gone
    assert 'name="duplicate_mode"' not in page
    assert "Approve upload 1" in page
