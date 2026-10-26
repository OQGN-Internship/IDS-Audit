"""The upload, staging row and skipped row tables (ENG-02 2)."""

from decimal import Decimal

from django.conf import settings
from django.db import models

from ids_audit.ledger.models import SpendLine
from ids_audit.reports.models import Report, Ruleset


class Upload(models.Model):
    """One uploaded file, and everything learned from reading it (ENG-02 2).

    Its status changes only through the moves in ENG-02 6, and each move writes
    an audit event. The fields that reading fills stay empty until it does.
    """

    class Status(models.TextChoices):
        """The states in ENG-02 6."""

        READING = "reading", "Reading"
        NEEDS_COLUMNS = "needs_columns", "Needs columns"
        AWAITING_APPROVAL = "awaiting_approval", "Awaiting approval"
        COMMITTED = "committed", "Committed"
        REJECTED = "rejected", "Rejected"
        FAILED = "failed", "Failed"
        ROLLED_BACK = "rolled_back", "Rolled back"

    class ControlTotalSource(models.TextChoices):
        """Where the total to reconcile against came from (ENG-02 9.1)."""

        FILE = "file", "From the file"
        ENTERED = "entered", "Entered by the uploader"

    report = models.ForeignKey(Report, on_delete=models.PROTECT, related_name="uploads")
    # 1, 2, 3 within the report, given once when the upload is created and never
    # changed. "Upload n" means this number (ENG-02 2).
    number = models.PositiveIntegerField()

    # The file. Its SHA-256 is also its name in the uploads volume.
    file_sha256 = models.CharField(max_length=64)
    original_name = models.CharField(max_length=255)
    size_bytes = models.PositiveIntegerField()
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    ruleset = models.ForeignKey(Ruleset, on_delete=models.PROTECT, related_name="+")
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.READING
    )

    # Filled by reading (ENG-02 4, 9 and 11).
    column_layout = models.JSONField(null=True, blank=True)
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    control_total = models.DecimalField(
        max_digits=18, decimal_places=3, null=True, blank=True
    )
    control_total_source = models.CharField(
        max_length=10, choices=ControlTotalSource.choices, blank=True
    )
    rows_read = models.PositiveIntegerField(null=True, blank=True)
    rows_skipped = models.PositiveIntegerField(null=True, blank=True)
    rows_new = models.PositiveIntegerField(null=True, blank=True)
    rows_duplicate = models.PositiveIntegerField(null=True, blank=True)
    rows_replaced = models.PositiveIntegerField(null=True, blank=True)
    preview = models.JSONField(null=True, blank=True)
    # 0.000 means the file reconciles (ENG-02 9.2).
    reconciliation_difference = models.DecimalField(
        max_digits=18, decimal_places=3, null=True, blank=True
    )

    # Approval or rejection, then a rollback (ENG-02 6 and 10).
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    rolled_back_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    rolled_back_at = models.DateTimeField(null=True, blank=True)
    # Why it failed, if it did.
    error = models.TextField(blank=True)

    class Meta:
        db_table = "upload"
        constraints = [
            models.UniqueConstraint(
                fields=["report", "number"], name="upload_one_row_per_number"
            )
        ]

    def __str__(self):
        # "upload n, file name", as in the as-of line (ENG-03 3.2).
        return f"upload {self.number}, {self.original_name}"


class StagingRow(SpendLine):
    """A spend line read from a file awaiting approval (ENG-02 2).

    Deleted once its upload leaves awaiting_approval or fails (ENG-02 6).
    """

    # Already in the report, in line-key mode (ENG-02 7.3). Not committed.
    is_duplicate = models.BooleanField(default=False)

    class Meta:
        db_table = "staging_row"


class PlainNumericField(models.Field):
    """A Decimal in a plain numeric column, with no fixed number of decimal
    places, so a value is stored exactly as read (ENG-01 4, rule 5).

    Django's DecimalField always fixes the number of decimal places, and
    PostgreSQL rounds anything longer when it saves.
    """

    def db_type(self, connection):
        return "numeric"

    def to_python(self, value):
        if value is None or isinstance(value, Decimal):
            return value
        # A float's binary value isn't the amount that was written.
        if isinstance(value, float):
            raise TypeError("An amount never passes through a float.")
        return Decimal(value)

    def get_prep_value(self, value):
        return self.to_python(super().get_prep_value(value))


class SkippedRow(models.Model):
    """A spend row that couldn't be cleaned, kept with its reason (ENG-02 5).

    ids_app can only insert rows here (ENG-01 6.3).
    """

    upload = models.ForeignKey(Upload, on_delete=models.PROTECT, related_name="+")
    sheet = models.TextField(blank=True)
    row_number = models.PositiveIntegerField()
    reason = models.TextField()
    # Only if it could be read, and kept exactly as read, such as 12.3456.
    amount = PlainNumericField(null=True, blank=True)
    # The row's cells as they were in the file.
    raw_values = models.JSONField()

    class Meta:
        db_table = "skipped_row"
