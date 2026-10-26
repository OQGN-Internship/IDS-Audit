from django.apps import AppConfig


class AccountsConfig(AppConfig):
    """Registers the accounts app with Django."""

    name = "ids_audit.accounts"

    def ready(self) -> None:
        # Connects the receivers that write sign-ins to the audit log.
        from ids_audit.accounts import signals  # noqa: F401
