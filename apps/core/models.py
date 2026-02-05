"""
Core models.
"""

import secrets
from django.conf import settings
from django.db import models


# ---------------------------------------------------------------------------
# Новая система обратной связи через Telegram (пользователь ↔ админ в Telegram)
# ---------------------------------------------------------------------------


class UserTelegramLink(models.Model):
    """
    Связь пользователя сайта с Telegram chat_id.
    Telegram не позволяет писать пользователю первым — пользователь должен
    хотя бы раз написать боту (например /start с токеном с сайта).
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="telegram_link",
    )
    telegram_chat_id = models.BigIntegerField(
        "Telegram chat_id (бот поддержки)",
        null=True,
        blank=True,
        unique=True,
        db_index=True,
        help_text="Используется ботом поддержки для ответов пользователю.",
    )
    user_bot_chat_id = models.BigIntegerField(
        "Chat ID для пользовательского бота",
        null=True,
        blank=True,
        unique=True,
        db_index=True,
        help_text="Заполняется только после /start в чате с ботом уведомлений (кнопка «Подключить» на профиле).",
    )
    binding_token = models.CharField(
        "Токен привязки (для t.me/bot?start=TOKEN)",
        max_length=64,
        blank=True,
        unique=True,
        db_index=True,
    )
    token_created_at = models.DateTimeField("Когда создан токен", null=True, blank=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "привязка Telegram"
        verbose_name_plural = "привязки Telegram"

    def __str__(self):
        return f"{self.user} → {self.telegram_chat_id or 'не привязан'}"

    def get_or_create_binding_token(self):
        """Вернуть токен для привязки; создать новый, если нет или просрочен (24 ч)."""
        from django.utils import timezone
        from datetime import timedelta
        if self.binding_token:
            if self.token_created_at and timezone.now() - self.token_created_at < timedelta(hours=24):
                return self.binding_token
        self.binding_token = secrets.token_urlsafe(32)
        self.token_created_at = timezone.now()
        self.save(update_fields=["binding_token", "token_created_at"])
        return self.binding_token


class SupportMessage(models.Model):
    """
    Сообщение в системе поддержки: от пользователя (с сайта или из Telegram)
    или от администратора (ответ в Telegram).
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="support_messages",
    )
    subject = models.CharField("Тема", max_length=200, blank=True)
    text = models.TextField("Текст сообщения")
    is_from_admin = models.BooleanField("От администратора", default=False)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    # ID сообщения в Telegram (наше сообщение админу), чтобы связать reply админа с этим сообщением
    admin_telegram_message_id = models.BigIntegerField(
        "ID сообщения в Telegram (админу)",
        null=True,
        blank=True,
        unique=True,
        db_index=True,
    )
    # Текст, который мы отправили админу (для редактирования сообщения после ответа — пометка «Ответ отправлен»)
    admin_telegram_text = models.TextField("Текст сообщения админу", blank=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "сообщение поддержки"
        verbose_name_plural = "сообщения поддержки"

    def __str__(self):
        return f"#{self.pk} {'(админ)' if self.is_from_admin else ''} {self.user}"


# ---------------------------------------------------------------------------
# Старая модель обратной связи (виджет на сайте, ответы на сайте)
# ---------------------------------------------------------------------------


class Feedback(models.Model):
    """Обратная связь от пользователя. Сообщение уходит в Telegram админу; ответы хранятся здесь."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="feedback_messages",
    )
    subject = models.CharField("Тема", max_length=200, blank=True)
    message = models.TextField("Сообщение")
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    telegram_message_id = models.BigIntegerField(
        "ID сообщения в Telegram",
        null=True,
        blank=True,
        help_text="Нужен для привязки ответа админа из Telegram.",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "обратная связь"
        verbose_name_plural = "обратная связь"

    def __str__(self):
        return f"#{self.pk} от {self.user} ({self.created_at:%d.%m.%Y %H:%M})"


class FeedbackReply(models.Model):
    """Ответ администратора на обратную связь (приходит из Telegram)."""

    feedback = models.ForeignKey(
        Feedback,
        on_delete=models.CASCADE,
        related_name="replies",
    )
    text = models.TextField("Текст ответа")
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "ответ на обратную связь"
        verbose_name_plural = "ответы на обратную связь"

    def __str__(self):
        return f"Ответ на #{self.feedback_id}: {self.text[:50]}..."
