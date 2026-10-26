"""manage.py show_ruleset <report>: print a report's latest ruleset version, the
settings its files are read with, as JSON, so a change can be written from it
(ENG-02 3).
"""

import json

from django.core.management.base import BaseCommand, CommandError

from ids_audit.reports.models import Report


class Command(BaseCommand):
    help = "Print a report's latest ruleset version and its settings as JSON."

    def add_arguments(self, parser) -> None:
        parser.add_argument("report", help="The report's name, in any case.")

    def handle(self, *args, report: str, **options) -> None:
        found = Report.objects.filter(name__iexact=report.strip()).first()
        if found is None:
            raise CommandError(f"No report is called {report}.")
        latest = found.rulesets.order_by("-version").first()
        shown = {"report": found.name, "version": latest.version}
        shown["settings"] = latest.settings
        self.stdout.write(json.dumps(shown, indent=2, ensure_ascii=False))
