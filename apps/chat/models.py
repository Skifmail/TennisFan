"""Хранилище городских комнат, участия и журнала изменений."""

from uuid import uuid4

from django.conf import settings
from django.db import models

from .domain import MAX_MESSAGE_LENGTH, MAX_REPORT_LENGTH


class ChatRoom(models.Model):
    """Общая комната населённого пункта.

    Attributes:
        city: Однозначная ссылка на населённый пункт.
        is_active: Разрешён ли доступ к комнате.
        rules: Закреплённые правила общения.
    """

    city = models.OneToOneField(
        "core.City", on_delete=models.PROTECT, related_name="chat_room"
    )
    is_active = models.BooleanField("Открыта", default=True)
    rules = models.TextField(
        "Закреплённые правила",
        default="Общайтесь уважительно. Без спама, оскорблений и публикации чужих контактов.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "городской чат"
        verbose_name_plural = "городские чаты"

    def __str__(self) -> str:
        """Возвращает название комнаты для администрирования."""
        return str(self.city)


class ChatMembership(models.Model):
    """Участие пользователя независимо от города его профиля.

    Attributes:
        is_active: Вступил ли пользователь в комнату.
        is_moderator: Может ли модерировать эту комнату.
        banned_until: До какого момента запрещено писать.
        last_read_message_id: Монотонная отметка прочтения.
        notifications_enabled: Получать ли уведомления об ответах.
    """

    room = models.ForeignKey(
        ChatRoom, on_delete=models.CASCADE, related_name="memberships"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="chat_memberships",
    )
    is_active = models.BooleanField("Участвует", default=True)
    is_moderator = models.BooleanField("Модератор", default=False)
    banned_until = models.DateTimeField("Запрет писать до", null=True, blank=True)
    notifications_enabled = models.BooleanField("Уведомлять об ответах", default=True)
    last_read_message_id = models.PositiveBigIntegerField(default=0)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["room", "user"], name="chat_member_unique")
        ]
        indexes = [models.Index(fields=["user", "is_active"])]
        verbose_name = "участник чата"
        verbose_name_plural = "участники чатов"


class ChatMessage(models.Model):
    """Сообщение с идемпотентным ключом отправки и мягким удалением.

    Attributes:
        client_id: Ключ повторного запроса одного автора.
        reply_to: Исходное сообщение в той же комнате.
        deleted_at: Момент удаления; удалённый текст очищается.
    """

    room = models.ForeignKey(
        ChatRoom, on_delete=models.CASCADE, related_name="messages"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="chat_messages",
    )
    client_id = models.UUIDField(default=uuid4)
    revision = models.PositiveBigIntegerField(default=0)
    text = models.TextField(max_length=MAX_MESSAGE_LENGTH)
    reply_to = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="replies"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    edited_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["author", "client_id"], name="chat_message_retry_unique"
            )
        ]
        indexes = [
            models.Index(fields=["room", "id"]),
            models.Index(fields=["author", "-created_at"]),
        ]
        verbose_name = "сообщение чата"
        verbose_name_plural = "сообщения чатов"


class ChatEvent(models.Model):
    """Надёжный курсор изменений для восстановления после обрыва связи.

    Attributes:
        message: Изменившееся сообщение, включая удалённые.
        room: Комната; порядок событий сериализуется блокировкой комнаты.
    """

    room = models.ForeignKey(ChatRoom, on_delete=models.CASCADE, related_name="events")
    message = models.ForeignKey(
        ChatMessage, on_delete=models.CASCADE, related_name="events"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["room", "id"])]


class ChatReport(models.Model):
    """Жалоба участника на сообщение для рассмотрения администрацией.

    Attributes:
        reason: Причина жалобы без публичного раскрытия.
        is_resolved: Завершено ли рассмотрение.
    """

    message = models.ForeignKey(
        ChatMessage, on_delete=models.CASCADE, related_name="reports"
    )
    reporter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    reason = models.CharField("Причина", max_length=MAX_REPORT_LENGTH)
    is_resolved = models.BooleanField("Рассмотрена", default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["message", "reporter"], name="chat_report_unique"
            )
        ]
        verbose_name = "жалоба на сообщение"
        verbose_name_plural = "жалобы на сообщения"
