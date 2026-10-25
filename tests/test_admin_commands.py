"""The fixed administrators' commands, and what the application refuses to do
to a fixed administrator (ENG-01 6.1), with the events each writes (6.3).
"""

import io

import pytest
from django.core.management import CommandError, call_command
from django.test import Client
from django.urls import reverse

from ids_audit.accounts.models import FixedAdminProtected, User
from ids_audit.audit.models import AuditEvent
from tests.factories import PASSWORD, make_admin, make_user

pytestmark = pytest.mark.django_db

Action = AuditEvent.Action
INITIAL = "amber-kettle-meadow-48"


@pytest.fixture
def typed(monkeypatch):
    """Answer the commands' questions: the given text for each input(), and
    the given passwords for each getpass(), in order. Returns the questions
    asked.
    """
    asked = []

    def answer(*, text=(), passwords=()):
        texts, secrets = iter(text), iter(passwords)

        def ask(answers):
            def question(prompt=""):
                asked.append(prompt)
                return next(answers)

            return question

        monkeypatch.setattr("builtins.input", ask(texts))
        monkeypatch.setattr("getpass.getpass", ask(secrets))
        return asked

    return answer


def run(*args) -> str:
    """Run a manage.py command and return what it printed."""
    out = io.StringIO()
    call_command(*args, stdout=out)
    return out.getvalue()


def signs_in(username: str, password: str) -> bool:
    """Return whether the username and password sign in, from a new browser."""
    response = Client().post(
        reverse("sign_in"), {"username": username, "password": password}
    )
    return response.status_code == 302


def test_create_admin_makes_a_fixed_admin_who_must_change_the_password(typed) -> None:
    """Verify create_admin asks for the full name and the password twice,
    then creates a fixed administrator whose first sign-in goes to the change
    page, and writes user_created as a command-line action.
    """
    typed(text=["Salim bin Rashid Al Harthy"], passwords=[INITIAL, INITIAL])
    assert "Salim bin Rashid Al Harthy (salim)" in run("create_admin", "salim")

    user = User.objects.get(username="salim")
    assert (user.full_name, user.is_fixed_admin, user.must_change_password) == (
        "Salim bin Rashid Al Harthy",
        True,
        True,
    )
    assert user.password.startswith("argon2")
    event = AuditEvent.objects.get(action=Action.USER_CREATED)
    assert event.user is None
    assert event.details == {
        "username": "salim",
        "full_name": "Salim bin Rashid Al Harthy",
        "is_fixed_admin": True,
        "command": "create_admin",
    }

    client = Client()
    client.post(reverse("sign_in"), {"username": "salim", "password": INITIAL})
    assert client.get(reverse("home"))["Location"] == reverse("password_change")


def test_create_admin_refuses_a_taken_username_before_asking_anything(typed) -> None:
    """Verify create_admin refuses a username that exists, without asking for
    a name or a password.
    """
    existing = make_user(fixed_admin=False)
    asked = typed()
    with pytest.raises(CommandError, match="already taken"):
        run("create_admin", existing.username)
    assert asked == []


@pytest.mark.parametrize(
    ("full_name", "passwords", "message"),
    [
        ("   ", [INITIAL, INITIAL], "cannot be blank"),
        ("Salim", ["Tulip-harb1", "Tulip-harb1"], "at least 12 characters"),
        ("Salim", [INITIAL, INITIAL + "x"], "differ"),
    ],
    ids=["empty full name", "11 characters", "passwords differ"],
)
def test_create_admin_refuses_and_creates_nothing(
    typed, full_name, passwords, message
) -> None:
    """Verify create_admin refuses an empty full name, an 11-character
    password and two passwords that differ, creating no account and no event.
    """
    typed(text=[full_name], passwords=passwords)
    with pytest.raises(CommandError, match=message):
        run("create_admin", "salim")
    assert not User.objects.filter(username="salim").exists()
    assert not AuditEvent.objects.exists()


