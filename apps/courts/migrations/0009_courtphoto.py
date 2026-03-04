from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("courts", "0008_court_extra_fields_and_rental_price_range"),
    ]

    operations = [
        migrations.CreateModel(
            name="CourtPhoto",
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
                        upload_to="courts/gallery/",
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
                    "court",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="photos",
                        to="courts.court",
                        verbose_name="Корт",
                    ),
                ),
            ],
            options={
                "verbose_name": "Фото корта",
                "verbose_name_plural": "Фото кортов",
                "ordering": ["order", "id"],
            },
        ),
    ]
