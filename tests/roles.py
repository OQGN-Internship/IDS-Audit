"""Running a test's statements as ids_app (ENG-01 7.5)."""

from collections.abc import Iterator
from contextlib import contextmanager

from django.db import connection, transaction
from django.db.backends.utils import CursorWrapper

# PostgreSQL's code for "permission denied". Checking it means a test can't
# pass because of some other error, such as a typo in the SQL.
INSUFFICIENT_PRIVILEGE = "42501"


@contextmanager
def as_app() -> Iterator[CursorWrapper]:
    """Run the statements inside as ids_app, in a savepoint of their own.

    If a statement is refused, rolling back the savepoint also undoes the role
    switch. If not, RESET ROLE switches back to ids_owner.
    """
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL ROLE ids_app")
        yield cursor
        cursor.execute("RESET ROLE")