def test_disable_admin_stops_sign_in_and_list_admins_shows_it() -> None:
    """Verify a disabled administrator can't sign in, is listed as disabled,
    and the change is written as user_disabled from the command line.
    """
    admin, other = make_admin(), make_admin()
    assert f"Disabled {admin}" in run("disable_admin", admin.username)
    assert not signs_in(admin.username, PASSWORD)
    assert signs_in(other.username, PASSWORD)

    listed = run("list_admins").splitlines()
    assert f"{admin.username}\t{admin.full_name}\tdisabled" in listed
    assert f"{other.username}\t{other.full_name}\tactive" in listed
    event = AuditEvent.objects.get(action=Action.USER_DISABLED)
    assert event.user is None
    assert event.details == {"username": admin.username, "command": "disable_admin"}


def test_disable_admin_refuses_anyone_else_and_a_disabled_admin() -> None:
    """Verify disable_admin refuses a user who isn't a fixed administrator,
    and one already disabled, writing nothing.
    """
    with pytest.raises(CommandError, match="No fixed administrator"):
        run("disable_admin", make_user(fixed_admin=False).username)
    admin = make_admin()
    run("disable_admin", admin.username)
    with pytest.raises(CommandError, match="already disabled"):
        run("disable_admin", admin.username)
    assert AuditEvent.objects.filter(action=Action.USER_DISABLED).count() == 1


def test_list_admins_lists_only_fixed_administrators() -> None:
    """Verify list_admins shows each fixed administrator and no one else."""
    admin, user = make_admin(), make_user(fixed_admin=False)
    out = run("list_admins")
    assert admin.username in out
    assert user.username not in out


def test_a_fixed_admin_cant_be_deleted_disabled_or_demoted_from_the_app() -> None:
    """Verify the model refuses to delete or disable a fixed administrator, or
    take away the flag, and still saves any other change.
    """
    admin = make_admin()
    with pytest.raises(FixedAdminProtected):
        admin.delete()
    for change in ({"is_active": False}, {"is_fixed_admin": False}):
        stored = User.objects.get(pk=admin.pk)
        for field, value in change.items():
            setattr(stored, field, value)
        with pytest.raises(FixedAdminProtected):
            stored.save()
    admin.refresh_from_db()
    assert admin.is_active and admin.is_fixed_admin

    admin.full_name = "Corrected Name"
    admin.save()
    user = make_user(fixed_admin=False)
    user.is_active = False
    user.save()
    assert not User.objects.get(pk=user.pk).is_active


def test_changepassword_is_a_reset_the_user_must_follow(typed) -> None:
    """Verify the project's changepassword sets the password, makes the user
    choose their own at the next sign-in, and writes password_reset.
    """
    admin = make_admin()
    typed(passwords=[INITIAL, INITIAL])
    run("changepassword", admin.username)

    admin.refresh_from_db()
    assert admin.check_password(INITIAL)
    assert admin.must_change_password
    event = AuditEvent.objects.get(action=Action.PASSWORD_RESET)
    assert event.user is None
    assert event.details == {"username": admin.username, "command": "changepassword"}


def test_axes_reset_username_unlocks_at_once_and_writes_account_unlocked() -> None:
    """Verify the project's axes_reset_username lets a locked account sign in
    straight away and writes account_unlocked, and writes nothing for a
    username with no failed sign-ins.
    """
    admin, other = make_admin(), make_admin()
    for _ in range(5):
        signs_in(admin.username, "not-the-password")
    assert not signs_in(admin.username, PASSWORD)

    out = run("axes_reset_username", admin.username, other.username)
    assert f"Unlocked {admin.username}" in out
    assert signs_in(admin.username, PASSWORD)
    event = AuditEvent.objects.get(action=Action.ACCOUNT_UNLOCKED)
    assert event.user is None
    assert event.details == {
        "username": admin.username,
        "command": "axes_reset_username",
    }
