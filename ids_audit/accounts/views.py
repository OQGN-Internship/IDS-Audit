"""Signing in and out, and choosing a password (ENG-01 6.1)."""

from axes.models import AccessAttempt
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.forms import AuthenticationForm
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils import timezone

from ids_audit.accounts.services import password_was_changed


class SignIn(auth_views.LoginView):
    """The sign-in page, the one page open to someone who isn't signed in."""

    template_name = "accounts/sign_in.html"
    redirect_authenticated_user = True


class SignOut(auth_views.LogoutView):
    """Signing out, which only a form's POST can do."""


class PasswordChange(auth_views.PasswordChangeView):
    """Choosing a new password: the temporary one, then the new one twice."""

    template_name = "accounts/password_change.html"
    success_url = reverse_lazy("home")

    def form_valid(self, form):
        with transaction.atomic():
            # Saves the password, and keeps the user signed in with it.
            response = super().form_valid(form)
            password_was_changed(form.user)
        messages.success(self.request, "Your password is changed.")
        return response


def locked_out(request, original_response=None, credentials=None):
    """The sign-in page for a sign-in the lock-out refused, saying when the
    lock ends (ENG-01 6.1). django-axes calls this in place of the page it
    would show.
    """
    username = (credentials or {}).get("username") or ""
    attempt = (
        AccessAttempt.objects.filter(username=username)
        .order_by("-attempt_time")
        .first()
    )
    # The lock lasts from the failure that started it (AXES_RESET_COOL_OFF_
    # ON_FAILURE_DURING_LOCKOUT is off), and that failure's time is kept.
    ends = timezone.localtime(
        (attempt.attempt_time if attempt else timezone.now())
        + settings.AXES_COOLOFF_TIME
    )
    context = {
        "form": AuthenticationForm(request, initial={"username": username}),
        "locked": (
            f"This account is locked after {settings.LOCKOUT_ATTEMPTS} failed "
            f"sign-ins, until {ends:%H:%M}. Try again then, or ask an "
            "administrator to unlock it."
        ),
    }
    return render(request, "accounts/sign_in.html", context, status=429)


def home(request):
    """The page a user lands on once signed in: the report list."""
    return redirect("reports")
