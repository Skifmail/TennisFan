# Tournament allowed categories (1–5 per tournament)

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("tournaments", "0018_tournament_min_participants_insufficient_notified"),
    ]

    operations = [
        migrations.CreateModel(
            name="TournamentAllowedCategory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "category",
                    models.CharField(
                        choices=[
                            ("novice", "Новичок"),
                            ("amateur", "Любитель"),
                            ("experienced", "Опытный"),
                            ("advanced", "Продвинутый"),
                            ("professional", "Профессионал"),
                        ],
                        max_length=20,
                        verbose_name="Категория",
                    ),
                ),
                (
                    "tournament",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="allowed_categories",
                        to="tournaments.tournament",
                        verbose_name="Турнир",
                    ),
                ),
            ],
            options={
                "verbose_name": "Допустимая категория турнира",
                "verbose_name_plural": "Допустимые категории турнира",
                "ordering": ["tournament", "category"],
                "unique_together": {("tournament", "category")},
            },
        ),
    ]
