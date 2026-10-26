"""The report list and the upload screens, through Django's test client
(ENG-03 4, ENG-01 6.2 and 6.4). The screens only call the imports/
functions, whose own tests cover what they decide.
"""

import datetime
import re
from decimal import Decimal
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from ids_audit.accounts import access
from ids_audit.accounts.models import User
from ids_audit.audit.models import AuditEvent
from ids_audit.imports.models import Upload
from ids_audit.imports.screens import EXCEPTION_NAMES
from ids_audit.pipeline.preview import ORDER
from ids_audit.pipeline.ruleset import DEFAULT_SETTINGS
from ids_audit.reports import queries
from ids_audit.reports.models import Report, Ruleset
from ids_audit.reports.templatetags.figures import amount, month
from tests.factories import grant, make_report, make_upload, make_user
from tests.files import HEADER, LINE, csv_file, line, total_row, xlsx_file

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("uploads_dir")]

Status = Upload.Status
SAMPLE_XLSX = (Path(__file__).parent / "fixtures" / "sample.xlsx").read_bytes()
# A file of one line that reconciles, and one with no total row.
ONE_LINE = csv_file(LINE, total_row("1000.000"))
NO_TOTAL = csv_file(LINE)
GL = ("G/L Account", "G/L Account Name", "Commitment Item", "Commitment Item Name")


def with_gl_mapping(date: str, *mapped: tuple[str, str]) -> bytes:
    """A workbook of one line of 1,000.000 posted on date, with a GL mapping
    sheet that maps each GL account to a commitment item.
    """
    return xlsx_file(
        {
            "Spend": [HEADER, line(Pstng_Date=date), total_row("1000.000")],
            "GL": [GL, *((gl, f"GL {gl}", ci, f"CI {ci}") for gl, ci in mapped)],
        }
    )


@pytest.fixture
def admin():
    """A fixed administrator, who can act on every report."""
    return make_user()


@pytest.fixture
def signed_in(admin) -> Client:
    """A browser signed in as the fixed administrator."""
    client = Client()
    client.force_login(admin)
    return client


@pytest.fixture
def report(admin) -> Report:
    return make_report()


def send(signed_in: Client, report: Report, data: bytes, name="spend.csv", total=""):
    """Send the upload form with a file and a typed total."""
    return signed_in.post(
        reverse("new_upload", args=[report.pk]),
        {"file": SimpleUploadedFile(name, data), "control_total": total},
    )


def uploaded(signed_in: Client, report: Report, data: bytes, **kwargs) -> Upload:
    """Upload a file from the form, check it opens the upload's page, open
    it, which shows its message, and return the upload.
    """
    response = send(signed_in, report, data, **kwargs)
    upload = report.uploads.order_by("-number").first()
    assert response["Location"] == reverse("upload", args=[report.pk, upload.number])
    signed_in.get(response["Location"])
    return upload


def page(signed_in: Client, name: str, *args, **kwargs) -> str:
    """Open a page, check it opened, and return its text."""
    response = signed_in.get(reverse(name, args=args), **kwargs)
    assert response.status_code == 200
    return response.content.decode()


def said(response) -> list[str]:
    """Return the messages the next page shows after a redirect. Each test
    opens a page after any earlier action, so these are this action's.
    """
    return [str(m) for m in response.wsgi_request._messages]


# The report list and creating a report


def test_a_fixed_admin_sees_every_report_and_can_create_one(signed_in) -> None:
    """Verify the report list shows every report and the New report link, and
    creating one opens its Uploads with its ruleset ready.
    """
    first = make_report()
    text = page(signed_in, "reports")
    assert first.name in text
    assert reverse("new_report") in text

    response = signed_in.post(reverse("new_report"), {"name": "  IDS 2026 "})
    created = Report.objects.get(name="IDS 2026")
    assert response["Location"] == reverse("uploads", args=[created.pk])
    assert created.rulesets.get().version == 1
    assert AuditEvent.objects.filter(action="report_created", report=created).exists()


def test_a_name_that_differs_only_in_capitals_is_refused(signed_in) -> None:
    """Verify a second report named like the first but for capitals is
    refused on the form, and nothing is created.
    """
    signed_in.post(reverse("new_report"), {"name": "IDS 2026"})
    response = signed_in.post(reverse("new_report"), {"name": "ids 2026"})
    assert response.status_code == 200
    assert "already exists" in response.content.decode()
    assert Report.objects.count() == 1


