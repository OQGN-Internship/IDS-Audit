"""Django's command line for this project.

It uses the development settings unless DJANGO_SETTINGS_MODULE names others
(ENG-01 7.5).
"""

import os
import sys

from django.core.management import execute_from_command_line


def main() -> None:
    """Run the manage.py command given on the command line."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
