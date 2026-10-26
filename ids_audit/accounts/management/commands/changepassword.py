"""manage.py changepassword <username>, in place of Django's (ENG-01 6.1).

Setting someone's password from the command line is a reset: the user must
choose their own at the next sign-in, and password_reset is written. Django's
own command does neither.
"""

import getpass

from django.contrib.auth.management.commands import changepassword
from django.db import transaction

from ids_audit.accounts.models import User
from ids_audit.accounts.services import password_was_reset


class Command(changepassword.Command):
    help = (
        "Reset a user's password to a temporary one, which they must change at "
        "the next sign-in."
    )

    def handle(self, *args, **options) -> str:
        # Django's command takes the shell's user when no username is given.
        username = options["username"] or getpass.getuser()
        with transaction.atomic():
            # Asks for the password twice and saves it, or raises CommandError.
            done = super().handle(*args, **options)
            password_was_reset(User.objects.get(username=username), "changepassword")
        return f"{done}. They must choose a new one at the next sign-in."