def test_a_user_sees_only_their_reports_and_can_t_create_one() -> None:
    """Verify a signed-in user who isn't a fixed administrator sees only the
    reports they have a role on, no New report link, and gets 403 from the
    address for creating one unless can_create_reports is on (ENG-01 6.2).
    """
    mine, theirs = make_report(), make_report()
    user = make_user(fixed_admin=False)
    client = Client()
    client.force_login(user)
    text = page(client, "reports")
    assert "There are no reports you can open." in text
    grant(user, mine, "Viewer")
    text = page(client, "reports")
    assert mine.name in text and theirs.name not in text
    assert reverse("new_report") not in text
    assert client.get(reverse("new_report")).status_code == 403
    assert client.post(reverse("new_report"), {"name": "Mine"}).status_code == 403
    assert not Report.objects.filter(name="Mine").exists()
    User.objects.filter(pk=user.pk).update(can_create_reports=True)
    assert reverse("new_report") in page(client, "reports")
    assert client.get(reverse("new_report")).status_code == 200


def test_home_and_a_report_s_own_address_open_the_right_pages(signed_in, report):
    """Verify signing in lands on the report list, and a report's address
    opens its overview (ENG-03 4).
    """
    assert signed_in.get(reverse("home"))["Location"] == reverse("reports")
    response = signed_in.get(reverse("report", args=[report.pk]))
    assert response["Location"] == reverse("overview", args=[report.pk])
    assert signed_in.get(reverse("report", args=[report.pk + 99])).status_code == 404


# Uploads and the upload form


def test_opening_uploads_marks_an_interrupted_read_failed(signed_in, report) -> None:
    """Verify Uploads marks failed an upload still at reading 11 minutes
    after it was received, and leaves one received 5 minutes ago.
    """
    old, recent = make_upload(report), make_upload(report)
    now = timezone.now()
    Upload.objects.filter(pk=old.pk).update(
        uploaded_at=now - datetime.timedelta(minutes=11)
    )
    Upload.objects.filter(pk=recent.pk).update(
        uploaded_at=now - datetime.timedelta(minutes=5)
    )
    text = page(signed_in, "uploads", report.pk)
    old.refresh_from_db()
    recent.refresh_from_db()
    assert (old.status, old.error) == (Status.FAILED, "Reading was interrupted")
    assert recent.status == Status.READING
    assert text.index("Upload 2") < text.index("Upload 1")


def test_uploading_reads_the_file_and_opens_its_preview(signed_in, report) -> None:
    """Verify the upload form receives and reads a file, and opens the upload
    awaiting approval, with the message saying so.
    """
    response = send(signed_in, report, SAMPLE_XLSX, name="sample.xlsx")
    upload = report.uploads.get()
    assert upload.status == Status.AWAITING_APPROVAL
    assert upload.original_name == "sample.xlsx"
    assert said(response) == ["Upload 1 is read and awaits approval."]


@pytest.mark.parametrize(
    ("typed", "saved"),
    [("1,000.000", "1000.000"), ("1,234.500-", "-1234.500"), ("OMR 12", "12.000")],
)
def test_a_typed_total_is_read_as_sap_writes_it(signed_in, report, typed, saved):
    """Verify a total typed on the form is read by the rule for amounts as
    text, and kept as entered (ENG-02 9.1).
    """
    upload = uploaded(signed_in, report, NO_TOTAL, total=typed)
    assert upload.control_total == Decimal(saved)
    assert upload.control_total_source == Upload.ControlTotalSource.ENTERED


def test_a_typed_total_is_read_with_the_report_s_decimal_separator(
    signed_in, report
) -> None:
    """Verify a typed total is read with the decimal separator of the ruleset
    the file will be read with.
    """
    Ruleset.objects.create(
        report=report,
        version=2,
        settings={**DEFAULT_SETTINGS, "decimal_separator": ","},
        created_by=report.created_by,
    )
    upload = uploaded(
        signed_in, report, csv_file(line(Amount_in_LC="1.000,000")), total="1.234,500-"
    )
    assert upload.control_total == Decimal("-1234.500")


@pytest.mark.parametrize(
    ("typed", "message"),
    [("abc", "Type it as SAP shows it"), ("12.3456", "at most 3 decimals")],
)
def test_a_typed_total_that_isnt_an_amount_is_refused_with_nothing_stored(
    signed_in, report, uploads_dir, typed, message
) -> None:
    """Verify a typed total that can't be read, or has more than 3 decimal
    places, is refused on the form, and no upload or file is kept.
    """
    response = send(signed_in, report, NO_TOTAL, total=typed)
    assert response.status_code == 200
    assert message in response.content.decode()
    assert not report.uploads.exists()
    assert list(uploads_dir.iterdir()) == []


def test_a_refused_file_shows_its_reason_on_the_form(signed_in, report) -> None:
    """Verify a file sent while the report has an open upload is refused on
    the form with the refusal's message, and written as upload_refused.
    """
    uploaded(signed_in, report, ONE_LINE)
    response = send(signed_in, report, NO_TOTAL)
    assert response.status_code == 200
    assert "Upload 1 is still open (awaiting approval)" in response.content.decode()
    assert report.uploads.count() == 1
    refused = AuditEvent.objects.get(action="upload_refused")
    assert refused.details["reason"] == "open_upload"


