"""manage.py list_admins: every fixed administrator, with their full name and
whether the account is active (ENG-01 6.1).
"""

from django.core.management.base import BaseCommand

from ids_audit.accounts.models import User


class Command(BaseCommand):
    help = "List every fixed administrator, and whether the account is active."

    def handle(self, *args, **options) -> None:
        admins = User.objects.filter(is_fixed_admin=True).order_by("username")
        if not admins:
            self.stdout.write("There are no fixed administrators.")
        for user in admins:
            state = "active" if user.is_active else "disabled"
            self.stdout.write(f"{user.username}\t{user.full_name}\t{state}")
