from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tournaments", "0039_add_skill_rating_models"),
    ]

    operations = [
        migrations.CreateModel(
            name="TournamentPhoto",
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
                    "image",
                    models.ImageField(
                        upload_to="tournaments/gallery/",
                        verbose_name="Фото",
                    ),
                ),
                (
                    "order",
                    models.PositiveSmallIntegerField(
                        default=0,
                        help_text="Меньшее число — раньше в галерее.",
                        verbose_name="Порядок",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                        verbose_name="Создано",
                    ),
                ),
                (
                    "tournament",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="photos",
                        to="tournaments.tournament",
                        verbose_name="Турнир",
                    ),
                ),
            ],
            options={
                "verbose_name": "Фото турнира",
                "verbose_name_plural": "Фото турниров",
                "ordering": ["order", "id"],
            },
        ),
    ]