def test_an_unknown_format_is_refused_on_the_form(signed_in, report) -> None:
    """Verify an HTML file named .xlsx is refused on the form (ENG-01 6.4)."""
    response = send(signed_in, report, b"<html><body>x</body></html>", "spend.xlsx")
    assert response.status_code == 200
    assert not report.uploads.exists()
    assert AuditEvent.objects.get(action="upload_refused").details["reason"] == (
        "unknown_format"
    )


# The upload's summary, and the order of its page (ENG-03 4)

SUMMARY = re.compile(
    r'<div class="card tile( problem)?"><div class="card-body">'
    r'<p class="subheader label">(.*?)</p>'
    r'<p class="figure">(.*?)</p><p class="sub">(.*?)</p></div></div>'
)


def summary(text: str) -> list[tuple[bool, str, str, str]]:
    """Return the summary's tiles: whether each shows a problem, and its
    label, figure and detail, as words.
    """
    tiles = re.search(r'<div class="tiles summary">(.*?)\n  </div>', text, re.S)[1]
    return [
        (bool(problem), label, re.sub(r"<[^>]+>", "", figure), detail)
        for problem, label, figure, detail in SUMMARY.findall(tiles)
    ]


def test_the_summary_shows_the_preview_s_own_figures(signed_in, report) -> None:
    """Verify the summary's four tiles show the saved preview's difference,
    the report's actual spend after and before approval, the rows to commit
    and the exception types, none marked when nothing blocks approval.
    """
    upload = uploaded(signed_in, report, SAMPLE_XLSX, name="sample.xlsx")
    preview = upload.preview
    after = amount(preview["report_actual_spend"]["after"])
    before = amount(preview["report_actual_spend"]["before"])
    assert summary(page(signed_in, "upload", report.pk, upload.number)) == [
        (
            False,
            "Difference",
            "0.000 OMR",
            "The rows add up to the control total, "
            f"{amount(upload.control_total)} OMR.",
        ),
        (
            False,
            "Actual spend after approval",
            f"{after} OMR",
            f"The report's, now {before} OMR.",
        ),
        (
            False,
            "Rows to commit",
            str(upload.rows_new),
            f"Of {upload.rows_read} read: {upload.rows_duplicate} already in the "
            f"report, {upload.rows_skipped} skipped.",
        ),
        (
            False,
            "Exception types",
            str(len(preview["exceptions"])),
            "None blocks approval.",
        ),
    ]


def test_the_summary_marks_what_blocks_approval(signed_in, report) -> None:
    """Verify a file with no control total marks its Difference and its
    exception types as blocking approval, in words.
    """
    upload = uploaded(signed_in, report, NO_TOTAL)
    tiles = summary(page(signed_in, "upload", report.pk, upload.number))
    assert tiles[0] == (
        True,
        "Difference",
        "No control total",
        "Blocks approval: the file has no total row, and none was typed with it.",
    )
    assert tiles[3][0] is True
    assert tiles[3][3] == "Block approval: No control total."


def test_the_rows_tile_names_duplicates_and_skipped_rows_apart(
    signed_in, report
) -> None:
    """Verify the rows tile says how many rows are already in the report and
    how many were skipped, each from its own figure.
    """
    upload = uploaded(signed_in, report, SAMPLE_XLSX, name="sample.xlsx")
    Upload.objects.filter(pk=upload.pk).update(rows_duplicate=2, rows_skipped=5)
    tiles = summary(page(signed_in, "upload", report.pk, upload.number))
    assert tiles[2][3] == (
        f"Of {upload.rows_read} read: 2 already in the report, 5 skipped."
    )


def test_the_summary_marks_a_difference_that_isn_t_zero(signed_in, report) -> None:
    """Verify a typed total the rows don't add up to shows its difference,
    marked as blocking approval.
    """
    upload = uploaded(signed_in, report, NO_TOTAL, total="1,250.000")
    tiles = summary(page(signed_in, "upload", report.pk, upload.number))
    assert tiles[0] == (
        True,
        "Difference",
        "250.000 OMR",
        "Blocks approval: the rows don't add up to the control total, 1,250.000 OMR.",
    )
    assert tiles[3] == (
        True,
        "Exception types",
        str(len(upload.preview["exceptions"])),
        "Block approval: Difference not zero.",
    )


