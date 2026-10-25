"""The administration screens at /admin/ (ENG-01 6.1): accounts, passwords,
roles and report access, each change with its one event (ENG-01 6.3).
"""

import pytest
from django.core.management import CommandError, call_command
from django.test import Client
from django.urls import reverse

from ids_audit.accounts import services
from ids_audit.accounts.models import ReportAccess, Role, User
from ids_audit.audit.models import AuditEvent
from tests.factories import PASSWORD, grant, make_admin, make_report, make_user

pytestmark = pytest.mark.django_db

TEMPORARY = "brisk-falcon-meadow-48"
OWN = "quiet-river-lantern-27"


@pytest.fixture
def admin() -> User:
    return make_admin()


@pytest.fixture
def client(admin) -> Client:
    """A browser signed in as a fixed administrator."""
    client = Client()
    client.force_login(admin)
    return client


def a_user() -> User:
    """A user who isn't a fixed administrator, signing in with PASSWORD."""
    user = make_user(fixed_admin=False)
    user.set_password(PASSWORD)
    user.save()
    return user


def sign_in(client: Client, username: str, password: str):
    return client.post(reverse("sign_in"), {"username": username, "password": password})


def signs_in(username: str, password: str) -> bool:
    """Return whether the username and password sign in, from a new browser."""
    return sign_in(Client(), username, password).status_code == 302


def only_event(action: str) -> AuditEvent:
    (event,) = AuditEvent.objects.filter(action=action)
    return event


def said(response) -> str:
    """The messages shown on the page a redirect leads to."""
    return " ".join(str(m) for m in response.wsgi_request._messages)


def addresses(user: User, role: Role) -> list[tuple[str, str]]:
    """Every address of the administration screens, with its method."""
    return [
        ("get", reverse("admin_home")),
        ("get", reverse("admin_users")),
        ("get", reverse("admin_new_user")),
        ("post", reverse("admin_new_user")),
        ("get", reverse("admin_user", args=[user.pk])),
        ("post", reverse("admin_user_details", args=[user.pk])),
        ("post", reverse("admin_user_password", args=[user.pk])),
        ("post", reverse("admin_user_disable", args=[user.pk])),
        ("post", reverse("admin_user_enable", args=[user.pk])),
        ("post", reverse("admin_user_unlock", args=[user.pk])),
        ("post", reverse("admin_user_access", args=[user.pk])),
        ("get", reverse("admin_roles")),
        ("get", reverse("admin_new_role")),
        ("post", reverse("admin_new_role")),
        ("get", reverse("admin_role", args=[role.pk])),
        ("post", reverse("admin_role", args=[role.pk])),
        ("post", reverse("admin_role_remove", args=[role.pk])),
    ]


def test_only_a_fixed_admin_opens_the_administration_screens(client) -> None:
    """Verify every address under /admin/ is 403 for a user who isn't a fixed
    administrator, even one with a role on every report, and opens for a
    fixed administrator.
    """
    user, role = a_user(), Role.objects.get(name="Approver")
    grant(user, make_report(), role)
    other = Client()
    other.force_login(user)
    for method, url in addresses(user, role):
        assert getattr(other, method)(url).status_code == 403, url
    for method, url in addresses(user, role):
        if method == "get":
            assert client.get(url).status_code == 200, url
    assert not AuditEvent.objects.exclude(action="sign_in").exists()


def test_the_header_links_administration_only_for_a_fixed_admin(client) -> None:
    """Verify the base template links the administration screens for a fixed
    administrator, and not for anyone else.
    """
    link = f'href="{reverse("admin_home")}"'
    assert link in client.get(reverse("reports")).content.decode()
    other = Client()
    other.force_login(a_user())
    assert link not in other.get(reverse("reports")).content.decode()


def test_a_new_user_must_choose_a_password_at_first_sign_in(client, admin):
    """Verify an administrator creates a user, which writes user_created by
    the administrator, and the user's first sign-in leads only to the
    change-password page.
    """
    response = client.post(
        reverse("admin_new_user"),
        {
            "username": "salim",
            "full_name": "Salim bin Nasser Al Hinai",
            "email": "",
            "password1": TEMPORARY,
            "password2": TEMPORARY,
        },
    )
    user = User.objects.get(username="salim")
    assert response["Location"] == reverse("admin_user", args=[user.pk])
    assert not user.is_fixed_admin and user.must_change_password
    event = only_event("user_created")
    assert event.user == admin
    assert event.details == {
        "username": "salim",
        "full_name": "Salim bin Nasser Al Hinai",
        "is_fixed_admin": False,
    }
    browser = Client()
    assert sign_in(browser, "salim", TEMPORARY).status_code == 302
    assert browser.get(reverse("reports"))["Location"] == reverse("password_change")


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"password1": "short-one-1"}, "too short"),
        ({"password2": "something-else-entirely-9"}, "The two passwords differ."),
        ({"username": "taken"}, "already exists"),
        ({"full_name": " "}, "This field is required."),
    ],
)
def test_a_new_user_that_breaks_a_rule_is_refused(client, changes, message) -> None:
    """Verify a short password, two different passwords, a username already
    taken or no full name are refused with a message, and nothing is saved.
    """
    User.objects.create(username="taken", full_name="Taken")
    data = {
        "username": "new",
        "full_name": "New User",
        "email": "",
        "password1": TEMPORARY,
        "password2": TEMPORARY,
        **changes,
    }
    if changes.get("password1"):
        data["password2"] = changes["password1"]
    response = client.post(reverse("admin_new_user"), data)
    assert response.status_code == 200
    assert message in response.content.decode()
    assert not User.objects.filter(username=data["username"], full_name="New User")
    assert not AuditEvent.objects.filter(action="user_created").exists()


