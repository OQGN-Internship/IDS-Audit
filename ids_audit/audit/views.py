"""The audit log screens (ENG-03 4): a report's events for a user with
view_audit_log, and the whole log for fixed administrators. Both only read
the log (ENG-01 6.3).
"""

import json

from django.core.exceptions import BadRequest
from django.core.paginator import EmptyPage, Paginator
from django.db.models import QuerySet
from django.http import Http404
from django.shortcuts import render

from ids_audit.accounts.access import VIEW_AUDIT_LOG, report_permission
from ids_audit.accounts.admin_views import fixed_admin_only
from ids_audit.audit.models import AuditEvent
from ids_audit.reports.models import Report

Action = AuditEvent.Action

# Events on one page of the log.
PAGE_SIZE = 100


def page_number(request) -> int:
    """Return the page the address asks for, from 1. The address takes only
    page, once, as a whole number from 1 (ENG-03 4).
    """
    if set(request.GET) - {"page"} or len(request.GET.getlist("page")) > 1:
        raise BadRequest("The audit log takes only page.")
    value = request.GET.get("page", "1")
    if not value.isdigit() or int(value) < 1:
        raise BadRequest("The page is a whole number from 1.")
    return int(value)


def who(event: AuditEvent) -> str:
    """Name who acted: the user, or why there's none (ENG-03 4)."""
    if event.user is not None:
        return str(event.user)
    if "command" in event.details:
        return "Command line"
    if event.action in (Action.SIGN_IN_FAILED, Action.ACCOUNT_LOCKED):
        return "Unknown username"
    return "The application"


def details(event: AuditEvent) -> list[tuple[str, str]]:
    """Return the event's details as names and values, by name. A value that
    holds more than text is written as JSON, its names in order too, since
    jsonb keeps none (ENG-03 4).
    """
    return [
        (
            name,
            value
            if isinstance(value, str)
            else json.dumps(value, ensure_ascii=False, sort_keys=True),
        )
        for name, value in sorted(event.details.items())
    ]


def show(request, events: QuerySet[AuditEvent], context: dict):
    """Render one page of the events, newest first."""
    pages = Paginator(
        events.select_related("user", "report", "upload").order_by("-id"), PAGE_SIZE
    )
    try:
        page = pages.page(page_number(request))
    except EmptyPage as past:
        raise Http404("There's no such page of the audit log.") from past
    rows = [
        {
            "event": event,
            "who": who(event),
            "action": event.action.replace("_", " ").capitalize(),
            "details": details(event),
        }
        for event in page
    ]
    return render(request, "audit/log.html", {**context, "page": page, "rows": rows})


@report_permission(VIEW_AUDIT_LOG)
def report_log(request, report: Report):
    """The events that name the report (ENG-03 4)."""
    return show(request, AuditEvent.objects.filter(report=report), {"report": report})


@fixed_admin_only
def full_log(request):
    """Every event, for fixed administrators (ENG-01 6.1)."""
    return show(request, AuditEvent.objects.all(), {"full": True})