def test_the_page_reads_summary_preview_file_then_actions(signed_in, report) -> None:
    """Verify an upload's page comes in reading order, with a link from the
    summary to the actions for a user who may take one, and none for a
    Viewer of a committed upload, who may take none.
    """
    upload = uploaded(signed_in, report, SAMPLE_XLSX, name="sample.xlsx")
    text = page(signed_in, "upload", report.pk, upload.number)
    order = [
        "<h2>Summary</h2>",
        '<a href="#actions">',
        "<h2>Preview</h2>",
        "<h2>File</h2>",
        '<h2 id="actions">Actions</h2>',
        "Approve upload 1</button>",
    ]
    assert [text.index(mark) for mark in order] == sorted(
        text.index(mark) for mark in order
    )
    signed_in.post(
        reverse("approve", args=[report.pk, 1]),
        {"duplicate_mode": "replace_by_period"},
    )
    viewer = make_user(fixed_admin=False)
    grant(viewer, report, "Viewer")
    browser = Client()
    browser.force_login(viewer)
    text = page(browser, "upload", report.pk, upload.number)
    assert "<h2>Summary</h2>" in text
    assert "#actions" not in text and 'id="actions"' not in text


# The preview


def test_the_preview_shows_every_figure_it_saved(signed_in, report) -> None:
    """Verify the upload page shows every amount and row figure in the
    sample's preview, and each exception type with its amount.
    """
    upload = uploaded(signed_in, report, SAMPLE_XLSX, name="sample.xlsx")
    text = page(signed_in, "upload", report.pk, upload.number)
    preview = upload.preview
    amounts = [
        *preview["amounts"].values(),
        *preview["actual_spend"].values(),
        preview["not_actual"],
        *preview["report_actual_spend"].values(),
        str(upload.control_total),
        str(upload.reconciliation_difference),
    ]
    for value in amounts:
        assert amount(value) in text, value
    for figure in ("rows_read", "rows_new", "rows_skipped"):
        assert f">{getattr(upload, figure)}<" in text
    assert preview["exceptions"]
    for exception in preview["exceptions"]:
        assert EXCEPTION_NAMES[exception["type"]] in text
        if exception["amount"] is not None:
            assert amount(exception["amount"]) in text
    assert "This upload's own" in text
    assert f"{upload.period_start:%d.%m.%Y}" in text
    assert preview["months_replaced"]
    for kept in preview["months_replaced"]:
        assert month(kept) in text
    uploaded_at = timezone.localtime(upload.uploaded_at)
    assert f"{uploaded_at:%d.%m.%Y %H:%M}" in text


def test_a_mapping_change_s_items_show_both_values_and_what_moves(signed_in, report):
    """Verify a second file that moves 610100 to another commitment item, and
    lists a GL account the first didn't, shows each change with its values
    before and after, the actual spend it moves and the part already
    committed.
    """
    first = uploaded(
        signed_in, report, with_gl_mapping("08.07.2025", ("610100", "2101"))
    )
    signed_in.post(reverse("approve", args=[report.pk, first.number]))
    second = uploaded(
        signed_in,
        report,
        with_gl_mapping("08.08.2025", ("610100", "2401"), ("620100", "2101")),
    )
    args = (report.pk, second.number, "mapping_change")
    panel = page(signed_in, "exception_items", *args, headers={"HX-Request": "true"})
    rows = re.findall(r"<tr>(.*?)</tr>", panel, re.S)
    cells = [re.findall(r"<td[^>]*>(.*?)</td>", row, re.S) for row in rows[1:]]
    assert [c.strip() for c in cells[0]] == [
        "610100",
        "GL",
        "2101",
        "2401",
        "1,000.000",
        "Spend: row 2",
        "2,000.000",
    ]
    assert [c.strip() for c in cells[1]][:4] == ["620100", "GL", "not listed", "2101"]
    text = page(signed_in, "upload", report.pk, second.number)
    assert "This upload's own" in text and "None yet" in text


def test_value_types_and_the_mapping_in_force_are_shown(signed_in, report) -> None:
    """Verify a file with a value-type column lists each value type with what
    it counts as, and a file with no GL mapping names the upload whose
    mapping its postings will use.
    """
    first = uploaded(
        signed_in, report, with_gl_mapping("08.07.2025", ("610100", "2101"))
    )
    signed_in.post(reverse("approve", args=[report.pk, first.number]))
    data = csv_file(
        (*line(Pstng_Date="08.08.2025"), "Actual"),
        (*total_row("1000.000"), ""),
        header=(*HEADER, "Value Type"),
    )
    second = uploaded(signed_in, report, data)
    text = page(signed_in, "upload", report.pk, second.number)
    assert "<td>Actual</td><td>Actual spend</td>" in text
    assert "Upload 1's" in text


def test_every_exception_type_has_a_name() -> None:
    """Verify each type in ENG-02 11.2's order has its name for the screen."""
    assert list(EXCEPTION_NAMES) == list(ORDER)


