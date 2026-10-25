"""manage.py record_restore <backup> --totals-match yes|no --files-match yes|no

restore.sh runs this last, so the restored audit log records the restore as
a command-line action: which backup, and whether its totals and files
matched (ENG-01 6.3 and 7.2).
"""

from django.core.management.base import BaseCommand

from ids_audit import audit
from ids_audit.audit.models import AuditEvent


class Command(BaseCommand):
    help = "Record a restore from a backup in the audit log, as restore.sh does."

    def add_arguments(self, parser) -> None:
        parser.add_argument("backup", help="The backup folder's name.")
        parser.add_argument("--totals-match", choices=("yes", "no"), required=True)
        parser.add_argument("--files-match", choices=("yes", "no"), required=True)

    def handle(self, *args, backup, totals_match, files_match, **options) -> None:
        audit.record(
            AuditEvent.Action.DATABASE_RESTORED,
            details={
                "backup": backup,
                "totals_match": totals_match == "yes",
                "files_match": files_match == "yes",
                "command": "restore.sh",
            },
        )
        self.stdout.write(f"Recorded the restore of {backup} in the audit log.")
