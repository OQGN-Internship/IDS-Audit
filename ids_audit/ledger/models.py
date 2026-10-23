"""The posting and mapping tables (ENG-02 2).

ids_app can only insert rows into all three (ENG-01 6.3). Links to other apps
are written as "app.Model" strings, because imports also imports from here.
"""

from django.db import models


class SpendLine(models.Model):
    """The fields a posting and a staging row share (ENG-02 2).

    abstract = True makes no table of its own: Posting and StagingRow each get
    these fields in their own table.
    """

    report = models.ForeignKey(
        "reports.Report", on_delete=models.PROTECT, related_name="+"
    )
    # An upload with postings can't be deleted.
    upload = models.ForeignKey(
        "imports.Upload", on_delete=models.PROTECT, related_name="+"
    )

    # Its place in the original file.
    sheet = models.TextField(blank=True)
    row_number = models.PositiveIntegerField()

    # All four are optional. Confirm with the real export.
    company_code = models.TextField(blank=True)
    fiscal_year = models.PositiveIntegerField(null=True, blank=True)
    document_number = models.TextField(blank=True)
    line_item = models.TextField(blank=True)
    # The four above, cleaned and joined with "-", when all four exist (ENG-02 7).
    line_key = models.TextField(blank=True)

    posting_date = models.DateField()
    # Cleaned as in ENG-02 5. A spend row always has both.
    gl_code = models.TextField()
    cost_center_code = models.TextField()
    # As written in the file, if it has a value-type column.
    value_type = models.TextField(blank=True)
    # Set by the ruleset's actual-spend rule (ENG-02 3).
    is_actual = models.BooleanField()
    # Negative for credits. Never a float (ENG-01 4, rule 5).
    amount = models.DecimalField(max_digits=18, decimal_places=3)
    currency = models.CharField(max_length=3)
    # Kept as written, Arabic included.
    item_text = models.TextField(blank=True)

    class Meta:
        abstract = True


class Posting(SpendLine):
    """One committed spend line. It counts only while its upload is committed
    (ENG-02 10.2).
    """

    class Meta:
        db_table = "posting"


class GlCiMap(models.Model):
    """A GL account and its budget line, as one upload gave them (ENG-02 8)."""

    upload = models.ForeignKey(
        "imports.Upload", on_delete=models.PROTECT, related_name="+"
    )
    report = models.ForeignKey(
        "reports.Report", on_delete=models.PROTECT, related_name="+"
    )
    gl_code = models.TextField()
    gl_name = models.TextField(blank=True)
    # Empty when the file lists the GL account without a commitment item.
    ci_code = models.TextField(blank=True)
    ci_name = models.TextField(blank=True)

    class Meta:
        db_table = "gl_ci_map"
        constraints = [
            models.UniqueConstraint(
                fields=["upload", "gl_code"], name="gl_ci_map_one_row_per_gl"
            )
        ]


class CcDeptMap(models.Model):
    """A cost center and its department, as one upload gave them (ENG-02 8)."""

    upload = models.ForeignKey(
        "imports.Upload", on_delete=models.PROTECT, related_name="+"
    )
    report = models.ForeignKey(
        "reports.Report", on_delete=models.PROTECT, related_name="+"
    )
    cc_code = models.TextField()
    cc_name = models.TextField(blank=True)
    dept_code = models.TextField()
    dept_name = models.TextField(blank=True)

    class Meta:
        db_table = "cc_dept_map"
        constraints = [
            models.UniqueConstraint(
                fields=["upload", "cc_code"], name="cc_dept_map_one_row_per_cc"
            )
        ]
