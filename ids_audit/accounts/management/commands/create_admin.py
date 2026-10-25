"""manage.py create_admin <username>: create a fixed administrator (ENG-01 6.1).

It asks for the full name, then twice for an initial password, without
showing it. The administrator chooses their own password at first sign-in.
"""

import getpass

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from ids_audit.accounts.models import User
from ids_audit.accounts.services import create_fixed_admin


class Command(BaseCommand):
    help = (
        "Create a fixed administrator, who must change the password at first sign-in."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("username")

    def handle(self, *args, username: str, **options) -> None:
        # Before anything is typed, so no one types a password for nothing.
        if User.objects.filter(username=username).exists():
            raise CommandError(f"The username {username} is already taken.")
        full_name = input("Full name: ")
        password = getpass.getpass("Initial password: ")
        if getpass.getpass("Initial password again: ") != password:
            raise CommandError("The two passwords differ. Nothing was created.")
        try:
            user = create_fixed_admin(username, full_name, password)
        except ValidationError as error:
            raise CommandError(" ".join(error.messages)) from None
        self.stdout.write(
            f"Created {user}, a fixed administrator, who must choose a new "
            "password at first sign-in."
        )
