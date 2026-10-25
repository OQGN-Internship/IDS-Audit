"""manage.py disable_admin <username>: disable a fixed administrator (ENG-01
6.1). The account stays, with its history in the audit log.
"""

from django.core.management.base import BaseCommand, CommandError

from ids_audit.accounts.models import User
from ids_audit.accounts.services import disable_fixed_admin


class Command(BaseCommand):
    help = "Disable a fixed administrator. Their history stays in the audit log."

    def add_arguments(self, parser) -> None:
        parser.add_argument("username")

    def handle(self, *args, username: str, **options) -> None:
        user = User.objects.filter(username=username, is_fixed_admin=True).first()
        if user is None:
            raise CommandError(f"No fixed administrator has the username {username}.")
        if not user.is_active:
            raise CommandError(f"{user} is already disabled.")
        disable_fixed_admin(user)
        self.stdout.write(f"Disabled {user}.")
