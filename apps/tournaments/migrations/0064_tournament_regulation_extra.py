"""Дополнительные положения регламента турнира."""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Добавляет необязательный текст, который попадает в скачиваемый регламент."""

    dependencies = [
        ("tournaments", "0063_sport_padel"),
    ]

    operations = [
        migrations.AddField(
            model_name="tournament",
            name="regulation_extra",
            field=models.TextField(
                blank=True,
                help_text=(
                    "Необязательный текст: особые правила, мячи, судья, "
                    "контакты на площадке. Попадает отдельным разделом "
                    "в скачиваемый регламент."
                ),
                verbose_name="Дополнительные положения регламента",
            ),
        ),
    ]
