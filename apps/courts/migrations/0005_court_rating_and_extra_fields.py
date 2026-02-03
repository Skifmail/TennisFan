# Generated manually for court rating and extra fields

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("courts", "0004_enrollment_contact_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="court",
            name="district",
            field=models.CharField(blank=True, max_length=100, verbose_name="Район города"),
        ),
        migrations.AddField(
            model_name="court",
            name="sells_balls",
            field=models.BooleanField(default=False, verbose_name="Теннисные мячи в продаже"),
        ),
        migrations.AddField(
            model_name="court",
            name="sells_water",
            field=models.BooleanField(default=False, verbose_name="Вода в продаже"),
        ),
        migrations.AddField(
            model_name="court",
            name="multiple_payment_methods",
            field=models.BooleanField(default=False, verbose_name="Возможность оплаты разными способами"),
        ),
        migrations.AlterField(
            model_name="court",
            name="phone",
            field=models.CharField(blank=True, max_length=50, verbose_name="Телефон"),
        ),
        migrations.CreateModel(
            name="CourtRating",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("score", models.PositiveSmallIntegerField(choices=[(1, "1"), (2, "2"), (3, "3"), (4, "4"), (5, "5")], verbose_name="Оценка")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создана")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Обновлена")),
                ("court", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="ratings", to="courts.court", verbose_name="Корт")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="court_ratings", to=settings.AUTH_USER_MODEL, verbose_name="Пользователь")),
            ],
            options={
                "verbose_name": "Оценка корта",
                "verbose_name_plural": "Оценки кортов",
                "ordering": ["-updated_at"],
                "unique_together": {("court", "user")},
            },
        ),
    ]
