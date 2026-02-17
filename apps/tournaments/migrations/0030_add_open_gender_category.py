# Add "Смешанный" (open) gender category for tournaments

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tournaments", "0029_rename_fan_format_display"),
    ]

    operations = [
        migrations.AlterField(
            model_name="tournament",
            name="gender",
            field=models.CharField(
                choices=[
                    ("male", "Мужчины"),
                    ("female", "Женщины"),
                    ("open", "Смешанный"),
                    ("mixed", "Микст"),
                ],
                default="male",
                help_text=(
                    "Мужчины/Женщины — только указанный пол. "
                    "Смешанный — любой пол (М против Ж, пары ММ против ЖЖ и т.д.). "
                    "Микст — только для парных: в команде должны быть М + Ж."
                ),
                max_length=10,
                verbose_name="Категория по полу",
            ),
        ),
    ]
