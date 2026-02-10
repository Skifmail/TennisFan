from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0004_add_user_bot_chat_id"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="TelegramTransferConsentLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("consent_version", models.CharField(db_index=True, default="v1", max_length=32, verbose_name="Версия текста согласия")),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True, verbose_name="IP-адрес")),
                ("user_agent", models.TextField(blank=True, default="", verbose_name="User-Agent")),
                ("consented_at", models.DateTimeField(auto_now_add=True, verbose_name="Дата и время согласия")),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="telegram_transfer_consents",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "согласие на передачу данных в Telegram",
                "verbose_name_plural": "согласия на передачу данных в Telegram",
                "ordering": ["-consented_at"],
            },
        ),
    ]
