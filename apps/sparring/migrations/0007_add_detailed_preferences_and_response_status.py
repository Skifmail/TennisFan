# Generated manually for refactoring sparring module

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sparring", "0006_add_sparring_response"),
    ]

    operations = [
        migrations.AddField(
            model_name="sparringrequest",
            name="desired_partner_age_min",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="Желаемый минимальный возраст партнера для спарринга",
                null=True,
                verbose_name="Минимальный возраст партнера",
            ),
        ),
        migrations.AddField(
            model_name="sparringrequest",
            name="desired_partner_age_max",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="Желаемый максимальный возраст партнера для спарринга",
                null=True,
                verbose_name="Максимальный возраст партнера",
            ),
        ),
        migrations.AddField(
            model_name="sparringrequest",
            name="preferred_location",
            field=models.CharField(
                blank=True,
                help_text="Конкретное место или район для игры (например, название корта или района)",
                max_length=200,
                verbose_name="Предпочтительное место",
            ),
        ),
        migrations.AddField(
            model_name="sparringresponse",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Ожидает рассмотрения"),
                    ("accepted", "Принят"),
                    ("rejected", "Отклонен"),
                ],
                default="pending",
                max_length=20,
                verbose_name="Статус отклика",
            ),
        ),
        migrations.AddField(
            model_name="sparringresponse",
            name="updated_at",
            field=models.DateTimeField(auto_now=True, verbose_name="Обновлено"),
        ),
    ]
