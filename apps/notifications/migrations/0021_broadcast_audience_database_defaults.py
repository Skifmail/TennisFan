"""Сохранить совместимость INSERT предыдущих версий приложения при деплое."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("notifications", "0020_broadcast_audience")]

    operations = [
        migrations.AlterField(
            model_name="broadcast",
            name="audience_mode",
            field=models.CharField(
                choices=[
                    ("all", "Всем активным пользователям"),
                    ("filters", "По городу и полу"),
                    ("selected", "Конкретным пользователям"),
                ],
                db_default="all",
                default="all",
                max_length=12,
                verbose_name="Кому отправить",
            ),
        ),
        migrations.AlterField(
            model_name="broadcast",
            name="cities",
            field=models.JSONField(
                blank=True, db_default=[], default=list, verbose_name="Города"
            ),
        ),
        migrations.AlterField(
            model_name="broadcast",
            name="gender",
            field=models.CharField(
                blank=True,
                choices=[("male", "Мужской"), ("female", "Женский")],
                db_default="",
                max_length=10,
                verbose_name="Пол",
            ),
        ),
        migrations.AlterField(
            model_name="broadcast",
            name="recipient_ids",
            field=models.JSONField(
                blank=True, db_default=[], default=list, verbose_name="Пользователи"
            ),
        ),
    ]