def test_an_exception_s_items_open_as_a_panel_or_a_page(signed_in, report) -> None:
    """Verify an exception type's items open as a panel for an HTMX request
    and as a page of their own otherwise, each listing its codes and rows,
    and a type the preview doesn't have is 404.
    """
    upload = uploaded(signed_in, report, SAMPLE_XLSX, name="sample.xlsx")
    args = (report.pk, upload.number, "outside_ids")
    panel = page(signed_in, "exception_items", *args, headers={"HX-Request": "true"})
    assert "<html" not in panel
    for code in ("3100", "3200"):
        assert f"<td>{code}</td>" in panel
    assert "Transactions: rows" in panel
    full = page(signed_in, "exception_items", *args)
    assert "<html" in full and "Cost center outside IDS" in full
    missing = (report.pk, upload.number, "names_differ")
    assert signed_in.get(reverse("exception_items", args=missing)).status_code == 404


def test_a_preview_needs_upload_or_approve_until_committed(monkeypatch) -> None:
    """Verify a user with view only gets 403 on an upload awaiting approval
    and its items, and opens one that was committed (ENG-03 4).
    """
    report = make_report()
    waiting = make_upload(report, status=Status.AWAITING_APPROVAL)
    done = make_upload(report, status=Status.COMMITTED)
    viewer = make_user(fixed_admin=False)
    monkeypatch.setattr(access, "held", lambda user, report: frozenset({"view"}))
    client = Client()
    client.force_login(viewer)
    assert client.get(reverse("upload", args=[report.pk, 1])).status_code == 403
    items = reverse("exception_items", args=[report.pk, waiting.number, "skipped_row"])
    assert client.get(items).status_code == 403
    assert client.get(reverse("upload", args=[report.pk, done.number])).status_code == (
        200
    )


# The actions


def test_approving_from_the_screen_commits_the_upload(signed_in, report, admin):
    """Verify the Approve form commits an upload, which then counts in the
    report, and says so.
    """
    upload = uploaded(signed_in, report, ONE_LINE)
    response = signed_in.post(reverse("approve", args=[report.pk, upload.number]))
    upload.refresh_from_db()
    assert (upload.status, upload.decided_by) == (Status.COMMITTED, admin)
    assert queries.totals(report).total == Decimal("1000.000")
    assert said(response) == ["Upload 1 is approved, and counts in the report."]
    report.refresh_from_db()
    assert report.duplicate_mode == Report.DuplicateMode.REPLACE_BY_PERIOD


def test_the_first_approval_offers_line_key_mode_when_the_preview_does(
    signed_in, report
) -> None:
    """Verify the first upload's Approve form offers line-key mode, which the
    approval then sets, and a later upload's form offers no mode.
    """
    upload = uploaded(signed_in, report, ONE_LINE)
    text = page(signed_in, "upload", report.pk, upload.number)
    assert 'value="line_key">' in text
    signed_in.post(
        reverse("approve", args=[report.pk, upload.number]),
        {"duplicate_mode": "line_key"},
    )
    report.refresh_from_db()
    assert report.duplicate_mode == Report.DuplicateMode.LINE_KEY

    second = uploaded(
        signed_in, report, csv_file(line(DocumentNo="9"), total_row("1000"))
    )
    assert 'name="duplicate_mode"' not in page(signed_in, "upload", report.pk, 2)
    assert second.status == Status.AWAITING_APPROVAL


def test_line_key_mode_isnt_offered_for_a_file_without_line_keys(signed_in, report):
    """Verify the line-key choice is shown disabled, with why, for a file
    whose rows have no full line key.
    """
    header = tuple(h for h in HEADER if h != "Line Item")
    row = tuple(c for h, c in zip(HEADER, LINE, strict=True) if h != "Line Item")
    total = tuple(
        c
        for h, c in zip(HEADER, total_row("1000.000"), strict=True)
        if h != "Line Item"
    )
    upload = uploaded(signed_in, report, csv_file(row, total, header=header))
    text = page(signed_in, "upload", report.pk, upload.number)
    assert 'value="line_key" disabled>' in text
    assert "Line key isn't offered" in text


def test_a_blocked_approval_shows_why_and_is_on_record(signed_in, report) -> None:
    """Verify a file with no control total shows what blocks it, and the
    approval is refused with its message, written as approval_refused.
    """
    upload = uploaded(signed_in, report, NO_TOTAL)
    assert "Approval is blocked by: No control total" in page(
        signed_in, "upload", report.pk, upload.number
    )
    response = signed_in.post(reverse("approve", args=[report.pk, upload.number]))
    upload.refresh_from_db()
    assert upload.status == Status.AWAITING_APPROVAL
    (message,) = said(response)
    assert "no_control_total" in message and message.endswith("Nothing was changed.")
    assert AuditEvent.objects.filter(action="approval_refused").count() == 1


