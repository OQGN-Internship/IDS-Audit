"""The report list, creating a report, and the report screens (ENG-03 4).

Each screen's view checks access with the one access check (ENG-01 6.2),
reads its filters and keys from the address, and shows the tables
reports/screens.py builds. A panel request from HTMX gets the panel alone.
"""

import tempfile
from collections.abc import Callable

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError
from django.db.models.functions import Lower
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from ids_audit import audit
from ids_audit.accounts.access import (
    APPROVE,
    EXPORT,
    UPLOAD,
    VIEW,
    allowed,
    may_create_reports,
    report_permission,
    require,
    visible_reports,
)
from ids_audit.accounts.middleware import is_htmx
from ids_audit.audit.models import AuditEvent
from ids_audit.imports.services import create_report
from ids_audit.reports import export, faq, queries, screens
from ids_audit.reports import filters as addresses
from ids_audit.reports.models import Report
from ids_audit.reports.queries import NO_KEYS, NONE, OUTSIDE, Filters, Keys

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ReportForm(forms.Form):
    """A new report's name (ENG-02 3)."""

    name = forms.CharField(max_length=200)


def report_list(request):
    """Every report the user can open (ENG-01 6.2, rule 3), by name, each as a
    card with its actual spend, the upload its figures are as of, and the
    upload waiting for someone (ENG-03 4).
    """
    cards = []
    for report in visible_reports(request.user).order_by(Lower("name")):
        latest = queries.latest_committed(report)
        waiting = queries.waiting(report)
        cards.append(
            {
                "report": report,
                "totals": queries.totals(report) if latest else None,
                "rows": screens.Links(report, queries.ALL).rows(),
                "latest": latest,
                "waiting": waiting,
                # Its page needs upload or approve as well as view (ENG-03 4).
                "can_open_waiting": bool(waiting)
                and allowed(request.user, report, VIEW, any_of=(UPLOAD, APPROVE)),
            }
        )
    return render(
        request,
        "reports/report_list.html",
        {"cards": cards, "can_create": may_create_reports(request.user)},
    )


def new_report(request):
    """Create a report, then open its rules, to check before its first upload
    (ENG-03 4). Only fixed administrators create reports, the plan's default
    (ENG-01 8).
    """
    if not may_create_reports(request.user):
        raise PermissionDenied
    form = ReportForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        name = form.cleaned_data["name"]
        try:
            report = create_report(name, request.user)
        except IntegrityError:
            form.add_error(
                "name",
                f"A report named {name} already exists. Names that differ only in "
                "capitals count as the same.",
            )
        else:
            messages.success(
                request,
                f"Report {report} is created. Check its rules before its first "
                "upload, above all the mapping rule, which can't change once an "
                "upload is open or committed.",
            )
            return redirect("rules", report.pk)
    return render(request, "reports/new_report.html", {"form": form})


@report_permission(VIEW)
def report_home(request, report: Report):
    """A report's own address, which opens its overview (ENG-03 4)."""
    return redirect("overview", report.pk)


def panel_only(request: HttpRequest) -> bool:
    """Return whether to send the panel alone: for an HTMX request, but not
    HTMX's request for a whole page that's no longer in its history.
    """
    restoring = request.headers.get("HX-History-Restore-Request") == "true"
    return is_htmx(request) and not restoring


