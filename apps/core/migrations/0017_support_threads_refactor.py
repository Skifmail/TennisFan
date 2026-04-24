from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


def _build_guest_key(message):
    return (
        (message.guest_name or "").strip().lower(),
        (message.guest_contact or "").strip().lower(),
    )


def forwards(apps, schema_editor):
    SupportMessage = apps.get_model("core", "SupportMessage")
    SupportThread = apps.get_model("core", "SupportThread")

    user_threads = {}
    guest_threads = {}

    for msg in SupportMessage.objects.all().order_by("created_at", "id"):
        if msg.user_id:
            thread = user_threads.get(msg.user_id)
            if thread is None:
                thread = SupportThread.objects.create(
                    user_id=msg.user_id,
                    guest_email="",
                    guest_name="",
                    guest_session_key="",
                    last_message_at=msg.created_at or timezone.now(),
                    admin_unread_count=0,
                    user_unread_count=0,
                    is_closed=False,
                )
                user_threads[msg.user_id] = thread
        else:
            guest_key = _build_guest_key(msg)
            thread = guest_threads.get(guest_key)
            if thread is None:
                thread = SupportThread.objects.create(
                    user_id=None,
                    guest_email=(msg.guest_contact or "").strip(),
                    guest_name=(msg.guest_name or "").strip(),
                    guest_session_key="",
                    last_message_at=msg.created_at or timezone.now(),
                    admin_unread_count=0,
                    user_unread_count=0,
                    is_closed=False,
                )
                guest_threads[guest_key] = thread

        msg.thread_id = thread.id
        msg.save(update_fields=["thread"])

        if msg.created_at and msg.created_at > thread.last_message_at:
            thread.last_message_at = msg.created_at

        if msg.is_from_admin:
            thread.user_unread_count += 1
        else:
            thread.admin_unread_count += 1
        thread.save(
            update_fields=["last_message_at", "admin_unread_count", "user_unread_count"]
        )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0016_club_legal_and_user_consent"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SupportThread",
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
                    "guest_email",
                    models.EmailField(
                        blank=True, max_length=254, verbose_name="Email гостя"
                    ),
                ),
                (
                    "guest_name",
                    models.CharField(
                        blank=True, max_length=200, verbose_name="Имя гостя"
                    ),
                ),
                (
                    "guest_session_key",
                    models.CharField(
                        blank=True,
                        db_index=True,
                        max_length=64,
                        verbose_name="Ключ сессии гостя",
                    ),
                ),
                (
                    "last_message_at",
                    models.DateTimeField(
                        db_index=True, verbose_name="Последнее сообщение"
                    ),
                ),
                (
                    "admin_unread_count",
                    models.PositiveIntegerField(
                        default=0, verbose_name="Непрочитано админом"
                    ),
                ),
                (
                    "user_unread_count",
                    models.PositiveIntegerField(
                        default=0, verbose_name="Непрочитано пользователем"
                    ),
                ),
                (
                    "is_closed",
                    models.BooleanField(default=False, verbose_name="Диалог закрыт"),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="Создано"),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="Обновлено"),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        help_text="Зарегистрированный пользователь (null для гостя).",
                        null=True,
                        on_delete=models.deletion.CASCADE,
                        related_name="support_threads",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "диалог поддержки",
                "verbose_name_plural": "диалоги поддержки",
                "ordering": ["-last_message_at"],
            },
        ),
        migrations.AddField(
            model_name="supportmessage",
            name="thread",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name="messages",
                to="core.supportthread",
            ),
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="supportmessage",
            name="thread",
            field=models.ForeignKey(
                on_delete=models.deletion.CASCADE,
                related_name="messages",
                to="core.supportthread",
            ),
        ),
        migrations.RemoveField(
            model_name="supportmessage",
            name="admin_telegram_message_id",
        ),
        migrations.RemoveField(
            model_name="supportmessage",
            name="admin_telegram_text",
        ),
        migrations.RemoveField(
            model_name="supportmessage",
            name="guest_binding_token",
        ),
        migrations.RemoveField(
            model_name="supportmessage",
            name="guest_telegram_chat_id",
        ),
        migrations.RemoveField(
            model_name="supportmessage",
            name="guest_telegram_username",
        ),
        migrations.DeleteModel(
            name="SupportMessageAdminDelivery",
        ),
    ]
