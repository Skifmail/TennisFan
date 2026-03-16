"""
Миграция Phase 7: PlatformAuditLog и PlatformSettings.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("clubs", "0003_notification_models"),
    ]

    operations = [
        migrations.CreateModel(
            name="PlatformAuditLog",
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
                    "action",
                    models.CharField(
                        max_length=50,
                        choices=[
                            ("club_blocked", "Клуб заблокирован"),
                            ("club_unblocked", "Клуб разблокирован"),
                            ("plan_changed", "Тариф изменён"),
                            ("subscription_extended", "Подписка продлена"),
                            ("club_deleted", "Клуб удалён"),
                            ("trial_reset", "Trial сброшен"),
                            ("club_auto_suspended", "Клуб автоматически приостановлен"),
                            ("club_auto_deleted", "Клуб автоматически удалён"),
                            ("settings_changed", "Настройки платформы изменены"),
                        ],
                        verbose_name="Действие",
                    ),
                ),
                (
                    "details",
                    models.TextField(blank=True, verbose_name="Детали"),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="Дата"),
                ),
                (
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="platform_audit_logs",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Кто выполнил",
                    ),
                ),
                (
                    "club",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="audit_logs",
                        to="clubs.club",
                        verbose_name="Клуб",
                    ),
                ),
            ],
            options={
                "verbose_name": "Запись аудит-лога платформы",
                "verbose_name_plural": "Аудит-лог платформы",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="PlatformSettings",
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
                    "trial_days",
                    models.PositiveIntegerField(
                        default=14,
                        verbose_name="Дней trial-периода",
                    ),
                ),
                (
                    "suspended_data_retention_days",
                    models.PositiveIntegerField(
                        default=30,
                        verbose_name="Хранение данных suspended клубов (дней)",
                    ),
                ),
                (
                    "auto_delete_suspended",
                    models.BooleanField(
                        default=False,
                        verbose_name="Автоудаление suspended клубов",
                    ),
                ),
                (
                    "registration_open",
                    models.BooleanField(
                        default=True,
                        verbose_name="Регистрация клубов открыта",
                    ),
                ),
            ],
            options={
                "verbose_name": "Настройки платформы",
                "verbose_name_plural": "Настройки платформы",
            },
        ),
    ]
