"""The upload screens (ENG-03 4): Uploads, the upload form, each upload with
its preview, and the actions on it.

Each view checks access with the one access check (ENG-01 6.2), then calls the
functions in imports/, which decide everything else. A refused action changes
nothing, and the page says why, in the refusal's own words.
"""

from collections.abc import Callable

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ids_audit.accounts.access import (
    APPROVE,
    ROLL_BACK,
    UPLOAD,
    VIEW,
    allowed,
    report_permission,
    require,
)
from ids_audit.accounts.middleware import is_htmx
from ids_audit.imports import screens
from ids_audit.imports.commit import commit_upload
from ids_audit.imports.decisions import reject_upload, withdraw_upload
from ids_audit.imports.forms import MappingForm, UploadForm
from ids_audit.imports.models import Upload
from ids_audit.imports.rollback import (
    actual_spend_change,
    check_rollback,
    roll_back_upload,
)
from ids_audit.imports.services import receive_file
from ids_audit.imports.stage import pick_columns, stage_upload
from ids_audit.imports.states import Refused, fail_interrupted_reads
from ids_audit.imports.storage import read_stored_file
from ids_audit.pipeline.columns import column_letter
from ids_audit.pipeline.read import first_rows
from ids_audit.reports.models import Report

Status = Upload.Status


def get_upload(report: Report, number: int) -> Upload:
    """Return the report's upload with this number, or 404."""
    return get_object_or_404(
        report.uploads.select_related("uploaded_by", "decided_by", "ruleset"),
        number=number,
    )


def require_to_see(user, report: Report, upload: Upload) -> None:
    """Check the user may see the upload and its preview: view, and for an
    upload never committed, upload or approve as well (ENG-03 4).
    """
    if upload.status in (Status.COMMITTED, Status.ROLLED_BACK):
        require(user, report, VIEW)
    else:
        require(user, report, VIEW, any_of=(UPLOAD, APPROVE))


def to_upload(upload: Upload):
    """Open the upload's page."""
    return redirect("upload", upload.report_id, upload.number)


def read_outcome(upload: Upload) -> tuple[int, str]:
    """Return the message, and its level, for how reading an upload ended."""
    if upload.status == Status.AWAITING_APPROVAL:
        return messages.SUCCESS, f"Upload {upload.number} is read and awaits approval."
    if upload.status == Status.NEEDS_COLUMNS:
        return (
            messages.WARNING,
            f"Upload {upload.number} needs a column picked for each problem below.",
        )
    return messages.ERROR, f"Upload {upload.number} couldn't be read: {upload.error}"


def act(request, action: Callable[[], object], done: str) -> bool:
    """Run an action, then say it was done, or why it was refused and that
    nothing changed. Return whether it was done.
    """
    try:
        action()
    except (Refused, ValueError) as refusal:
        messages.error(request, f"{refusal} Nothing was changed.")
        return False
    messages.success(request, done)
    return True


@report_permission(VIEW)
def uploads(request, report: Report):
    """Every upload in the report, newest first. Opening it first marks any
    interrupted read failed (ENG-02 6).
    """
    fail_interrupted_reads(report)
    uploads = report.uploads.select_related("uploaded_by", "decided_by")
    return render(
        request,
        "imports/uploads.html",
        {
            "report": report,
            "rows": [
                {"upload": upload, "for_you": for_you(request.user, report, upload)}
                for upload in uploads.order_by("-number")
            ],
            "can_upload": allowed(request.user, report, UPLOAD),
        },
    )


def for_you(user, report: Report, upload: Upload) -> str:
    """Say when an open upload waits for the user: their approval, or their
    columns to pick, as its uploader (ENG-03 4).
    """
    if upload.status == Status.AWAITING_APPROVAL and allowed(user, report, APPROVE):
        return "Waiting for your approval"
    if upload.status == Status.NEEDS_COLUMNS and allowed(
        user, report, UPLOAD, upload=upload
    ):
        return "Waiting for you to pick columns"
    return ""


