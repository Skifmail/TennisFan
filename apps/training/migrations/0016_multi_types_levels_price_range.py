"""
Миграция: множественные типы/уровни, диапазон цен, цена за корт, цены по типам.

Шаги:
1. Добавить новые поля (JSONField и price_min/max, court_price_min/max)
2. Создать модель TrainingTypePrice
3. Скопировать данные из старых полей в новые
4. Удалить старые поля
"""

import django.db.models.deletion
from django.db import migrations, models


def migrate_data_forward(apps, schema_editor):
    """Копирование данных из старых полей в новые JSON-поля."""
    Training = apps.get_model("training", "Training")
    for t in Training.objects.all():
        if t.training_type:
            t.training_types = [t.training_type]
        if t.skill_level:
            t.skill_levels = [t.skill_level]
        if t.target_category:
            t.target_levels = [t.target_category]
        if t.price is not None:
            t.price_min = t.price
            t.price_max = t.price
        t.save()


def migrate_data_backward(apps, schema_editor):
    """Обратная миграция: из JSON-полей в старые одиночные поля."""
    Training = apps.get_model("training", "Training")
    for t in Training.objects.all():
        types = t.training_types or []
        t.training_type = types[0] if types else "individual"
        levels = t.skill_levels or []
        t.skill_level = levels[0] if levels else "amateur"
        targets = t.target_levels or []
        t.target_category = targets[0] if targets else ""
        if t.price_min is not None:
            t.price = t.price_min
        elif t.price_max is not None:
            t.price = t.price_max
        t.save()


class Migration(migrations.Migration):

    dependencies = [
        ("training", "0015_training_multiple_courts"),
    ]

    operations = [
        # 1. Добавить новые поля
        migrations.AddField(
            model_name="training",
            name="training_types",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Список выбранных типов тренировки (individual, group, mini_group, sparring, split).",
                verbose_name="Типы тренировки",
            ),
        ),
        migrations.AddField(
            model_name="training",
            name="skill_levels",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Список выбранных уровней (novice, amateur, experienced, advanced, professional).",
                verbose_name="Уровни",
            ),
        ),
        migrations.AddField(
            model_name="training",
            name="target_levels",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Список целевых уровней силы.",
                verbose_name="Целевые уровни силы",
            ),
        ),
        migrations.AddField(
            model_name="training",
            name="price_min",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=10,
                null=True,
                verbose_name="Цена от",
            ),
        ),
        migrations.AddField(
            model_name="training",
            name="price_max",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=10,
                null=True,
                verbose_name="Цена до",
            ),
        ),
        migrations.AddField(
            model_name="training",
            name="court_price_min",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=10,
                null=True,
                verbose_name="Цена за корт от",
            ),
        ),
        migrations.AddField(
            model_name="training",
            name="court_price_max",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=10,
                null=True,
                verbose_name="Цена за корт до",
            ),
        ),
        # 2. Создать модель TrainingTypePrice
        migrations.CreateModel(
            name="TrainingTypePrice",
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
                    "training_type",
                    models.CharField(
                        choices=[
                            ("individual", "Индивидуальная"),
                            ("group", "Групповая"),
                            ("mini_group", "Мини-группа (2-4 чел.)"),
                            ("sparring", "Спарринг тренировка"),
                            ("split", "Сплит"),
                        ],
                        max_length=20,
                        verbose_name="Тип тренировки",
                    ),
                ),
                (
                    "price",
                    models.DecimalField(
                        decimal_places=2, max_digits=10, verbose_name="Цена"
                    ),
                ),
                (
                    "training",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="type_prices",
                        to="training.training",
                        verbose_name="Тренировка",
                    ),
                ),
            ],
            options={
                "verbose_name": "Цена по типу тренировки",
                "verbose_name_plural": "Цены по типам тренировок",
                "ordering": ["training_type"],
                "unique_together": {("training", "training_type")},
            },
        ),
        # 3. Скопировать данные из старых полей
        migrations.RunPython(migrate_data_forward, migrate_data_backward),
        # 4. Удалить старые поля
        migrations.RemoveField(
            model_name="training",
            name="training_type",
        ),
        migrations.RemoveField(
            model_name="training",
            name="skill_level",
        ),
        migrations.RemoveField(
            model_name="training",
            name="target_category",
        ),
        migrations.RemoveField(
            model_name="training",
            name="price",
        ),
    ]