@pytest.mark.parametrize(
    ("action", "event"),
    [("reject", "upload_rejected"), ("withdraw", "upload_withdrawn")],
)
def test_rejecting_and_withdrawing_need_a_reason(signed_in, report, action, event):
    """Verify rejecting or withdrawing without a reason is refused and changes
    nothing, and with one closes the upload and keeps the reason.
    """
    upload = uploaded(signed_in, report, ONE_LINE)
    url = reverse(action, args=[report.pk, upload.number])
    response = signed_in.post(url, {"reason": "   "})
    assert said(response) == ["A reason is needed. Nothing was changed."]
    upload.refresh_from_db()
    assert upload.status == Status.AWAITING_APPROVAL

    signed_in.post(url, {"reason": "Wrong month"})
    upload.refresh_from_db()
    assert upload.status == Status.REJECTED
    assert AuditEvent.objects.get(action=event).details == {"reason": "Wrong month"}
    assert "Reason: Wrong month" in page(signed_in, "upload", report.pk, upload.number)


def test_an_action_on_an_upload_already_decided_is_refused(signed_in, report) -> None:
    """Verify approving an upload already rejected shows the refusal and
    changes nothing.
    """
    upload = uploaded(signed_in, report, ONE_LINE)
    signed_in.post(reverse("reject", args=[report.pk, 1]), {"reason": "No"})
    page(signed_in, "upload", report.pk, 1)
    response = signed_in.post(reverse("approve", args=[report.pk, 1]))
    assert said(response) == [
        "Upload 1 is rejected, not awaiting approval. Nothing was changed."
    ]
    upload.refresh_from_db()
    assert upload.status == Status.REJECTED


def test_actions_are_forms_sent_with_post(signed_in, report) -> None:
    """Verify each action's address refuses a GET."""
    uploaded(signed_in, report, ONE_LINE)
    for action in ("approve", "reject", "withdraw"):
        assert signed_in.get(reverse(action, args=[report.pk, 1])).status_code == 405


def test_each_action_shows_only_for_a_user_who_may_take_it(monkeypatch) -> None:
    """Verify an uploader sees only Withdraw, an approver Approve and Reject,
    and Roll back only with roll_back, and an action's address refuses
    whoever its form isn't shown to.
    """
    report = make_report()
    uploader, approver = make_user(fixed_admin=False), make_user(fixed_admin=False)
    waiting = make_upload(report, status=Status.AWAITING_APPROVAL)
    Upload.objects.filter(pk=waiting.pk).update(uploaded_by=uploader)
    roles = {uploader.pk: {"view", "upload"}, approver.pk: {"view", "approve"}}
    monkeypatch.setattr(
        access, "held", lambda user, report: frozenset(roles.get(user.pk, ()))
    )
    client = Client()

    client.force_login(uploader)
    text = page(client, "upload", report.pk, waiting.number)
    assert "Withdraw upload 1" in text
    assert "Approve upload 1" not in text and "Reject upload 1" not in text
    assert client.post(reverse("approve", args=[report.pk, 1])).status_code == 403

    client.force_login(approver)
    text = page(client, "upload", report.pk, waiting.number)
    assert "Approve upload 1" in text and "Reject upload 1" in text
    assert "Withdraw upload 1" not in text

    done = make_upload(report, status=Status.COMMITTED)
    assert "Roll back upload 2" not in page(client, "upload", report.pk, done.number)
    assert client.get(reverse("roll_back", args=[report.pk, 2])).status_code == 403
    roles[approver.pk].add("roll_back")
    assert "Roll back upload 2" in page(client, "upload", report.pk, done.number)


# Picking columns

NO_AMOUNT_HEADER = tuple("Value" if h == "Amount in LC" else h for h in HEADER)


def test_picking_a_column_reads_the_file_again(signed_in, report) -> None:
    """Verify an upload missing its amount column shows the problem, the
    sheet's header and first rows under their letters, and reads to awaiting
    approval once the column is picked.
    """
    data = csv_file(LINE, total_row("1000.000"), header=NO_AMOUNT_HEADER)
    upload = uploaded(signed_in, report, data)
    assert upload.status == Status.NEEDS_COLUMNS
    assert "no column matches amount" in page(signed_in, "upload", report.pk, 1)

    text = page(signed_in, "columns", report.pk, 1)
    assert "<th>I</th>" in text and "<td>Value</td>" in text
    assert "<td>1,000.000</td>" in text
    assert '<option value="I">I: Value</option>' in text

    response = signed_in.post(reverse("columns", args=[report.pk, 1]), {"pick-0": "I"})
    upload.refresh_from_db()
    assert upload.status == Status.AWAITING_APPROVAL
    assert said(response) == ["Upload 1 is read and awaits approval."]


