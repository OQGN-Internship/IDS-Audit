"""The audit log screens (ENG-03 4): a report's events for a user with
view_audit_log, and the whole log for fixed administrators. Both only read
the log (ENG-01 6.3). Their search and filters only narrow what each already
shows.
"""

import datetime
import json
import re
from dataclasses import asdict, dataclass
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.core.exceptions import BadRequest
from django.core.paginator import EmptyPage, Paginator
from django.db.models import Q, QuerySet, TextField
from django.db.models.functions import Cast
from django.http import Http404
from django.shortcuts import render
from django.utils import timezone

from ids_audit.accounts.access import VIEW_AUDIT_LOG, report_permission
from ids_audit.accounts.admin_views import fixed_admin_only
from ids_audit.audit.models import AuditEvent
from ids_audit.reports.models import Report

Action = AuditEvent.Action

# Events on one page of the log.
PAGE_SIZE = 100
# The most a search may hold, in characters.
SEARCH_MOST = 100
# The keys each log's address takes; the whole log takes report too.
KEYS = ("q", "action", "user", "from", "to", "page")
DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
WHOLE = re.compile(r"[1-9][0-9]*")


@dataclass(frozen=True)
class Search:
    """What the address asks of the log (ENG-03 4): words to find, an
    action, a username, the first and last day, a report's id or "none",
    and the page. An empty one is no filter.
    """

    q: str = ""
    action: str = ""
    user: str = ""
    start: datetime.date | None = None
    end: datetime.date | None = None
    report: str = ""
    page: int = 1

    @property
    def on(self) -> bool:
        """Whether any search or filter is on."""
        return any(value for key, value in asdict(self).items() if key != "page")

    def query(self, page: int) -> str:
        """The address of another page of this search: its keys that are on,
        then the page."""
        values = {
            "q": self.q,
            "action": self.action,
            "user": self.user,
            "from": self.start.isoformat() if self.start else "",
            "to": self.end.isoformat() if self.end else "",
            "report": self.report,
        }
        return "?" + urlencode({**{k: v for k, v in values.items() if v}, "page": page})


def day(value: str, key: str) -> datetime.date | None:
    """Return the day a from or to value names, or None for an empty one."""
    if not value:
        return None
    try:
        if not DAY.fullmatch(value):
            raise ValueError
        return datetime.date.fromisoformat(value)
    except ValueError:
        raise BadRequest(f"{key} is a date written as 2026-10-27.") from None


def read(request, full: bool) -> Search:
    """Return the search the address asks for, refusing a key given twice, a
    key the log doesn't take, or a value outside its kind (ENG-03 4).
    """
    keys = KEYS + ("report",) if full else KEYS
    if set(request.GET) - set(keys):
        raise BadRequest("The audit log takes only " + ", ".join(keys) + ".")
    if any(len(request.GET.getlist(key)) > 1 for key in request.GET):
        raise BadRequest("Each key is given at most once.")
    value = {key: request.GET.get(key, "").strip() for key in keys}
    if len(value["q"]) > SEARCH_MOST:
        raise BadRequest(f"A search holds at most {SEARCH_MOST} characters.")
    if value["action"] and value["action"] not in Action.values:
        raise BadRequest("The action is one the log records.")
    if full and value["report"] and value["report"] != "none":
        if not WHOLE.fullmatch(value["report"]):
            raise BadRequest("The report is a report's id, or none.")
    page = request.GET.get("page", "1")
    if not WHOLE.fullmatch(page):
        raise BadRequest("The page is a whole number from 1.")
    return Search(
        q=value["q"],
        action=value["action"],
        user=value["user"],
        start=day(value["from"], "from"),
        end=day(value["to"], "to"),
        report=value["report"] if full else "",
        page=int(page),
    )


def midnight(date: datetime.date) -> datetime.datetime:
    """The day's start in Muscat time, where the log's days are counted."""
    return timezone.make_aware(datetime.datetime.combine(date, datetime.time.min))


def narrowed(events: QuerySet[AuditEvent], search: Search) -> QuerySet[AuditEvent]:
    """Return the events the search keeps. Words are found, ignoring case,
    in the details, the user's username or full name, the report's name or
    the action, as typed: Django's icontains reads % and _ as themselves.
    """
    if search.q:
        events = events.annotate(details_text=Cast("details", TextField())).filter(
            Q(details_text__icontains=search.q)
            | Q(user__username__icontains=search.q)
            | Q(user__full_name__icontains=search.q)
            | Q(report__name__icontains=search.q)
            | Q(action__icontains=search.q.replace(" ", "_"))
        )
    if search.action:
        events = events.filter(action=search.action)
    if search.user:
        events = events.filter(user__username=search.user)
    if search.start:
        events = events.filter(at__gte=midnight(search.start))
    if search.end:
        events = events.filter(at__lt=midnight(search.end + datetime.timedelta(days=1)))
    if search.report == "none":
        events = events.filter(report__isnull=True)
    elif search.report:
        events = events.filter(report_id=int(search.report))
    return events


def in_words(action: str) -> str:
    """An action in words, such as "Upload approved"."""
    return action.replace("_", " ").capitalize()


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


def choices(events: QuerySet[AuditEvent], search: Search, full: bool) -> dict:
    """The lists the search card offers: the actions in the log and the
    accounts that acted in it, in words, and on the whole log the reports.
    A value chosen in the address stays offered, so the form keeps it.
    """
    actions = {
        a: in_words(a) for a in events.values_list("action", flat=True).distinct()
    }
    if search.action:
        actions[search.action] = in_words(search.action)
    users = {
        user.username: str(user)
        for user in get_user_model().objects.filter(pk__in=events.values("user"))
    }
    if search.user and search.user not in users:
        users[search.user] = search.user
    found = {
        "actions": sorted(actions.items(), key=lambda pair: pair[1]),
        "users": sorted(users.items(), key=lambda pair: pair[1].casefold()),
    }
    if full:
        found["reports"] = [
            (str(report.pk), report.name)
            for report in Report.objects.order_by("name", "pk")
        ]
    return found


def show(request, events: QuerySet[AuditEvent], context: dict):
    """Render one page of the events the search keeps, newest first."""
    full = context.get("full", False)
    search = read(request, full)
    pages = Paginator(
        narrowed(events, search)
        .select_related("user", "report", "upload")
        .order_by("-id"),
        PAGE_SIZE,
    )
    try:
        page = pages.page(search.page)
    except EmptyPage as past:
        raise Http404("There's no such page of the audit log.") from past
    rows = [
        {
            "event": event,
            "who": who(event),
            "action": in_words(event.action),
            "details": details(event),
        }
        for event in page
    ]
    count = pages.count
    if search.on:
        found = f"{count} event{'s match' if count != 1 else ' matches'}"
    else:
        found = f"{count} event{'s' if count != 1 else ''}"
    pager = []
    if page.has_previous():
        pager.append(("Newer events", search.query(page.previous_page_number())))
    if page.has_next():
        pager.append(("Older events", search.query(page.next_page_number())))
    return render(
        request,
        "audit/log.html",
        {
            **context,
            **choices(events, search, full),
            "search": search,
            "page": page,
            "rows": rows,
            "found": found,
            "pager": pager,
        },
    )


@report_permission(VIEW_AUDIT_LOG)
def report_log(request, report: Report):
    """The events that name the report (ENG-03 4)."""
    return show(request, AuditEvent.objects.filter(report=report), {"report": report})


@fixed_admin_only
def full_log(request):
    """Every event, for fixed administrators (ENG-01 6.1)."""
    return show(request, AuditEvent.objects.all(), {"full": True})
