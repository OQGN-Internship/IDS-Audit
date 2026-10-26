"""The one access check every view that touches a report calls (ENG-01 4,
rule 3, and 6.2). No view decides access on its own.
"""

import functools

from django.core.exceptions import PermissionDenied
from django.db.models import QuerySet
from django.http import Http404
from django.shortcuts import get_object_or_404

from ids_audit.accounts.models import ReportAccess, User

# The six permissions (ENG-01 6.2), which the views import from here. The
# aliases mark them as passed on.
from ids_audit.accounts.permissions import APPROVE as APPROVE
from ids_audit.accounts.permissions import EXPORT as EXPORT
from ids_audit.accounts.permissions import PERMISSIONS as PERMISSIONS
from ids_audit.accounts.permissions import ROLL_BACK as ROLL_BACK
from ids_audit.accounts.permissions import UPLOAD as UPLOAD
from ids_audit.accounts.permissions import VIEW as VIEW
from ids_audit.accounts.permissions import VIEW_AUDIT_LOG as VIEW_AUDIT_LOG
from ids_audit.reports.models import Report


def held(user: User, report: Report) -> frozenset[str]:
    """Return the permissions the user holds on the report (ENG-01 6.2).

    An active fixed administrator holds every one on every report (rule 4).
    Anyone else active holds their role's on the report, from their
    ReportAccess row, and nothing without one. A disabled account holds
    nothing. It's read at every check, so a change applies at once.
    """
    if not (user.is_authenticated and user.is_active):
        return frozenset()
    if user.is_fixed_admin:
        return PERMISSIONS
    role = (
        ReportAccess.objects.filter(user=user, report=report)
        .values_list("role__permissions", flat=True)
        .first()
    )
    return frozenset(role or ())


def require(
    user: User,
    report: Report,
    *permissions: str,
    any_of: tuple[str, ...] = (),
    upload=None,
) -> None:
    """Refuse unless the user holds every one of permissions on the report,
    and at least one of any_of when it's given (ENG-01 6.2).

    A user with no permission on the report gets Http404, so its existence
    isn't revealed, and one missing a permission gets PermissionDenied, which
    Django answers with 403 (rule 2). With upload, the user must also be the
    one who uploaded it, unless they're a fixed administrator (rule 6). A name
    that isn't a permission is a ValueError.
    """
    unknown = (set(permissions) | set(any_of)) - PERMISSIONS
    if unknown:
        raise ValueError(f"Not a permission: {', '.join(sorted(unknown))}.")
    holds = held(user, report)
    if not holds:
        raise Http404("No such report.")
    if not holds.issuperset(permissions):
        raise PermissionDenied
    if any_of and not holds.intersection(any_of):
        raise PermissionDenied
    if upload is not None and upload.uploaded_by_id != user.pk:
        if not user.is_fixed_admin:
            raise PermissionDenied


def allowed(
    user: User,
    report: Report,
    *permissions: str,
    any_of: tuple[str, ...] = (),
    upload=None,
) -> bool:
    """Return whether require() would let the user through, so a screen shows
    only the actions the user may take. The action's own address still calls
    require() (ENG-03 4).
    """
    try:
        require(user, report, *permissions, any_of=any_of, upload=upload)
    except (Http404, PermissionDenied):
        return False
    return True


def report_permission(*permissions: str, any_of: tuple[str, ...] = ()):
    """Decorate a view of an address under /reports/<report_id>/: load the
    report, check the user's access to it with require(), and pass the report
    to the view in place of its id. A report that doesn't exist is 404, the
    same as one the user can't see.
    """

    def decorator(view):
        @functools.wraps(view)
        def checked(request, report_id: int, *args, **kwargs):
            report = get_object_or_404(Report, pk=report_id)
            require(request.user, report, *permissions, any_of=any_of)
            return view(request, report, *args, **kwargs)

        return checked

    return decorator


def visible_reports(user: User) -> QuerySet[Report]:
    """Return the reports the user can open: every report for a fixed
    administrator, and the ones anyone else has a ReportAccess row for
    (ENG-01 6.2, rules 3 and 4). A disabled account sees none.
    """
    if not user.is_active:
        return Report.objects.none()
    if user.is_fixed_admin:
        return Report.objects.all()
    return Report.objects.filter(access__user=user)


def may_create_reports(user: User) -> bool:
    """Return whether the user may create a report: a fixed administrator, or
    a user with can_create_reports, which nothing turns on until the client
    answers the plan's open question (ENG-01 8).
    """
    return user.is_active and (user.is_fixed_admin or user.can_create_reports)