def test_details_are_corrected_with_user_changed(client, admin) -> None:
    """Verify correcting a full name writes user_changed with the value before
    and after, and saving the same details writes nothing.
    """
    user = a_user()
    before = user.full_name
    url = reverse("admin_user_details", args=[user.pk])
    client.post(url, {"full_name": before, "email": ""})
    assert not AuditEvent.objects.filter(action="user_changed").exists()
    response = client.post(url, {"full_name": "Aisha Al Habsi", "email": ""})
    assert response["Location"] == reverse("admin_user", args=[user.pk])
    user.refresh_from_db()
    assert user.full_name == "Aisha Al Habsi"
    event = only_event("user_changed")
    assert event.user == admin
    assert event.details == {
        "username": user.username,
        "changed": {"full_name": {"before": before, "after": "Aisha Al Habsi"}},
    }


def test_a_reset_password_ends_the_session_and_must_be_changed(client, admin):
    """Verify a reset writes password_reset, signs the user out, and makes
    them choose their own password at the next sign-in.
    """
    user = a_user()
    theirs = Client()
    sign_in(theirs, user.username, PASSWORD)
    response = client.post(
        reverse("admin_user_password", args=[user.pk]),
        {"password1": TEMPORARY, "password2": TEMPORARY},
    )
    assert response.status_code == 302
    assert only_event("password_reset").details == {"username": user.username}
    assert only_event("password_reset").user == admin
    assert theirs.get(reverse("reports"))["Location"].startswith(reverse("sign_in"))
    assert not signs_in(user.username, PASSWORD)
    browser = Client()
    sign_in(browser, user.username, TEMPORARY)
    assert browser.get(reverse("reports"))["Location"] == reverse("password_change")


def test_a_reset_to_a_weak_password_is_refused(client) -> None:
    """Verify a temporary password the validators refuse changes nothing."""
    user = a_user()
    response = client.post(
        reverse("admin_user_password", args=[user.pk]),
        {"password1": "123456789012", "password2": "123456789012"},
    )
    assert response.status_code == 200
    assert "entirely numeric" in response.content.decode()
    assert signs_in(user.username, PASSWORD)
    assert not AuditEvent.objects.filter(action="password_reset").exists()


def test_a_disabled_account_can_t_sign_in_until_enabled(client, admin) -> None:
    """Verify disabling writes user_disabled and signs the user out at their
    next request, they can't sign in, and enabling writes user_changed and
    lets them in again.
    """
    user = a_user()
    theirs = Client()
    sign_in(theirs, user.username, PASSWORD)
    client.post(reverse("admin_user_disable", args=[user.pk]))
    assert only_event("user_disabled").details == {"username": user.username}
    assert theirs.get(reverse("reports"))["Location"].startswith(reverse("sign_in"))
    assert not signs_in(user.username, PASSWORD)
    client.post(reverse("admin_user_enable", args=[user.pk]))
    event = only_event("user_changed")
    assert event.user == admin
    assert event.details == {
        "username": user.username,
        "changed": {"is_active": {"before": False, "after": True}},
    }
    assert signs_in(user.username, PASSWORD)


def test_unlocking_from_the_page_lets_the_user_in_at_once(client, admin) -> None:
    """Verify a locked-out user's page says so and offers Unlock, which writes
    account_unlocked by the administrator and lets them sign in at once.
    """
    user = a_user()
    for _ in range(5):
        sign_in(Client(), user.username, "not-the-password")
    assert not signs_in(user.username, PASSWORD)
    page = client.get(reverse("admin_user", args=[user.pk])).content.decode()
    assert "Locked out" in page and f"Unlock {user.username}" in page
    client.post(reverse("admin_user_unlock", args=[user.pk]))
    event = only_event("account_unlocked")
    assert (event.user, event.details) == (admin, {"username": user.username})
    assert signs_in(user.username, PASSWORD)
    page = client.get(reverse("admin_user", args=[user.pk])).content.decode()
    assert "Locked out" not in page


