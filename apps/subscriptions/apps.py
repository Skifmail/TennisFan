from django.apps import AppConfig


class SubscriptionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.subscriptions"

    def ready(self) -> None:
        """Подключить назначение тарифа Free при создании пользователя."""
        from . import signals  # noqa: F401
