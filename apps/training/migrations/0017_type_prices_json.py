"""
Миграция: объединение training_types (список) и TrainingTypePrice (модель) в единое поле type_prices (словарь).

Шаги:
1. Добавить временное поле type_prices_data (JSONField default=dict)
2. Перенести данные: training_types + TrainingTypePrice → type_prices_data
3. Удалить модель TrainingTypePrice (связана related_name="type_prices")
4. Удалить поле training_types
5. Переименовать type_prices_data → type_prices
"""

from decimal import Decimal

from django.db import migrations, models


def migrate_forward(apps, schema_editor):
    """
    Объединяем training_types (список типов без цен) и TrainingTypePrice (модель с ценами)
    в словарь type_prices_data: {тип: цена или None}.
    """
    Training = apps.get_model("training", "Training")
    TrainingTypePrice = apps.get_model("training", "TrainingTypePrice")

    for training in Training.objects.all():
        prices_dict = {}

        # Цены из TrainingTypePrice
        for ttp in TrainingTypePrice.objects.filter(training=training):
            prices_dict[ttp.training_type] = float(ttp.price) if ttp.price else None

        # Типы из training_types, у которых нет записи в TrainingTypePrice
        for t in training.training_types or []:
            if t not in prices_dict:
                prices_dict[t] = None

        training.type_prices_data = prices_dict
        training.save(update_fields=["type_prices_data"])


def migrate_backward(apps, schema_editor):
    """
    Обратно: type_prices_data → training_types (список) + TrainingTypePrice (модель).
    """
    Training = apps.get_model("training", "Training")
    TrainingTypePrice = apps.get_model("training", "TrainingTypePrice")

    for training in Training.objects.all():
        tp = training.type_prices_data or {}
        training.training_types = list(tp.keys())
        training.save(update_fields=["training_types"])

        for t_type, price in tp.items():
            if price is not None:
                TrainingTypePrice.objects.update_or_create(
                    training=training,
                    training_type=t_type,
                    defaults={"price": Decimal(str(price))},
                )


class Migration(migrations.Migration):

    dependencies = [
        ("training", "0016_multi_types_levels_price_range"),
    ]

    operations = [
        # 1. Добавить временное поле type_prices_data
        migrations.AddField(
            model_name="training",
            name="type_prices_data",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Словарь {тип_тренировки: цена}.",
                verbose_name="Типы и цены (temp)",
            ),
        ),
        # 2. Перенести данные
        migrations.RunPython(migrate_forward, migrate_backward),
        # 3. Удалить модель TrainingTypePrice (related_name="type_prices")
        migrations.DeleteModel(
            name="TrainingTypePrice",
        ),
        # 4. Удалить поле training_types
        migrations.RemoveField(
            model_name="training",
            name="training_types",
        ),
        # 5. Переименовать type_prices_data → type_prices
        migrations.RenameField(
            model_name="training",
            old_name="type_prices_data",
            new_name="type_prices",
        ),
        # 6. Обновить help_text
        migrations.AlterField(
            model_name="training",
            name="type_prices",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Словарь {тип_тренировки: цена}. Пример: {'individual': 3000, 'group': 1500}.",
                verbose_name="Типы и цены",
            ),
        ),
    ]
