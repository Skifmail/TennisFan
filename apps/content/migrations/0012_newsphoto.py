# Add NewsPhoto model for news gallery

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("content", "0011_add_rules_seeding_section"),
    ]

    operations = [
        migrations.CreateModel(
            name="NewsPhoto",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("image", models.ImageField(upload_to="news/gallery/", verbose_name="Фото")),
                ("caption", models.CharField(blank=True, max_length=200, verbose_name="Подпись")),
                ("order", models.PositiveSmallIntegerField(default=0, verbose_name="Порядок")),
                (
                    "news",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="photos",
                        to="content.news",
                        verbose_name="Новость",
                    ),
                ),
            ],
            options={
                "verbose_name": "Фото новости",
                "verbose_name_plural": "Фото новостей",
                "ordering": ["order", "id"],
            },
        ),
    ]
