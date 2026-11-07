"""Сохранять дедупликацию входящих обновлений независимо от кеша и процесса."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("telegram_bot", "0004_legacy_broadcast_status")]

    operations = [
        migrations.CreateModel(
            name="TelegramUpdateReceipt",
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
                ("bot", models.CharField(max_length=64)),
                ("update_id", models.PositiveBigIntegerField()),
                ("processed_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("bot", "update_id"), name="telegram_unique_bot_update"
                    )
                ],
            },
        ),
    ]