def test_a_role_is_given_changed_and_taken_away_from_the_user_page(client):
    """Verify each change on the user's page writes its event, and a report or
    role that doesn't exist is 404.
    """
    user, report = a_user(), make_report()
    url = reverse("admin_user_access", args=[user.pk])
    viewer, approver = (
        Role.objects.get(name="Viewer"),
        Role.objects.get(name="Approver"),
    )
    client.post(url, {"report": report.pk, "role": viewer.pk})
    assert ReportAccess.objects.get(user=user, report=report).role == viewer
    client.post(url, {"report": report.pk, "role": approver.pk})
    assert ReportAccess.objects.get(user=user, report=report).role == approver
    page = client.get(reverse("admin_user", args=[user.pk])).content.decode()
    assert f'<option value="{approver.pk}" selected>Approver</option>' in page
    client.post(url, {"report": report.pk, "role": ""})
    assert not ReportAccess.objects.exists()
    actions = AuditEvent.objects.filter(report=report).order_by("id")
    assert list(actions.values_list("action", flat=True)) == [
        "access_granted",
        "access_changed",
        "access_removed",
    ]
    assert client.post(url, {"report": report.pk + 99, "role": ""}).status_code == 404
    assert client.post(url, {"report": "x", "role": ""}).status_code == 404
    assert client.post(url, {"report": report.pk, "role": "99"}).status_code == 404


def test_roles_are_created_changed_and_removed_from_their_pages(client) -> None:
    """Verify a role is created, changed and removed from its pages, each
    writing its event, and removing one still given is refused.
    """
    client.post(
        reverse("admin_new_role"),
        {"name": "Auditor", "permissions": ["view", "view_audit_log"]},
    )
    role = Role.objects.get(name="Auditor")
    assert role.permissions == ["view", "view_audit_log"]
    client.post(
        reverse("admin_role", args=[role.pk]),
        {"name": "Auditor", "permissions": ["view", "export", "view_audit_log"]},
    )
    role.refresh_from_db()
    assert role.permissions == ["view", "export", "view_audit_log"]
    grant(a_user(), make_report(), role)
    response = client.post(reverse("admin_role_remove", args=[role.pk]))
    assert "still given 1 time" in said(response)
    ReportAccess.objects.all().delete()
    client.post(reverse("admin_role_remove", args=[role.pk]))
    assert not Role.objects.filter(name="Auditor").exists()
    # Signing the administrator's browser in wrote sign_in.
    changes = AuditEvent.objects.exclude(action="sign_in").order_by("id")
    assert list(changes.values_list("action", flat=True)) == [
        "role_created",
        "role_changed",
        "role_removed",
    ]


def test_a_role_without_view_is_refused_on_its_page(client) -> None:
    """Verify the role form shows the rule a role breaks, and saves nothing."""
    response = client.post(
        reverse("admin_new_role"), {"name": "Exporter", "permissions": ["export"]}
    )
    assert response.status_code == 200
    assert "Every role holds View" in response.content.decode()
    assert not Role.objects.filter(name="Exporter").exists()


def test_a_fixed_admin_s_page_has_no_form_and_can_t_be_changed(client) -> None:
    """Verify another fixed administrator's page offers no form, and each
    change sent to it anyway, an unlock included, is refused with nothing
    changed or recorded.
    """
    other = make_admin()
    for _ in range(5):
        sign_in(Client(), other.username, "not-the-password")
    before = AuditEvent.objects.latest("id").pk
    page = client.get(reverse("admin_user", args=[other.pk])).content.decode()
    assert f"/admin/users/{other.pk}/" not in page
    report, role = make_report(), Role.objects.get(name="Viewer")
    sends = {
        "admin_user_details": {"full_name": "Changed", "email": ""},
        "admin_user_password": {"password1": TEMPORARY, "password2": TEMPORARY},
        "admin_user_disable": {},
        "admin_user_unlock": {},
        "admin_user_access": {"report": report.pk, "role": role.pk},
    }
    for name, data in sends.items():
        client.post(reverse(name, args=[other.pk]), data)
    other.refresh_from_db()
    assert other.is_active and other.full_name != "Changed"
    assert not other.must_change_password
    assert services.locked_until(other.username) is not None
    assert not ReportAccess.objects.exists()
    assert not AuditEvent.objects.filter(pk__gt=before).exists()


def test_the_temporary_password_can_t_be_chosen_again() -> None:
    """Verify the change-password page refuses the password it replaces, and
    the user still has to choose one (ENG-01 6.1).
    """
    user = make_admin(must_change_password=True)
    browser = Client()
    browser.force_login(user)
    response = browser.post(
        reverse("password_change"),
        {
            "old_password": PASSWORD,
            "new_password1": PASSWORD,
            "new_password2": PASSWORD,
        },
    )
    assert response.status_code == 200
    assert "different from the current one" in response.content.decode()
    user.refresh_from_db()
    assert user.must_change_password
    browser.post(
        reverse("password_change"),
        {"old_password": PASSWORD, "new_password1": OWN, "new_password2": OWN},
    )
    user.refresh_from_db()
    assert not user.must_change_password


def test_createsuperuser_makes_no_account() -> None:
    """Verify Django's createsuperuser is replaced by one that refuses and
    names create_admin, and no account is made.
    """
    with pytest.raises(CommandError, match="create_admin"):
        call_command(
            "createsuperuser",
            "--noinput",
            "--username=root",
            "--email=root@example.com",
            "--full_name=Root",
        )
    assert not User.objects.exists()
