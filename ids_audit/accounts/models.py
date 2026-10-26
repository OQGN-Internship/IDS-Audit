"""The accounts tables: users (ENG-01 6.1), and roles and report access
(ENG-01 6.2).
"""

from django.contrib.auth.models import AbstractUser
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.db.models.functions import Lower

from ids_audit.accounts.permissions import LABELS, PERMISSIONS, VIEW


class FixedAdminProtected(Exception):
    """A change the application refuses to make to a fixed administrator: to
    delete one, disable one, or take away their rights (ENG-01 6.1).
    """


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
    # Lets a user who isn't a fixed administrator create reports. Nothing turns
    # it on until the client answers the plan's open question (ENG-01 8).
    can_create_reports = models.BooleanField(default=False)

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

    def save(self, *args, **kwargs):
        """Save the user, refusing to disable a fixed administrator or take
        away the flag. manage.py disable_admin is the only way to disable one,
        and it doesn't go through here.
        """
        if self.pk is not None:
            stored = User.objects.filter(pk=self.pk, is_fixed_admin=True).first()
            if stored is not None and (
                not self.is_fixed_admin or (stored.is_active and not self.is_active)
            ):
                raise FixedAdminProtected(
                    f"{stored} is a fixed administrator, who can be disabled only "
                    "with manage.py disable_admin."
                )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Delete the user, refusing a fixed administrator."""
        if User.objects.filter(pk=self.pk, is_fixed_admin=True).exists():
            raise FixedAdminProtected(
                f"{self} is a fixed administrator, who can't be deleted."
            )
        return super().delete(*args, **kwargs)


class Role(models.Model):
    """A named set of permissions, which an administrator gives a user on a
    report (ENG-01 6.2). Viewer, Uploader and Approver are preset.
    """

    name = models.CharField(max_length=100)
    # Names from permissions.py, in the plan's order. Every role holds view,
    # which every report address needs.
    permissions = ArrayField(
        models.CharField(max_length=20, choices=list(LABELS.items()))
    )

    class Meta:
        db_table = "role"
        constraints = [
            models.UniqueConstraint(
                Lower("name"),
                name="role_name_unique_ignoring_case",
                violation_error_message=(
                    "A role with this name already exists. Names that differ "
                    "only in capitals count as the same."
                ),
            ),
            models.CheckConstraint(
                condition=models.Q(permissions__contained_by=sorted(PERMISSIONS)),
                name="role_permissions_known",
                violation_error_message="A role holds only the six permissions.",
            ),
            models.CheckConstraint(
                condition=models.Q(permissions__contains=[VIEW]),
                name="role_holds_view",
                violation_error_message=(
                    "Every role holds View, which every screen of a report needs."
                ),
            ),
        ]

    def __str__(self):
        return self.name


class ReportAccess(models.Model):
    """A user's role on one report (ENG-01 6.2). No row means no access, and a
    fixed administrator has none, since they hold every permission already.
    """

    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="access")
    report = models.ForeignKey(
        "reports.Report", on_delete=models.PROTECT, related_name="access"
    )
    # A role still assigned can't be removed.
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name="access")

    class Meta:
        db_table = "report_access"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "report"],
                name="report_access_one_row_per_user_and_report",
            )
        ]

    def __str__(self):
        return f"{self.user} on {self.report}: {self.role}"
