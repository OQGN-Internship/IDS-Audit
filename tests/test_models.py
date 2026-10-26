"""The tables, amount columns and unique rules the first migrations create
(ENG-02 2, ENG-01 4 rule 5, ENG-01 6.1).
"""

from collections.abc import Callable
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import IntegrityError, connection, models, transaction

from ids_audit.accounts.models import User
from ids_audit.imports.models import SkippedRow, Upload
from ids_audit.ledger.models import CcDeptMap, GlCiMap, Posting
from ids_audit.reports.models import Ruleset
from tests.factories import make_upload, spend_line

ENG_02_TABLES = [
    "report",
    "ruleset",
    "upload",
    "posting",
    "staging_row",
    "skipped_row",
    "gl_ci_map",
    "cc_dept_map",
    "audit_event",
]


def column_type(table: str, column: str) -> tuple[str, int | None, int | None]:
    """A column's type, precision and scale, as PostgreSQL reports them."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT data_type, numeric_precision, numeric_scale"
            " FROM information_schema.columns"
            " WHERE table_name = %s AND column_name = %s",
            [table, column],
        )
        return cursor.fetchone()


# Migrations


@pytest.mark.django_db
def test_every_model_change_has_a_migration() -> None:
    """Verify makemigrations finds nothing the migrations don't already hold."""
    call_command("makemigrations", "--check", "--dry-run", stdout=StringIO())


@pytest.mark.django_db
def test_every_table_has_its_name_from_eng_02() -> None:
    """Verify the migrated test database has every table named in ENG-02 2,
    the custom user in place of Django's own (ENG-01 6.1), and one view,
    posting_resolved (ENG-02 8.4).
    """
    tables = connection.introspection.table_names()
    assert [table for table in ENG_02_TABLES if table not in tables] == []
    assert "accounts_user" in tables
    assert "auth_user" not in tables
    with connection.cursor() as cursor:
        everything = connection.introspection.get_table_list(cursor)
    assert [info.name for info in everything if info.type == "v"] == [
        "posting_resolved"
    ]


# Amounts


AMOUNT_COLUMNS = {
    "posting.amount": ("numeric", 18, 3),
    "staging_row.amount": ("numeric", 18, 3),
    "upload.control_total": ("numeric", 18, 3),
    "upload.reconciliation_difference": ("numeric", 18, 3),
    # The view passes posting.amount through unchanged (ENG-02 8.4).
    "posting_resolved.amount": ("numeric", 18, 3),
    # The one exception to rule 5: kept exactly as read.
    "skipped_row.amount": ("numeric", None, None),
}


@pytest.mark.django_db
@pytest.mark.parametrize("name", AMOUNT_COLUMNS)
def test_amount_columns_have_the_right_type(name: str) -> None:
    """Verify every amount column is NUMERIC(18,3), except skipped_row.amount."""
    table, column = name.split(".")
    assert column_type(table, column) == AMOUNT_COLUMNS[name]


@pytest.mark.parametrize("text", ["3456.789", "-1234.500", "999999999999999.999"])
def test_a_posting_amount_comes_back_exactly(text: str, upload: Upload) -> None:
    """Verify an amount goes into posting and comes back as the same Decimal,
    the largest NUMERIC(18,3) holds included.
    """
    posting = Posting.objects.create(**spend_line(upload, amount=Decimal(text)))
    posting.refresh_from_db()
    assert str(posting.amount) == text


@pytest.mark.parametrize("text", ["12.3456", "0.30000000000000004"])
def test_a_skipped_amount_comes_back_exactly(text: str, upload: Upload) -> None:
    """Verify a skipped amount with more than 3 decimal places isn't rounded
    (ENG-02 5).
    """
    row = SkippedRow.objects.create(
        upload=upload,
        row_number=9,
        reason="more than 3 decimal places",
        amount=Decimal(text),
        raw_values={},
    )
    row.refresh_from_db()
    assert str(row.amount) == text


def test_a_skipped_amount_never_passes_through_a_float(upload: Upload) -> None:
    """Verify a float is refused for a skipped amount (ENG-01 4, rule 5)."""
    with pytest.raises(TypeError), transaction.atomic():
        SkippedRow.objects.create(
            upload=upload, row_number=9, reason="x", amount=12.3456, raw_values={}
        )


# One row per key. Each rule is shown refusing a second row with the same key,
# and allowing one with a different key.

ONE_ROW_PER_KEY: dict[str, Callable[[Upload, str], models.Model]] = {
    "upload number in a report": lambda u, key: Upload(
        report=u.report,
        number=int(key),
        file_sha256="1" * 64,
        original_name="b.xlsx",
        size_bytes=1,
        uploaded_by=u.uploaded_by,
        ruleset=u.ruleset,
    ),
    "ruleset version in a report": lambda u, key: Ruleset(
        report=u.report, version=int(key), settings={}, created_by=u.uploaded_by
    ),
    "GL account in an upload": lambda u, key: GlCiMap(
        upload=u, report=u.report, gl_code=f"61010{key}"
    ),
    "cost center in an upload": lambda u, key: CcDeptMap(
        upload=u, report=u.report, cc_code=f"310{key}", dept_code="D01"
    ),
}


def first_row(rule: str, upload: Upload) -> None:
    """Save the first row with key 1, unless the upload fixture already made it."""
    if rule not in ("upload number in a report", "ruleset version in a report"):
        ONE_ROW_PER_KEY[rule](upload, "1").save()


@pytest.mark.parametrize("rule", ONE_ROW_PER_KEY)
def test_a_second_row_with_the_same_key_is_refused(rule: str, upload: Upload) -> None:
    """Verify each unique constraint refuses a second row with the same key."""
    first_row(rule, upload)
    with pytest.raises(IntegrityError), transaction.atomic():
        ONE_ROW_PER_KEY[rule](upload, "1").save()


@pytest.mark.parametrize("rule", ONE_ROW_PER_KEY)
def test_a_second_row_with_another_key_is_allowed(rule: str, upload: Upload) -> None:
    """Verify each unique constraint allows a second row with a different key."""
    first_row(rule, upload)
    ONE_ROW_PER_KEY[rule](upload, "2").save()


@pytest.mark.django_db
def test_two_reports_can_each_have_an_upload_1() -> None:
    """Verify upload numbers are unique within a report, not across reports."""
    first, second = make_upload(), make_upload()
    assert (first.number, second.number) == (1, 1)
    assert first.report != second.report


# Users


def test_a_user_is_shown_as_full_name_and_username() -> None:
    """Verify a user reads as "Full name (username)" (ENG-01 6.1)."""
    user = User(username="mbalushi", full_name="Mohammed bin Said bin Ali Al Balushi")
    assert str(user) == "Mohammed bin Said bin Ali Al Balushi (mbalushi)"
