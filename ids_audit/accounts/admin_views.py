"""The administration screens at /admin/ (ENG-01 6.1): accounts, roles and
report access, open only to active fixed administrators.

Each form is sent with POST and saved through one function in services.py or
roles.py, which checks the caller again and writes the event. A refused
change saves nothing, and the page says why.
"""

import functools
from collections.abc import Callable

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count
from django.db.models.functions import Lower
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ids_audit.accounts import services
from ids_audit.accounts.forms import (
    DetailsForm,
    NewUserForm,
    RoleForm,
    TemporaryPasswordForm,
)
from ids_audit.accounts.models import ReportAccess, Role, User
from ids_audit.accounts.permissions import LABELS
from ids_audit.accounts.roles import change_role, create_role, remove_role, set_access
from ids_audit.reports.models import Report


def fixed_admin_only(view):
    """Let only an active fixed administrator open the view. Anyone else gets
    403 (ENG-01 6.1).
    """

    @functools.wraps(view)
    def checked(request, *args, **kwargs):
        user = request.user
        if not (user.is_active and user.is_fixed_admin):
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return checked


def posted_id(request, name: str) -> int:
    """Return the id a form sent under name, or 404 for anything else."""
    value = request.POST.get(name, "")
    if not value.isdigit():
        raise Http404(f"No such {name}.")
    return int(value)


def refused(request, error: ValidationError) -> None:
    """Say why a change was refused, and that nothing changed."""
    messages.error(request, " ".join(error.messages) + " Nothing was changed.")


def attempt(request, change: Callable[[], object], done: str) -> bool:
    """Make a change, then say it was made, or why it was refused. Return
    whether it was made.
    """
    try:
        change()
    except ValidationError as error:
        refused(request, error)
        return False
    messages.success(request, done)
    return True


@fixed_admin_only
def home(request):
    """Links to the accounts, the roles and the audit log."""
    return render(request, "accounts/admin_home.html")


@fixed_admin_only
def users(request):
    """Every account, by username, with what an administrator needs to know
    at a glance.
    """
    accounts = User.objects.annotate(reports=Count("access")).order_by("username")
    rows = [
        {"account": account, "locked_until": services.locked_until(account.username)}
        for account in accounts
    ]
    return render(request, "accounts/users.html", {"rows": rows})


@fixed_admin_only
def new_user(request):
    """Create an account with a temporary password, then open its page."""
    form = NewUserForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            user = services.create_user(
                request.user,
                data["username"],
                data["full_name"],
                data["email"],
                data["password1"],
            )
        except ValidationError as error:
            for message in error.messages:
                form.add_error(None, message)
        else:
            messages.success(
                request,
                f"{user} is created. They choose their own password at their "
                "first sign-in.",
            )
            return redirect("admin_user", user.pk)
    return render(request, "accounts/new_user.html", {"form": form})


def user_page(request, account: User, details=None, password=None, status=200):
    """Render an account's page, with the forms given, or new ones."""
    access = {
        row.report_id: row.role_id for row in ReportAccess.objects.filter(user=account)
    }
    reports = [
        {"report": report, "role": access.get(report.pk)}
        for report in Report.objects.order_by(Lower("name"))
    ]
    return render(
        request,
        "accounts/user.html",
        {
            "account": account,
            "details": details
            or DetailsForm(
                initial={"full_name": account.full_name, "email": account.email}
            ),
            "password": password or TemporaryPasswordForm(),
            "reports": reports,
            "roles": Role.objects.order_by(Lower("name")),
            "locked_until": services.locked_until(account.username),
        },
        status=status,
    )


@fixed_admin_only
def user(request, user_id: int):
    """One account: its details, its password, whether it's enabled, and its
    role on each report.
    """
    return user_page(request, get_object_or_404(User, pk=user_id))


@fixed_admin_only
@require_POST
def user_details(request, user_id: int):
    """Correct an account's full name or email."""
    account = get_object_or_404(User, pk=user_id)
    form = DetailsForm(request.POST)
    if form.is_valid():
        try:
            services.change_user(
                request.user,
                account,
                form.cleaned_data["full_name"],
                form.cleaned_data["email"],
            )
        except ValidationError as error:
            refused(request, error)
        else:
            messages.success(request, f"{account.username}'s details are saved.")
            return redirect("admin_user", account.pk)
    return user_page(request, account, details=form)