def show(
    request: HttpRequest,
    report: Report,
    filters: Filters,
    name: str,
    build: Callable[[], screens.Screen],
    hidden: dict[str, str] | None = None,
    keys: Keys = NO_KEYS,
    to_excel: bool = False,
) -> HttpResponse:
    """Render a screen: the whole page, or for HTMX its panel, which holds
    everything the filters change (ENG-03 4), or with to_excel its export
    (ENG-03 9). A report with no committed upload shows no figures, so its
    screen isn't built for the page.

    name is the screen's heading until it's built, hidden holds the screen's
    own keys, which its filter form sends again with the filters, and keys
    are its drill-down keys, which its export carries.
    """
    if to_excel:
        require(request.user, report, VIEW, EXPORT)
        return excel(request, report, filters, keys, build())
    latest = queries.latest_committed(report)
    awaiting = queries.awaiting_approval(report)
    screen = build() if latest else None
    years = queries.years(report)
    if filters.year is not None and filters.year not in years:
        years = sorted([*years, filters.year], reverse=True)
    context = {
        "report": report,
        "screen": screen,
        "heading": screen.title if screen else name,
        "hidden": hidden or {},
        "filters": filters,
        "filter_tags": addresses.tags(
            filters, screen.outside_applies if screen else True
        ),
        "latest": latest,
        "awaiting": awaiting,
        # Its preview needs upload or approve as well as view (ENG-03 4).
        "can_open_awaiting": bool(awaiting)
        and allowed(request.user, report, VIEW, any_of=(UPLOAD, APPROVE)),
        "years": years,
        "can_upload": allowed(request.user, report, UPLOAD),
        "can_export": allowed(request.user, report, VIEW, EXPORT),
        "has_charts": bool(screen) and any(t.charts for t in screen.tables),
        "export_url": request.path
        + "/export"
        + addresses.query(filters, **export_values(screen, keys)),
    }
    template = "reports/_panel.html" if panel_only(request) else "reports/screen.html"
    return render(request, template, context)


def export_values(screen: screens.Screen | None, keys: Keys) -> dict[str, str]:
    """Return the keys an export's address carries: the screen's drill-down
    keys, and an FAQ page's pick, written out even when it's the default
    (ENG-03 9.1).
    """
    values = addresses.key_values(keys)
    if screen is not None and screen.picker is not None and screen.picker.chosen:
        values[screen.picker.key] = screen.picker.chosen
    return values


def excel(
    request: HttpRequest, report: Report, filters: Filters, keys: Keys, screen
) -> HttpResponse:
    """Send a screen's export, written to a temporary file, and record it as
    one export_downloaded event. An export past Excel's row limit is refused
    before anything is written, with no event (ENG-03 9.4).
    """
    rows = export.largest_sheet(screen)
    values = export_values(screen, keys)
    address = request.path.removesuffix("/export") + addresses.query(filters, **values)
    if rows > export.MAX_DATA_ROWS:
        return render(
            request,
            "reports/export_refused.html",
            {
                "report": report,
                "heading": screen.title,
                "rows": rows,
                "limit": export.MAX_DATA_ROWS,
                "back": address,
            },
        )
    at = timezone.now()
    name = export.file_name(report, screen.view, at)
    output = tempfile.TemporaryFile()
    written = export.write_workbook(
        output, report, screen, address, filters, request.user, at
    )
    audit.record(
        AuditEvent.Action.EXPORT_DOWNLOADED,
        user=request.user,
        report=report,
        details={
            "view": screen.view,
            "filters": {
                "year": "all" if filters.year is None else str(filters.year),
                "exclude_outside": "1" if filters.exclude_outside else "0",
                **values,
            },
            "rows": written,
            "file_name": name,
        },
    )
    output.seek(0)
    return FileResponse(output, as_attachment=True, filename=name, content_type=XLSX)


def filters_only(request: HttpRequest) -> Filters:
    """Return the filters of a screen that takes no keys of its own."""
    return addresses.filters_from(addresses.read(request.GET))


@report_permission(VIEW)
def overview(request, report: Report, to_excel: bool = False):
    filters = filters_only(request)
    return show(
        request,
        report,
        filters,
        "Overview",
        lambda: screens.overview(report, filters),
        to_excel=to_excel,
    )


@report_permission(VIEW)
def gl_accounts(request, report: Report, to_excel: bool = False):
    filters = filters_only(request)
    return show(
        request,
        report,
        filters,
        "GL accounts",
        lambda: screens.gl_accounts(report, filters),
        to_excel=to_excel,
    )


