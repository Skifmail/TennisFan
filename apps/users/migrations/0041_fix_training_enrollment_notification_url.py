"""Исправляет ссылку в уведомлениях о заявках на тренировку."""

from django.db import migrations
from django.db.models import Q

LEGACY_URL = "/users/notifications/"
TARGET_URL = "/training/my/"


def forwards(apps, schema_editor):
    """Направляет старые уведомления о заявках в кабинет тренера.

    Args:
        apps: Исторические модели миграции.
        schema_editor: Текущее подключение схемы.
    """
    Notification = apps.get_model("users", "Notification")
    Notification.objects.using(schema_editor.connection.alias).filter(
        Q(event_key="training.enrollment")
        | Q(message__startswith="Заявка на тренировку."),
        url__in=["", LEGACY_URL],
    ).update(url=TARGET_URL)


def backwards(apps, schema_editor):
    """Возвращает прежнюю ссылку на ленту уведомлений.

    Args:
        apps: Исторические модели миграции.
        schema_editor: Текущее подключение схемы.
    """
    Notification = apps.get_model("users", "Notification")
    Notification.objects.using(schema_editor.connection.alias).filter(
        event_key="training.enrollment",
        url=TARGET_URL,
    ).update(url=LEGACY_URL)


class Migration(migrations.Migration):
    """Обновляет ссылку в уже созданных уведомлениях о тренировках."""

    dependencies = [("users", "0040_notification_event_key_notification_is_visible")]

    operations = [migrations.RunPython(forwards, backwards)]