@report_permission(UPLOAD)
def new_upload(request, report: Report):
    """The upload form. Sending it receives the file and reads it, then opens
    the upload (ENG-01 5.1).
    """
    # The settings the file will be read with.
    ruleset = report.rulesets.order_by("-version").first()
    form = UploadForm(
        request.POST or None,
        request.FILES or None,
        decimal_separator=ruleset.settings["decimal_separator"],
    )
    if request.method == "POST" and form.is_valid():
        file = form.cleaned_data["file"]
        try:
            upload = receive_file(
                report,
                request.user,
                file.name,
                file.read(),
                form.cleaned_data["control_total"],
            )
        except Refused as refusal:
            form.add_error(None, str(refusal))
        except ValueError as error:
            # Only the typed total can be wrong this way.
            form.add_error("control_total", str(error))
        else:
            upload = stage_upload(upload, request.user)
            messages.add_message(request, *read_outcome(upload))
            return to_upload(upload)
    return render(request, "imports/new_upload.html", {"report": report, "form": form})


@report_permission(UPLOAD)
def new_mapping(request, report: Report):
    """The mapping form. Sending it receives the file as a mapping upload and
    reads it, then opens the upload (ENG-02 8.5).
    """
    form = MappingForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        file = form.cleaned_data["file"]
        try:
            upload = receive_file(
                report, request.user, file.name, file.read(), kind=Upload.Kind.MAPPING
            )
        except Refused as refusal:
            form.add_error(None, str(refusal))
        else:
            upload = stage_upload(upload, request.user)
            messages.add_message(request, *read_outcome(upload))
            return to_upload(upload)
    return render(request, "imports/new_mapping.html", {"report": report, "form": form})


@report_permission(VIEW)
def upload_page(request, report: Report, number: int):
    """One upload: its file, state and decisions, why it stopped or failed,
    its preview once read, and the actions the user may take on it.
    """
    upload = get_upload(report, number)
    require_to_see(request.user, report, upload)
    user = request.user
    exceptions = screens.exception_summary(upload.preview or {})
    open_upload = upload.status in (Status.NEEDS_COLUMNS, Status.AWAITING_APPROVAL)
    problems = []
    if upload.status == Status.NEEDS_COLUMNS:
        problems, _ = screens.column_problems(upload)
    return render(
        request,
        "imports/upload.html",
        {
            "report": report,
            "upload": upload,
            "preview": upload.preview,
            "value_types": screens.value_types(upload.preview or {}),
            "mappings": screens.mappings(upload.preview or {}),
            "codes": screens.mapping_codes(upload),
            "moves": screens.moves(upload.preview or {}),
            "exceptions": exceptions,
            "blocking": [e["name"] for e in exceptions if e["blocks_approval"]],
            "closing": screens.closing(upload),
            "problems": problems,
            # A mapping upload never sets the duplicate mode (ENG-02 8.5).
            "first_upload": not report.duplicate_mode
            and upload.kind == Upload.Kind.SPEND,
            "can_approve": upload.status == Status.AWAITING_APPROVAL
            and allowed(user, report, APPROVE),
            "can_reject": open_upload and allowed(user, report, APPROVE),
            "can_withdraw": open_upload
            and allowed(user, report, UPLOAD, upload=upload),
            "can_pick": upload.status == Status.NEEDS_COLUMNS
            and allowed(user, report, UPLOAD, upload=upload),
            "can_roll_back": upload.status == Status.COMMITTED
            and allowed(user, report, ROLL_BACK),
        },
    )


@report_permission(VIEW)
def exception_items(request, report: Report, number: int, kind: str):
    """The items of one exception type in an upload's preview: a panel for an
    HTMX request, and a page of its own otherwise.
    """
    upload = get_upload(report, number)
    require_to_see(request.user, report, upload)
    exception = screens.find_exception(upload, kind)
    if exception is None:
        raise Http404("The preview has no exception of that type.")
    context = {
        "report": report,
        "upload": upload,
        "exception": {**exception, "name": screens.EXCEPTION_NAMES[kind]},
        "columns": screens.item_columns(exception),
        "items": screens.item_rows(exception),
    }
    if is_htmx(request):
        template = "imports/_exception_items.html"
    else:
        template = "imports/exception_items.html"
    return render(request, template, context)


