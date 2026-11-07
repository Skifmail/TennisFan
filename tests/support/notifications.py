"""Явные политики каналов для тестов после очистки данных транзакционных сценариев."""

from apps.notifications.domain.catalog import EVENTS
from apps.notifications.models import EventPolicy


def seed_event_policies() -> None:
    """Восстановить реестр для теста, не меняя уже настроенные им разрешения."""
    EventPolicy.objects.bulk_create(
        [
            EventPolicy(
                key=key,
                title=event.title,
                group=event.group,
                description=event.title,
            )
            for key, event in EVENTS.items()
        ],
        ignore_conflicts=True,
    )
