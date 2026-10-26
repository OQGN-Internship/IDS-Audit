"""manage.py axes_reset_username <username>..., in place of django-axes' (ENG-01
6.1). It ends each lock-out and writes account_unlocked, which django-axes'
own command doesn't.
"""

from django.core.management.base import BaseCommand

from ids_audit.accounts.services import unlock


class Command(BaseCommand):
    help = "Unlock each username, clearing its failed sign-ins."

    def add_arguments(self, parser) -> None:
        parser.add_argument("username", nargs="+")

    def handle(self, *args, username: list[str], **options) -> None:
        for name in username:
            if unlock(name, "axes_reset_username"):
                self.stdout.write(f"Unlocked {name}.")
            else:
                self.stdout.write(f"{name} has no failed sign-ins to clear.")
