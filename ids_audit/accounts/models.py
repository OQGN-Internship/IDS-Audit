"""The accounts tables (ENG-01 6.1). Role and ReportAccess come in Phase 2."""

from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Someone who signs in: Django's standard user, with one full name in place
    of a first and last name, plus two flags (ENG-01 6.1).
    """

    # The name as the person writes it, in Arabic or English. Omani names don't
    # split into a first and a last name.
    full_name = models.CharField(max_length=150)
    # None removes a field that AbstractUser would add.
    first_name = None
    last_name = None

    # Made only by manage.py create_admin. Passes every access check (ENG-01 6.2).
    is_fixed_admin = models.BooleanField(default=False)
    # On for every new account and after every reset, until the user picks a
    # password of their own.
    must_change_password = models.BooleanField(default=True)

    # createsuperuser asks for these, so it can't make a user without a name.
    REQUIRED_FIELDS = ["email", "full_name"]

    def __str__(self):
        # Full names can repeat, usernames can't (ENG-01 6.1).
        return f"{self.full_name} ({self.username})"

    # AbstractUser's versions of these two read first_name and last_name.
    def get_full_name(self):
        return self.full_name

    def get_short_name(self):
        return self.full_name