@report_permission(VIEW)
def gl_account(request, report: Report, code: str, to_excel: bool = False):
    filters = filters_only(request)
    gl = addresses.code(code)
    return show(
        request,
        report,
        filters,
        f"GL account {gl}",
        lambda: screens.gl_account(report, filters, gl),
        to_excel=to_excel,
    )


@report_permission(VIEW)
def departments(request, report: Report, to_excel: bool = False):
    filters = filters_only(request)
    return show(
        request,
        report,
        filters,
        "Departments",
        lambda: screens.departments(report, filters),
        to_excel=to_excel,
    )


@report_permission(VIEW)
def department(request, report: Report, code: str, to_excel: bool = False):
    filters = filters_only(request)
    key = addresses.code(code, (NONE, OUTSIDE))
    return show(
        request,
        report,
        filters,
        screens.department_title(key, ""),
        lambda: screens.department(report, filters, key),
        to_excel=to_excel,
    )


@report_permission(VIEW)
def budget_lines(request, report: Report, to_excel: bool = False):
    filters = filters_only(request)
    return show(
        request,
        report,
        filters,
        "Budget lines",
        lambda: screens.budget_lines(report, filters),
        to_excel=to_excel,
    )


@report_permission(VIEW)
def budget_line(request, report: Report, code: str, to_excel: bool = False):
    filters = filters_only(request)
    key = addresses.code(code, (NONE,))
    return show(
        request,
        report,
        filters,
        queries.NO_BUDGET_LINE if key == NONE else f"Budget line {key}",
        lambda: screens.budget_line(report, filters, key),
        to_excel=to_excel,
    )


@report_permission(VIEW)
def source_rows(request, report: Report, to_excel: bool = False):
    taken = addresses.ROW_KEYS
    if to_excel:
        # The export holds every row, so it takes no page (ENG-03 9.1).
        taken = tuple(key for key in taken if key != "page")
    values = addresses.read(request.GET, taken)
    filters = addresses.filters_from(values)
    keys = addresses.keys_from(values)
    page = None if to_excel else addresses.page_from(values)
    return show(
        request,
        report,
        filters,
        addresses.keys_in_words(keys),
        lambda: screens.source_rows(report, filters, keys, page),
        addresses.key_values(keys),
        keys,
        to_excel,
    )


@report_permission(VIEW)
def exceptions(request, report: Report, to_excel: bool = False):
    filters = filters_only(request)
    return show(
        request,
        report,
        filters,
        "Exceptions",
        lambda: screens.exceptions(report, filters),
        to_excel=to_excel,
    )


@report_permission(VIEW)
def faq_index(request, report: Report):
    """The FAQ questions in order, each linking to its page (ENG-03 7.1). It
    takes the filters like any screen, and refuses a wrong one (3.1).
    """
    filters_only(request)
    return render(
        request, "reports/faq_index.html", {"report": report, "entries": faq.ENTRIES}
    )


@report_permission(VIEW)
def faq_page(request, report: Report, slug: str, to_excel: bool = False):
    """One FAQ page. Pages 4 and 6 take their pick, gl or dept, in the
    address, and show the largest without one (ENG-03 7.2).
    """
    entry = faq.BY_SLUG.get(slug)
    if entry is None:
        raise Http404("There's no such question.")
    values = addresses.read(request.GET, (entry.pick,) if entry.pick else ())
    filters = addresses.filters_from(values)
    pick = values.get(entry.pick) if entry.pick else None
    if pick is not None:
        catch_alls = (NONE, OUTSIDE) if entry.pick == "dept" else ()
        pick = addresses.code(pick, catch_alls)
    return show(
        request,
        report,
        filters,
        entry.question,
        lambda: faq.page(report, filters, entry, pick),
        to_excel=to_excel,
    )