@fixed_admin_only
@require_POST
def user_password(request, user_id: int):
    """Reset an account's password to a temporary one."""
    account = get_object_or_404(User, pk=user_id)
    form = TemporaryPasswordForm(request.POST)
    if form.is_valid():
        try:
            services.reset_password(
                request.user, account, form.cleaned_data["password1"]
            )
        except ValidationError as error:
            refused(request, error)
        else:
            messages.success(
                request,
                f"{account}'s password is reset. They choose their own at their "
                "next sign-in.",
            )
            return redirect("admin_user", account.pk)
    return user_page(request, account, password=form)


@fixed_admin_only
@require_POST
def user_disable(request, user_id: int):
    account = get_object_or_404(User, pk=user_id)
    attempt(
        request,
        lambda: services.disable_user(request.user, account),
        f"{account} is disabled, and can't sign in.",
    )
    return redirect("admin_user", account.pk)


@fixed_admin_only
@require_POST
def user_enable(request, user_id: int):
    account = get_object_or_404(User, pk=user_id)
    attempt(
        request,
        lambda: services.enable_user(request.user, account),
        f"{account} is enabled, and can sign in again.",
    )
    return redirect("admin_user", account.pk)


@fixed_admin_only
@require_POST
def user_unlock(request, user_id: int):
    account = get_object_or_404(User, pk=user_id)
    attempt(
        request,
        lambda: services.unlock(account.username, by=request.user),
        f"{account} is unlocked, and can sign in at once.",
    )
    return redirect("admin_user", account.pk)


@fixed_admin_only
@require_POST
def user_access(request, user_id: int):
    """Give an account a role on one report, change it, or take it away."""
    account = get_object_or_404(User, pk=user_id)
    report = get_object_or_404(Report, pk=posted_id(request, "report"))
    chosen = request.POST.get("role") or ""
    role = get_object_or_404(Role, pk=posted_id(request, "role")) if chosen else None
    attempt(
        request,
        lambda: set_access(request.user, account, report, role),
        f"{account} has {'no access' if role is None else role.name} on {report}.",
    )
    return redirect("admin_user", account.pk)


@fixed_admin_only
def roles(request):
    """Every role, with its permissions and how many times it's given."""
    return render(
        request,
        "accounts/roles.html",
        {
            "roles": Role.objects.annotate(given=Count("access")).order_by(
                Lower("name")
            ),
            "labels": LABELS,
        },
    )


def role_form_page(request, form: RoleForm, role: Role | None):
    return render(
        request,
        "accounts/role.html",
        {"form": form, "role": role, "given": role.access.count() if role else 0},
    )


@fixed_admin_only
def new_role(request):
    """Create a role, then go back to the roles."""
    form = RoleForm(request.POST or None, initial={"permissions": ["view"]})
    if request.method == "POST" and form.is_valid():
        try:
            role = create_role(
                request.user,
                form.cleaned_data["name"],
                form.cleaned_data["permissions"],
            )
        except ValidationError as error:
            for message in error.messages:
                form.add_error(None, message)
        else:
            messages.success(request, f"The role {role} is created.")
            return redirect("admin_roles")
    return role_form_page(request, form, None)


@fixed_admin_only
def role(request, role_id: int):
    """Change a role's name or permissions."""
    found = get_object_or_404(Role, pk=role_id)
    form = RoleForm(
        request.POST or None,
        initial={"name": found.name, "permissions": found.permissions},
    )
    if request.method == "POST" and form.is_valid():
        try:
            change_role(
                request.user,
                found,
                form.cleaned_data["name"],
                form.cleaned_data["permissions"],
            )
        except ValidationError as error:
            for message in error.messages:
                form.add_error(None, message)
        else:
            messages.success(request, f"The role {form.cleaned_data['name']} is saved.")
            return redirect("admin_roles")
    return role_form_page(request, form, found)


@fixed_admin_only
@require_POST
def role_remove(request, role_id: int):
    """Remove a role, refused while it's given to anyone."""
    found = get_object_or_404(Role, pk=role_id)
    if attempt(
        request,
        lambda: remove_role(request.user, found),
        f"The role {found} is removed.",
    ):
        return redirect("admin_roles")
    return redirect("admin_role", found.pk)
