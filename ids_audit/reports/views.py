"""The report list and creating a report (ENG-03 4). Phase 3 adds the report
screens.
"""

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError
from django.db.models.functions import Lower
from django.shortcuts import redirect, render

from ids_audit.accounts.access import (
    VIEW,
    may_create_reports,
    report_permission,
    visible_reports,
)
from ids_audit.imports.services import create_report
from ids_audit.reports.models import Report


class ReportForm(forms.Form):
    """A new report's name (ENG-02 3)."""

    name = forms.CharField(max_length=200)


def report_list(request):
    """Every report the user can open (ENG-01 6.2, rule 3), by name."""
    return render(
        request,
        "reports/report_list.html",
        {
            "reports": visible_reports(request.user)
            .select_related("created_by")
            .order_by(Lower("name")),
            "can_create": may_create_reports(request.user),
        },
    )


def new_report(request):
    """Create a report, then open its Uploads. Only fixed administrators
    create reports, the plan's default (ENG-01 8).
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
            messages.success(request, f"Report {report} is created.")
            return redirect("uploads", report.pk)
    return render(request, "reports/new_report.html", {"form": form})


@report_permission(VIEW)
def report_home(request, report: Report):
    """A report's own address. Until Phase 3 builds the overview, it opens
    Uploads (ENG-03 4).
    """
    return redirect("uploads", report.pk)
