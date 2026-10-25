"""Signing in, the lock-out, idle sessions and the forced password change
(ENG-01 6.1), with the audit events each writes (6.3).
"""

import datetime
import json

import pytest
from django.test import Client
from django.urls import reverse

from ids_audit.audit.models import AuditEvent
from tests.factories import PASSWORD, make_admin

pytestmark = pytest.mark.django_db

Action = AuditEvent.Action
# 10:00 in Muscat.
START = datetime.datetime(2026, 10, 25, 6, 0, tzinfo=datetime.UTC)
HTMX = {"HX-Request": "true"}
NEW_PASSWORD = "quiet-river-lantern-27"


def sign_in(client: Client, username: str, password: str = PASSWORD):
    """Send the sign-in form."""
    return client.post(reverse("sign_in"), {"username": username, "password": password})


def signs_in(username: str, password: str = PASSWORD) -> bool:
    """Return whether the username and password sign in, from a new browser."""
    return sign_in(Client(), username, password).status_code == 302


def signed_in(client: Client) -> bool:
    """Return whether the client is signed in: the change-password page, open
    to anyone signed in, opens.
    """
    return client.get(reverse("password_change")).status_code == 200


def fail(client: Client, username: str, times: int = 1) -> None:
    """Send a wrong password for the username, so many times."""
    for _ in range(times):
        sign_in(client, username, "not-the-password")


def actions(**filters) -> list[str]:
    """Return the actions of the events matching the filters, in order."""
    events = AuditEvent.objects.filter(**filters).order_by("pk")
    return list(events.values_list("action", flat=True))


def test_every_address_but_sign_in_sends_a_visitor_to_sign_in() -> None:
    """Verify a visitor who isn't signed in is sent to the sign-in page, and
    back to the page asked for once signed in.
    """
    client = Client()
    assert client.get(reverse("sign_in")).status_code == 200
    response = client.get(reverse("password_change"))
    assert response.status_code == 302
    assert response["Location"] == "/sign-in?next=/password"

    admin = make_admin()
    response = client.post(
        "/sign-in?next=/password",
        {"username": admin.username, "password": PASSWORD, "next": "/password"},
    )
    assert response["Location"] == "/password"


def test_an_htmx_request_without_sign_in_moves_the_whole_page() -> None:
    """Verify a panel request from a visitor who isn't signed in gets an
    HX-Redirect to the sign-in page, back to the page the panel is on.
    """
    response = Client().get(
        reverse("home"),
        headers={**HTMX, "HX-Current-URL": "http://testserver/password?x=1"},
    )
    assert response.status_code == 200
    assert response["HX-Redirect"] == "/sign-in?next=/password%3Fx%3D1"
    assert "Location" not in response


def test_the_right_password_signs_in_and_writes_sign_in() -> None:
    """Verify signing in opens the home page and writes one sign_in event."""
    admin = make_admin()
    client = Client()
    response = sign_in(client, admin.username)
    assert response["Location"] == reverse("home")
    assert signed_in(client)
    assert actions(user=admin) == [Action.SIGN_IN]


def test_a_wrong_password_writes_the_username_tried_and_never_the_password() -> None:
    """Verify a failed sign-in writes sign_in_failed with the username tried,
    linked to the account it names, or to none for an unknown username.
    """
    admin = make_admin()
    client = Client()
    assert sign_in(client, admin.username, "not-the-password").status_code == 200
    sign_in(client, "nobody", "not-the-password")

    known, unknown = AuditEvent.objects.filter(action=Action.SIGN_IN_FAILED).order_by(
        "pk"
    )
    assert (known.user, known.details) == (admin, {"username": admin.username})
    assert (unknown.user, unknown.details) == (None, {"username": "nobody"})
    assert "not-the-password" not in json.dumps([known.details, unknown.details])
    assert not signed_in(client)


def test_five_wrong_passwords_lock_the_account_for_15_minutes(time_machine) -> None:
    """Verify the fifth wrong password locks the account, the page says until
    when, the right password is refused until 15 minutes have passed, and
    then signs in.
    """
    admin = make_admin()
    time_machine.move_to(START, tick=False)
    client = Client()
    fail(client, admin.username, 4)
    response = sign_in(client, admin.username, "not-the-password")
    assert response.status_code == 429
    assert "locked after 5 failed sign-ins, until 10:15" in response.content.decode()

    time_machine.shift(datetime.timedelta(minutes=14, seconds=59))
    assert sign_in(Client(), admin.username).status_code == 429
    time_machine.shift(datetime.timedelta(seconds=2))
    assert signs_in(admin.username)


def test_the_lock_is_written_once_and_more_tries_dont_extend_it(
    time_machine,
) -> None:
    """Verify five failures write five sign_in_failed events and one
    account_locked, and a sixth try during the lock is refused and written
    as a failure only, without moving the end of the lock.
    """
    admin = make_admin()
    time_machine.move_to(START, tick=False)
    fail(Client(), admin.username, 5)
    assert actions(action=Action.ACCOUNT_LOCKED, user=admin) == [Action.ACCOUNT_LOCKED]
    locked = AuditEvent.objects.get(action=Action.ACCOUNT_LOCKED)
    assert locked.details == {"username": admin.username}

    time_machine.shift(datetime.timedelta(minutes=10))
    response = sign_in(Client(), admin.username, "not-the-password")
    assert response.status_code == 429
    assert "until 10:15" in response.content.decode()
    assert actions(user=admin).count(Action.SIGN_IN_FAILED) == 6
    assert actions(user=admin).count(Action.ACCOUNT_LOCKED) == 1

    time_machine.shift(datetime.timedelta(minutes=5, seconds=1))
    assert signs_in(admin.username)


