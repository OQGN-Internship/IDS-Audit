"""manage.py change_ruleset <report> --by <username>: write a report's next
ruleset version from the changes read as JSON from standard input (ENG-02 3).

Run in Docker with -T, so standard input reaches it:
docker compose exec -T app python manage.py change_ruleset "IDS 2026"
--by <username> < change.json
"""

import json
import sys

from django.core.exceptions import PermissionDenied
from django.core.management.base import BaseCommand, CommandError

from ids_audit.accounts.models import User
from ids_audit.imports.services import RulesetRefused, change_ruleset
from ids_audit.reports.models import Report


class Command(BaseCommand):
    help = (
        "Write a report's next ruleset version from the settings that change, "
        "read as a JSON object from standard input."
    )
    # Tests pass their own standard input.
    stealth_options = ("stdin",)

    def add_arguments(self, parser) -> None:
        parser.add_argument("report", help="The report's name, in any case.")
        parser.add_argument(
            "--by",
            required=True,
            help="The active fixed administrator making the change.",
        )

    def handle(self, *args, report: str, by: str, **options) -> None:
        found = Report.objects.filter(name__iexact=report.strip()).first()
        if found is None:
            raise CommandError(f"No report is called {report}.")
        user = User.objects.filter(username=by).first()
        if user is None:
            raise CommandError(f"No account has the username {by}.")
        text = (options.get("stdin") or sys.stdin).read()
        try:
            changes = json.loads(text)
        except json.JSONDecodeError as error:
            raise CommandError(f"The change isn't valid JSON: {error}.") from None
        if not isinstance(changes, dict):
            raise CommandError("The change must be a JSON object of settings.")
        try:
            version = change_ruleset(found, user, changes, command="change_ruleset")
        except PermissionDenied:
            raise CommandError(
                f"{user} isn't an active fixed administrator, so can't change a "
                "ruleset."
            ) from None
        except RulesetRefused as refused:
            raise CommandError(
                "Nothing was changed:\n" + "\n".join(refused.problems)
            ) from None
        self.stdout.write(
            f"{found.name} now reads its files with ruleset version {version.version}."
        )
