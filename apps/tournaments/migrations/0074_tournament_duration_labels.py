"""Единый раздел турниров и независимый выбор продолжительности."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tournaments", "0073_alter_match_sparring_response"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="tournament",
            options={
                "ordering": ["-start_date"],
                "verbose_name": "Турнир",
                "verbose_name_plural": "Турниры",
            },
        ),
        migrations.AlterField(
            model_name="tournament",
            name="duration",
            field=models.CharField(
                choices=[
                    ("single", "Однодневный"),
                    ("weekend", "Выходного дня"),
                    ("multi", "Многодневный"),
                ],
                default="multi",
                help_text=(
                    "Не зависит от формата сетки. Однодневный — все матчи в дату начала, "
                    "взнос для всех с учётом скидок, без постоплаты. Выходного дня — "
                    "до двух дней подряд. Многодневный — дедлайны по числу дней на раунд."
                ),
                max_length=10,
                verbose_name="Продолжительность",
            ),
        ),
        migrations.AlterField(
            model_name="tournament",
            name="is_one_day",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Служебный признак для совместимости с оплатой. "
                    "Синхронизируется с продолжительностью «Однодневный»."
                ),
                verbose_name="Однодневный турнир",
            ),
        ),
        migrations.AlterField(
            model_name="tournament",
            name="match_days_per_round",
            field=models.PositiveSmallIntegerField(
                default=7,
                help_text=(
                    "Сколько дней у игроков на раунд многодневного турнира. "
                    "В коротких турнирах дедлайны ограничены датами проведения."
                ),
                verbose_name="Дней на раунд (дедлайн матча)",
            ),
        ),
    ]