@report_permission(APPROVE)
@require_POST
def approve(request, report: Report, number: int):
    """Approve an upload. On a report's first upload, the form also sends the
    duplicate mode the approver chose (ENG-02 7.3 and 10.1).
    """
    upload = get_upload(report, number)
    mode = request.POST.get("duplicate_mode") or None
    act(
        request,
        lambda: commit_upload(upload, request.user, duplicate_mode=mode),
        f"Upload {upload.number} is approved, and counts in the report.",
    )
    return to_upload(upload)


@report_permission(APPROVE)
@require_POST
def reject(request, report: Report, number: int):
    """Reject an upload, with a reason (ENG-02 6)."""
    upload = get_upload(report, number)
    act(
        request,
        lambda: reject_upload(upload, request.user, request.POST.get("reason", "")),
        f"Upload {upload.number} is rejected. The report didn't change.",
    )
    return to_upload(upload)


@report_permission(UPLOAD)
@require_POST
def withdraw(request, report: Report, number: int):
    """Withdraw an upload, as its uploader, with a reason (ENG-02 6, ENG-01
    6.2 rule 6).
    """
    upload = get_upload(report, number)
    require(request.user, report, UPLOAD, upload=upload)
    act(
        request,
        lambda: withdraw_upload(upload, request.user, request.POST.get("reason", "")),
        f"Upload {upload.number} is withdrawn. The report didn't change.",
    )
    return to_upload(upload)


@report_permission(UPLOAD)
def columns(request, report: Report, number: int):
    """Pick a column for each problem of an upload at needs_columns, from its
    sheets' header rows and first rows, as its uploader (ENG-02 4.5).
    """
    upload = get_upload(report, number)
    require(request.user, report, UPLOAD, upload=upload)
    if upload.status != Status.NEEDS_COLUMNS:
        messages.error(
            request,
            f"Upload {upload.number} is {upload.get_status_display().lower()}, so it "
            "needs no columns picked.",
        )
        return to_upload(upload)
    problems, headers = screens.column_problems(upload)
    if request.method == "POST":
        picks: dict[str, dict[str, str]] = {}
        for index, problem in enumerate(problems):
            letter = request.POST.get(f"pick-{index}", "")
            if letter:
                picks.setdefault(problem["sheet"], {})[problem["field"]] = letter
        try:
            upload = pick_columns(upload, request.user, picks)
        except (Refused, ValueError) as refusal:
            messages.error(request, f"{refusal} Nothing was changed.")
            return redirect("columns", report.pk, upload.number)
        messages.add_message(request, *read_outcome(upload))
        return to_upload(upload)

    data = read_stored_file(upload.file_sha256)
    sheets = []
    for sheet in dict.fromkeys(problem["sheet"] for problem in problems):
        found = first_rows(data, sheet, headers[sheet])
        header = headers[sheet]
        width = len(found.rows[0][1]) if found.rows else len(header)
        sheets.append(
            {
                "name": sheet,
                "rows": found.rows,
                "letters": [column_letter(i) for i in range(width)],
                "choices": [
                    (column_letter(i), header[i] if i < len(header) else "")
                    for i in range(width)
                ],
                "problems": [
                    (index, problem)
                    for index, problem in enumerate(problems)
                    if problem["sheet"] == sheet
                ],
            }
        )
    return render(
        request,
        "imports/columns.html",
        {"report": report, "upload": upload, "sheets": sheets},
    )


@report_permission(ROLL_BACK)
def roll_back(request, report: Report, number: int):
    """The rollback's confirmation, with the report's actual spend now and
    once rolled back, then the rollback, with a reason (ENG-02 10.3).
    """
    upload = get_upload(report, number)
    if request.method == "POST":
        done = act(
            request,
            lambda: roll_back_upload(
                upload, request.user, request.POST.get("reason", "")
            ),
            f"Upload {upload.number} is rolled back. The report's figures are as "
            "they were before it was approved.",
        )
        return to_upload(upload) if done else redirect("roll_back", report.pk, number)
    context = {"report": report, "upload": upload}
    try:
        check_rollback(upload)
    except Refused as refusal:
        context["refusal"] = str(refusal)
    else:
        context["before"], context["after"] = actual_spend_change(upload)
    return render(request, "imports/roll_back.html", context)