def test_picking_no_column_is_refused(signed_in, report) -> None:
    """Verify sending the form with no column picked is refused and leaves the
    upload at needs_columns.
    """
    data = csv_file(LINE, total_row("1000.000"), header=NO_AMOUNT_HEADER)
    upload = uploaded(signed_in, report, data)
    response = signed_in.post(reverse("columns", args=[report.pk, 1]), {"pick-0": ""})
    assert said(response) == [
        "Pick a column for at least one field. Nothing was changed."
    ]
    upload.refresh_from_db()
    assert upload.status == Status.NEEDS_COLUMNS


def test_only_the_uploader_picks_columns_and_withdraws(monkeypatch) -> None:
    """Verify another user holding upload gets 403 from picking columns and
    withdrawing, and the uploader's page offers both (ENG-01 6.2, rule 6).
    """
    report = make_report()
    uploader, other = make_user(fixed_admin=False), make_user(fixed_admin=False)
    upload = make_upload(report, status=Status.NEEDS_COLUMNS)
    Upload.objects.filter(pk=upload.pk).update(uploaded_by=uploader)
    monkeypatch.setattr(
        access, "held", lambda user, report: frozenset({"view", "upload"})
    )
    client = Client()
    client.force_login(other)
    assert client.get(reverse("columns", args=[report.pk, 1])).status_code == 403
    response = client.post(reverse("withdraw", args=[report.pk, 1]), {"reason": "x"})
    assert response.status_code == 403
    assert "Withdraw upload 1" not in page(client, "upload", report.pk, 1)

    client.force_login(uploader)
    text = page(client, "upload", report.pk, 1)
    assert "Withdraw upload 1" in text and "Pick the columns" in text
    assert "Approve upload 1" not in text and "Reject upload 1" not in text


# Rolling back


def commit_from_screen(signed_in: Client, report: Report, data: bytes) -> Upload:
    """Upload a file and approve it from the screens."""
    upload = uploaded(signed_in, report, data)
    signed_in.post(reverse("approve", args=[report.pk, upload.number]))
    page(signed_in, "upload", report.pk, upload.number)
    upload.refresh_from_db()
    assert upload.status == Status.COMMITTED
    return upload


def test_the_rollback_confirmation_shows_the_figure_the_rollback_gives(
    signed_in, report
) -> None:
    """Verify the confirmation shows the report's actual spend now and once
    rolled back, and the rollback then gives exactly that figure.
    """
    commit_from_screen(signed_in, report, ONE_LINE)
    second = commit_from_screen(
        signed_in,
        report,
        csv_file(
            line(Pstng_Date="05.08.2025", Amount_in_LC="250.500"), total_row("250.500")
        ),
    )
    text = page(signed_in, "roll_back", report.pk, second.number)
    assert "Actual spend: 1,250.500 OMR → 1,000.000 OMR." in text

    response = signed_in.post(
        reverse("roll_back", args=[report.pk, second.number]), {"reason": "Early"}
    )
    second.refresh_from_db()
    assert second.status == Status.ROLLED_BACK
    assert queries.totals(report).total == Decimal("1000.000")
    assert said(response)[0].startswith("Upload 2 is rolled back.")


def test_a_rollback_that_cant_happen_says_why_before_asking(signed_in, report) -> None:
    """Verify the confirmation for an upload that isn't the latest shows the
    refusal and no form, and sending the form anyway changes nothing.
    """
    first = commit_from_screen(signed_in, report, ONE_LINE)
    commit_from_screen(
        signed_in,
        report,
        csv_file(line(Pstng_Date="05.08.2025"), total_row("1000.000")),
    )
    text = page(signed_in, "roll_back", report.pk, first.number)
    assert "Only upload 2, the most recently approved, can be rolled back." in text
    assert 'name="reason"' not in text
    response = signed_in.post(
        reverse("roll_back", args=[report.pk, first.number]), {"reason": "x"}
    )
    assert said(response)[0].endswith("Nothing was changed.")
    first.refresh_from_db()
    assert first.status == Status.COMMITTED


def test_a_rollback_without_a_reason_is_refused(signed_in, report) -> None:
    """Verify the rollback form without a reason changes nothing and opens the
    confirmation again.
    """
    upload = commit_from_screen(signed_in, report, ONE_LINE)
    url = reverse("roll_back", args=[report.pk, upload.number])
    response = signed_in.post(url, {"reason": ""})
    assert response["Location"] == url
    upload.refresh_from_db()
    assert upload.status == Status.COMMITTED


# Access and safety on every address