def test_a_right_password_between_failures_starts_the_count_again() -> None:
    """Verify four wrong passwords, a right one, then four more wrong ones
    don't lock the account.
    """
    admin = make_admin()
    fail(Client(), admin.username, 4)
    assert signs_in(admin.username)
    fail(Client(), admin.username, 4)
    assert signs_in(admin.username)
    assert Action.ACCOUNT_LOCKED not in actions()


def test_a_locked_username_doesnt_lock_anyone_else() -> None:
    """Verify five wrong passwords for one username don't stop another user
    signing in from the same machine.
    """
    locked, other = make_admin(), make_admin()
    fail(Client(), locked.username, 5)
    assert not signs_in(locked.username)
    assert signs_in(other.username)


def test_a_session_ends_after_30_minutes_without_a_request(time_machine) -> None:
    """Verify a session left alone for 31 minutes is signed out: a page goes
    to the sign-in page, and an HTMX request gets an HX-Redirect to it.
    """
    time_machine.move_to(START, tick=False)
    client = Client()
    client.force_login(make_admin())
    time_machine.shift(datetime.timedelta(minutes=31))
    response = client.get(reverse("home"), headers=HTMX)
    assert response["HX-Redirect"].startswith("/sign-in?next=")
    assert client.get(reverse("home"))["Location"] == "/sign-in?next=/"


def test_each_request_starts_the_30_minutes_again(time_machine) -> None:
    """Verify a session with a request every 29 minutes stays signed in."""
    time_machine.move_to(START, tick=False)
    client = Client()
    client.force_login(make_admin())
    for _ in range(3):
        time_machine.shift(datetime.timedelta(minutes=29))
        assert signed_in(client)


def test_the_session_cookie_is_http_only_lax_and_lasts_30_minutes() -> None:
    """Verify the session cookie can't be read by scripts or sent from other
    sites, and lasts the 30 minutes.
    """
    response = sign_in(Client(), make_admin().username)
    cookie = response.cookies["sessionid"]
    assert cookie["httponly"] is True
    assert cookie["samesite"] == "Lax"
    assert cookie["max-age"] == 1800


def change_password(client: Client, new: str, old: str = PASSWORD):
    """Send the change-password form."""
    return client.post(
        reverse("password_change"),
        {"old_password": old, "new_password1": new, "new_password2": new},
    )


def test_a_temporary_password_opens_only_the_change_page_and_sign_out() -> None:
    """Verify a user who must change their password is sent to the change
    page from any other page, by HX-Redirect for an HTMX request, and can
    still sign out.
    """
    client = Client()
    client.force_login(make_admin(must_change_password=True))
    assert client.get(reverse("home"))["Location"] == reverse("password_change")
    response = client.get(reverse("home"), headers=HTMX)
    assert response["HX-Redirect"] == reverse("password_change")
    assert client.get(reverse("password_change")).status_code == 200
    assert client.post(reverse("sign_out"))["Location"] == reverse("sign_in")


def test_an_11_character_password_is_refused() -> None:
    """Verify the change page refuses a new password one character short, and
    the user must still change it.
    """
    admin = make_admin(must_change_password=True)
    client = Client()
    client.force_login(admin)
    response = change_password(client, "Tulip-harb1")
    assert response.status_code == 200
    assert "at least 12 characters" in response.content.decode()
    admin.refresh_from_db()
    assert admin.must_change_password
    assert admin.check_password(PASSWORD)


def test_changing_the_password_opens_every_page_and_writes_password_changed() -> None:
    """Verify a changed password is hashed with Argon2, keeps the user signed
    in, stops the redirect, and writes one password_changed event.
    """
    admin = make_admin(must_change_password=True)
    client = Client()
    client.force_login(admin)
    assert change_password(client, NEW_PASSWORD)["Location"] == reverse("home")

    admin.refresh_from_db()
    assert not admin.must_change_password
    assert admin.check_password(NEW_PASSWORD)
    assert admin.password.startswith("argon2")
    assert client.get(reverse("home")).get("Location") != reverse("password_change")
    assert signed_in(client)
    assert actions(action=Action.PASSWORD_CHANGED, user=admin) == [
        Action.PASSWORD_CHANGED
    ]


def test_signing_out_ends_the_session_and_writes_sign_out() -> None:
    """Verify signing out needs a POST, ends the session, and writes one
    sign_out event.
    """
    admin = make_admin()
    client = Client()
    client.force_login(admin)
    assert client.get(reverse("sign_out")).status_code == 405
    assert client.post(reverse("sign_out"))["Location"] == reverse("sign_in")
    assert not signed_in(client)
    assert actions(user=admin) == [Action.SIGN_IN, Action.SIGN_OUT]
