# Generated manually for Feedback and FeedbackReply

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Feedback",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("subject", models.CharField(blank=True, max_length=200, verbose_name="Тема")),
                ("message", models.TextField(verbose_name="Сообщение")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
                (
                    "telegram_message_id",
                    models.BigIntegerField(
                        blank=True,
                        help_text="Нужен для привязки ответа админа из Telegram.",
                        null=True,
                        verbose_name="ID сообщения в Telegram",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="feedback_messages",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "обратная связь",
                "verbose_name_plural": "обратная связь",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="FeedbackReply",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("text", models.TextField(verbose_name="Текст ответа")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
                (
                    "feedback",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="replies",
                        to="core.feedback",
                    ),
                ),
            ],
            options={
                "verbose_name": "ответ на обратную связь",
                "verbose_name_plural": "ответы на обратную связь",
                "ordering": ["created_at"],
            },
        ),
    ]