def addresses(report: Report) -> list[tuple[str, str]]:
    """Every address under a report, with the method its form uses."""
    base = [report.pk]
    one = [report.pk, 1]
    return [
        ("get", reverse("report", args=base)),
        ("get", reverse("overview", args=base)),
        ("get", reverse("gl_accounts", args=base)),
        ("get", reverse("gl_account", args=[*base, "610100"])),
        ("get", reverse("departments", args=base)),
        ("get", reverse("department", args=[*base, "outside"])),
        ("get", reverse("budget_lines", args=base)),
        ("get", reverse("budget_line", args=[*base, "none"])),
        ("get", reverse("rows", args=base)),
        ("get", reverse("exceptions", args=base)),
        ("get", reverse("faq", args=base)),
        ("get", reverse("faq_page", args=[*base, "total-spend"])),
        ("get", reverse("overview_export", args=base)),
        ("get", reverse("gl_accounts_export", args=base)),
        ("get", reverse("gl_account_export", args=[*base, "610100"])),
        ("get", reverse("departments_export", args=base)),
        ("get", reverse("department_export", args=[*base, "outside"])),
        ("get", reverse("budget_lines_export", args=base)),
        ("get", reverse("budget_line_export", args=[*base, "none"])),
        ("get", reverse("rows_export", args=base)),
        ("get", reverse("exceptions_export", args=base)),
        ("get", reverse("faq_page_export", args=[*base, "total-spend"])),
        ("get", reverse("uploads", args=base)),
        ("get", reverse("new_upload", args=base)),
        ("get", reverse("upload", args=one)),
        ("get", reverse("exception_items", args=[*one, "skipped_row"])),
        ("post", reverse("approve", args=one)),
        ("post", reverse("reject", args=one)),
        ("post", reverse("withdraw", args=one)),
        ("get", reverse("columns", args=one)),
        ("get", reverse("roll_back", args=one)),
    ]


def test_every_report_address_is_404_for_a_user_with_no_access() -> None:
    """Verify a signed-in user who isn't a fixed administrator gets 404 from
    every address under a report, for one that exists and one that doesn't.
    """
    report = make_report()
    make_upload(report, status=Status.AWAITING_APPROVAL)
    client = Client()
    client.force_login(make_user(fixed_admin=False))
    for method, url in addresses(report):
        assert getattr(client, method)(url).status_code == 404, url
    gone = Report(pk=report.pk + 99)
    for method, url in addresses(gone):
        assert getattr(client, method)(url).status_code == 404, url


def test_a_form_needs_its_csrf_token_and_htmx_sends_it_in_a_header(
    report,
) -> None:
    """Verify a POST without the CSRF token is refused, and an HTMX request
    carrying the token from the page's hx-headers succeeds (ENG-01 6.4).
    """
    upload = make_upload(report, status=Status.AWAITING_APPROVAL)
    client = Client(enforce_csrf_checks=True)
    client.force_login(make_user())
    url = reverse("reject", args=[report.pk, upload.number])
    assert client.post(url, {"reason": "x"}).status_code == 403
    text = page(client, "uploads", report.pk)
    (token,) = re.findall(r"hx-headers='\{\"X-CSRFToken\": \"([^\"]+)\"\}'", text)
    response = client.post(
        url, {"reason": "x"}, headers={"HX-Request": "true", "X-CSRFToken": token}
    )
    assert response.status_code == 302
    upload.refresh_from_db()
    assert upload.status == Status.REJECTED


def test_a_name_with_a_script_shows_as_text(signed_in) -> None:
    """Verify a report named with a script tag is shown as plain text on the
    report list and its Uploads (ENG-01 6.4).
    """
    signed_in.post(reverse("new_report"), {"name": "<script>alert(1)</script>"})
    report = Report.objects.get()
    for text in (page(signed_in, "reports"), page(signed_in, "uploads", report.pk)):
        assert "<script>alert(1)</script>" not in text
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text


def test_no_page_is_kept_in_the_browser(signed_in, report) -> None:
    """Verify a report page, its panel and the sign-in page each say
    no-store, the base template turns HTMX's history cache off, and signing
    out asks the browser to clear what it stored (ENG-01 6.4).
    """
    overview = reverse("overview", args=[report.pk])
    for response in (
        signed_in.get(overview),
        signed_in.get(overview, headers={"HX-Request": "true"}),
        Client().get(reverse("sign_in")),
    ):
        assert "no-store" in response["Cache-Control"]
    meta = """<meta name="htmx-config" content='{"historyCacheSize": 0}'>"""
    assert meta in page(signed_in, "reports")
    response = signed_in.post(reverse("sign_out"))
    assert response["Clear-Site-Data"] == '"cache", "storage"'


def test_every_page_refuses_to_be_framed(signed_in) -> None:
    """Verify a response carries X-Frame-Options: DENY (ENG-01 6.4)."""
    assert signed_in.get(reverse("reports"))["X-Frame-Options"] == "DENY"
