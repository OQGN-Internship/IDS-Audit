"""The forms of the change-password page and the administration screens
(ENG-01 6.1). Each form only checks what it was sent; the function it's
saved through checks the rules and writes the event.
"""

from django import forms
from django.contrib.auth.forms import PasswordChangeForm

from ids_audit.accounts.permissions import LABELS


class NewPasswordForm(PasswordChangeForm):
    """Choosing a new password, which can't be the one it replaces, so
    whoever set a temporary password never knows the one in use (ENG-01 6.1).
    """

    def clean(self):
        cleaned = super().clean()
        new = cleaned.get("new_password1")
        # The current password is in cleaned_data only once it was checked.
        if new and new == cleaned.get("old_password"):
            self.add_error(
                "new_password1", "Choose a password different from the current one."
            )
        return cleaned


def password_field(label: str) -> forms.CharField:
    """A password field the browser offers to fill with a new password,
    never one it saved.
    """
    return forms.CharField(
        label=label,
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )


class TemporaryPasswordForm(forms.Form):
    """A temporary password, typed twice."""

    password1 = password_field("Temporary password")
    password2 = password_field("Temporary password again")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("password1") != cleaned.get("password2"):
            self.add_error("password2", "The two passwords differ.")
        return cleaned


class DetailsForm(forms.Form):
    """An account's full name and email, which can be corrected."""

    full_name = forms.CharField(max_length=150)
    email = forms.EmailField(required=False)


class NewUserForm(TemporaryPasswordForm):
    """A new account: its username, full name, email if any, and a temporary
    password twice. Usernames never change, so it's asked only here.
    """

    username = forms.CharField(max_length=150)
    full_name = forms.CharField(max_length=150)
    email = forms.EmailField(required=False)

    field_order = ["username", "full_name", "email", "password1", "password2"]


class RoleForm(forms.Form):
    """A role's name and permissions (ENG-01 6.2)."""

    name = forms.CharField(max_length=100)
    permissions = forms.MultipleChoiceField(
        choices=list(LABELS.items()),
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )
