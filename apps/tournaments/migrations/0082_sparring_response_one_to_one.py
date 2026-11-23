"""Согласовать одиночную связь с уже существующим уникальным ограничением БД."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Изменить только ORM-состояние, сохранив столбец, индексы и данные матча."""

    dependencies = [
        ("tournaments", "0081_manual_stage_tours"),
    ]

    operations = [
        # Миграция 0073 уже создала UNIQUE; повторный DDL здесь не требуется.
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name="match",
                    name="sparring_response",
                    field=models.OneToOneField(
                        blank=True,
                        help_text="Связь с откликом на спарринг, если матч создан из спарринга",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="match",
                        to="sparring.sparringresponse",
                        verbose_name="Отклик на спарринг",
                    ),
                ),
            ],
        ),
    ]
