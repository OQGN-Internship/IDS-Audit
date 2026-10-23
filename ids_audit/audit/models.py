"""The audit log table (ENG-01 6.3).

Links to other apps are "app.Model" strings: every other app will call
audit.record(), so this file imports none of their models.
"""

from django.conf import settings
from django.db import models


class AuditEvent(models.Model):
    """One thing that happened, written only through audit.record().

    ids_app can only insert rows here, so nobody can change or delete an event
    (ENG-01 6.3).
    """

    class Action(models.TextChoices):
        """The actions recorded (ENG-01 6.3). The upload_ ones match the state
        changes in ENG-02 6, one each.
        """

        SIGN_IN = "sign_in"
        SIGN_OUT = "sign_out"
        SIGN_IN_FAILED = "sign_in_failed"
        ACCOUNT_LOCKED = "account_locked"
        USER_CREATED = "user_created"
        USER_DISABLED = "user_disabled"
        PASSWORD_RESET = "password_reset"
        ROLE_CREATED = "role_created"
        ROLE_CHANGED = "role_changed"
        ROLE_REMOVED = "role_removed"
        ACCESS_GRANTED = "access_granted"
        ACCESS_CHANGED = "access_changed"
        ACCESS_REMOVED = "access_removed"
        REPORT_CREATED = "report_created"
        RULESET_CHANGED = "ruleset_changed"
        UPLOAD_RECEIVED = "upload_received"
        UPLOAD_NEEDS_COLUMNS = "upload_needs_columns"
        UPLOAD_COLUMNS_PICKED = "upload_columns_picked"
        UPLOAD_AWAITING_APPROVAL = "upload_awaiting_approval"
        UPLOAD_FAILED = "upload_failed"
        UPLOAD_APPROVED = "upload_approved"
        UPLOAD_REJECTED = "upload_rejected"
        UPLOAD_WITHDRAWN = "upload_withdrawn"
        UPLOAD_ROLLED_BACK = "upload_rolled_back"
        EXPORT_DOWNLOADED = "export_downloaded"

    # Stored in UTC, shown in Muscat time.
    at = models.DateTimeField(auto_now_add=True)
    # Empty for a failed sign-in with an unknown username, a command-line action,
    # or a change the application makes on its own.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    action = models.CharField(max_length=40, choices=Action.choices)
    report = models.ForeignKey(
        "reports.Report",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    upload = models.ForeignKey(
        "imports.Upload",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    # Such as the username tried, the role granted, the file hash, or the
    # filters used in an export.
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "audit_event"
