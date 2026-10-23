"""The report and ruleset tables (ENG-02 2 and 3)."""

from django.conf import settings as django_settings
from django.db import models

# Imported under another name because Ruleset has a field called settings,
# which would hide django.conf.settings inside that class.
USER_MODEL = django_settings.AUTH_USER_MODEL


class Report(models.Model):
    """A separate set of uploads with its own totals and its own user access."""

    class DuplicateMode(models.TextChoices):
        """How a commit treats lines already in the report (ENG-02 7.3)."""

        REPLACE_BY_PERIOD = "replace_by_period", "Replace by period"
        LINE_KEY = "line_key", "Line key"

    name = models.CharField(max_length=200)
    # Empty until the report's first commit sets it. It never changes after that.
    duplicate_mode = models.CharField(
        max_length=20, choices=DuplicateMode.choices, blank=True
    )
    created_by = models.ForeignKey(
        USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "report"

    def __str__(self):
        return self.name


class Ruleset(models.Model):
    """One version of the settings that decide how a report's files are read and
    what counts (ENG-02 3). A change makes a new version, never an edit, and
    ids_app can only insert rows here (ENG-01 6.3).
    """

    report = models.ForeignKey(
        Report, on_delete=models.PROTECT, related_name="rulesets"
    )
    version = models.PositiveIntegerField()
    settings = models.JSONField()
    created_by = models.ForeignKey(
        USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ruleset"
        constraints = [
            models.UniqueConstraint(
                fields=["report", "version"], name="ruleset_one_row_per_version"
            )
        ]

    def __str__(self):
        return f"{self.report} v{self.version}"
