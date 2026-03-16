"""
Миграция Phase 6: модели настроек уведомлений для клубов и участников.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("clubs", "0002_pending_models"),
    ]

    operations = [
        migrations.CreateModel(
            name="ClubNotificationSettings",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "is_enabled",
                    models.BooleanField(
                        default=True, verbose_name="Уведомления включены"
                    ),
                ),
                (
                    "email_enabled",
                    models.BooleanField(
                        default=True, verbose_name="Разрешить email-уведомления"
                    ),
                ),
                (
                    "telegram_enabled",
                    models.BooleanField(
                        default=False,
                        verbose_name="Разрешить Telegram-уведомления",
                    ),
                ),
                (
                    "club",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="member_notification_settings",
                        to="clubs.club",
                        verbose_name="Клуб",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="club_notification_settings",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Пользователь",
                    ),
                ),
            ],
            options={
                "verbose_name": "Настройки уведомлений участника клуба",
                "verbose_name_plural": "Настройки уведомлений участников клубов",
                "unique_together": {("user", "club")},
            },
        ),
        migrations.CreateModel(
            name="ClubNotificationConfig",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "notify_by_email",
                    models.BooleanField(
                        default=True, verbose_name="Включить email-уведомления"
                    ),
                ),
                (
                    "notify_by_telegram",
                    models.BooleanField(
                        default=False,
                        verbose_name="Включить Telegram-уведомления",
                    ),
                ),
                (
                    "fee_reminders_enabled",
                    models.BooleanField(
                        default=True, verbose_name="Напоминания о взносах"
                    ),
                ),
                (
                    "fee_overdue_enabled",
                    models.BooleanField(default=True, verbose_name="Просрочка взносов"),
                ),
                (
                    "fee_paid_enabled",
                    models.BooleanField(
                        default=True, verbose_name="Уведомления об оплате взноса"
                    ),
                ),
                (
                    "subscription_expiring_enabled",
                    models.BooleanField(
                        default=True,
                        verbose_name="Истечение подписки клуба",
                    ),
                ),
                (
                    "tournament_reminders_enabled",
                    models.BooleanField(
                        default=True, verbose_name="Напоминания о турнирах"
                    ),
                ),
                (
                    "new_member_enabled",
                    models.BooleanField(
                        default=True, verbose_name="Новый участник клуба"
                    ),
                ),
                (
                    "debtors_summary_enabled",
                    models.BooleanField(default=True, verbose_name="Сводка должников"),
                ),
                (
                    "club",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="notification_config",
                        to="clubs.club",
                        verbose_name="Клуб",
                    ),
                ),
            ],
            options={
                "verbose_name": "Настройки уведомлений клуба",
                "verbose_name_plural": "Настройки уведомлений клубов",
            },
        ),
    ]
