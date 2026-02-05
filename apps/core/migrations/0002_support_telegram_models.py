# Support: UserTelegramLink + SupportMessage

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0001_add_feedback_models"),
    ]

    operations = [
        migrations.CreateModel(
            name="UserTelegramLink",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("telegram_chat_id", models.BigIntegerField(blank=True, db_index=True, null=True, unique=True, verbose_name="Telegram chat_id")),
                ("binding_token", models.CharField(blank=True, db_index=True, max_length=64, unique=True, verbose_name="Токен привязки (для t.me/bot?start=TOKEN)")),
                ("token_created_at", models.DateTimeField(blank=True, null=True, verbose_name="Когда создан токен")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Обновлено")),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="telegram_link", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "привязка Telegram",
                "verbose_name_plural": "привязки Telegram",
            },
        ),
        migrations.CreateModel(
            name="SupportMessage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("subject", models.CharField(blank=True, max_length=200, verbose_name="Тема")),
                ("text", models.TextField(verbose_name="Текст сообщения")),
                ("is_from_admin", models.BooleanField(default=False, verbose_name="От администратора")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
                (
                    "admin_telegram_message_id",
                    models.BigIntegerField(
                        blank=True,
                        db_index=True,
                        null=True,
                        unique=True,
                        verbose_name="ID сообщения в Telegram (админу)",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="support_messages",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "сообщение поддержки",
                "verbose_name_plural": "сообщения поддержки",
                "ordering": ["created_at"],
            },
        ),
    ]
