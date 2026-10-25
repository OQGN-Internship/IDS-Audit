"""The checks every request passes before its view: a signed-in user, who has
chosen their own password (ENG-01 6.1).
"""

from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth.middleware import LoginRequiredMiddleware
from django.contrib.auth.views import redirect_to_login
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, resolve_url
from django.urls import reverse
from django.utils.cache import add_never_cache_headers


def is_htmx(request: HttpRequest) -> bool:
    """Return whether HTMX sent the request, for a part of a page."""
    return request.headers.get("HX-Request") == "true"


def page_redirect(request: HttpRequest, url: str) -> HttpResponse:
    """Send the browser to url. An HTMX request gets an HX-Redirect header in
    place of a redirect, so HTMX moves the whole page and the page doesn't land
    inside a panel (ENG-01 6.1).
    """
    if is_htmx(request):
        return HttpResponse(headers={"HX-Redirect": url})
    return redirect(url)


class SignInRequired(LoginRequiredMiddleware):
    """Send anyone who isn't signed in to the sign-in page, from every address
    except the pages marked login_not_required, such as the sign-in page.
    """

    def handle_no_permission(self, request, view_func):
        if not is_htmx(request):
            return super().handle_no_permission(request, view_func)
        # Back to the page the panel is on, not to the panel.
        page = urlsplit(request.headers.get("HX-Current-URL", ""))
        back = page.path + (f"?{page.query}" if page.query else "")
        to_sign_in = redirect_to_login(back or "/", resolve_url(settings.LOGIN_URL))
        return page_redirect(request, to_sign_in.url)


class PasswordChangeRequired:
    """Until a user replaces a temporary password, send every request except
    the change-password page and signing out to the change-password page.
    """

    OPEN = {"password_change", "sign_out"}

    def __init__(self, get_response) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        user = request.user
        if not (user.is_authenticated and user.must_change_password):
            return None
        if request.resolver_match.url_name in self.OPEN:
            return None
        return page_redirect(request, reverse("password_change"))


class NoStore:
    """Tell the browser to keep no copy of any page, so a report can't be
    shown again from the browser's cache once its user has signed out
    (ENG-01 6.4).
    """

    def __init__(self, get_response) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        add_never_cache_headers(response)
        return response
