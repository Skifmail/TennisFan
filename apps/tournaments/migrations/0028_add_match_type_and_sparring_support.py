# Generated manually for sparring refactoring

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tournaments", "0027_add_season_archive"),
        ("sparring", "0007_add_detailed_preferences_and_response_status"),
    ]

    operations = [
        # Сначала добавляем поле match_type с дефолтом 'tournament'
        migrations.AddField(
            model_name="match",
            name="match_type",
            field=models.CharField(
                choices=[
                    ("tournament", "Турнирный матч"),
                    ("sparring", "Спарринг (личная встреча)"),
                ],
                default="tournament",
                help_text="Турнирный матч или спарринг (личная встреча)",
                max_length=20,
                verbose_name="Тип матча",
            ),
        ),
        # Делаем tournament опциональным
        migrations.AlterField(
            model_name="match",
            name="tournament",
            field=models.ForeignKey(
                blank=True,
                help_text="Для турнирных матчей. Для спаррингов оставить пустым.",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="matches",
                to="tournaments.tournament",
                verbose_name="Турнир",
            ),
        ),
        # Добавляем связь с SparringResponse
        migrations.AddField(
            model_name="match",
            name="sparring_response",
            field=models.ForeignKey(
                blank=True,
                help_text="Связь с откликом на спарринг, если матч создан из спарринга",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="matches",
                to="sparring.sparringresponse",
                verbose_name="Отклик на спарринг",
            ),
        ),
    ]
