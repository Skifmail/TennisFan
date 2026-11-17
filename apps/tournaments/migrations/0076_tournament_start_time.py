"""Добавить время старта, не выдумывая его для старых турниров."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tournaments", "0075_normalize_tournament_duration")]

    operations = [
        migrations.AddField(
            model_name="tournament",
            name="start_time",
            field=models.TimeField(
                blank=True,
                null=True,
                verbose_name="Время начала",
                help_text=(
                    "По часовому поясу сайта. Обязательно для однодневного турнира, "
                    "в том числе при старте после набора."
                ),
            ),
        ),
    ]
