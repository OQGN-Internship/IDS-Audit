"""manage.py createsuperuser, in place of Django's (ENG-01 6.1).

Django's would make an account that's neither a fixed administrator nor in
the audit log, so this one makes none and names create_admin instead.
"""

from django.contrib.auth.management.commands import createsuperuser
from django.core.management.base import CommandError


class Command(createsuperuser.Command):
    help = "Makes no account. Use create_admin to make a fixed administrator."

    def handle(self, *args, **options) -> None:
        raise CommandError(
            "createsuperuser makes no account here. Use manage.py create_admin "
            "<username>, which makes a fixed administrator and records it in the "
            "audit log (ENG-01 6.1)."
        )
