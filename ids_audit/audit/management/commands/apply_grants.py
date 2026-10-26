"""manage.py apply_grants: run sql/grants.sql on the database, in one
transaction (ENG-01 6.3 and 7.1).

The migrate job runs it after every migration, as the owner, so a new table
gets ids_app's rights and the insert-only tables never get UPDATE or DELETE
back. The tests run it on the test database too (ENG-01 7.5).
"""

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection, transaction

GRANTS_SQL = settings.BASE_DIR / "sql" / "grants.sql"


class Command(BaseCommand):
    help = "Give ids_app its rights on every table and view (sql/grants.sql)."

    def handle(self, *args, **options) -> None:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(GRANTS_SQL.read_text(encoding="utf-8"))
        self.stdout.write("Applied sql/grants.sql.")
