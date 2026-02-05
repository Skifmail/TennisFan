"""
Модели приложения Telegram-бота (рассылки и т.д.).
Прокси-модели для отображения в разделе «Телеграм» в админке.
"""

from django.conf import settings
from django.db import models

from apps.core.models import UserTelegramLink as CoreUserTelegramLink
from apps.tournaments.models import DeadlineExtensionRequest as CoreDeadlineExtensionRequest


class TelegramBroadcast(models.Model):
    """
    Рассылка в пользовательский Telegram-бот: текст и опционально фото.
    При сохранении (создании) отправляется всем пользователям с привязанным ботом.
    """

    text = models.TextField("Текст сообщения", help_text="Поддерживается HTML: <b>, <i>, <a href=\"...\">")
    image = models.ImageField(
        "Фото",
        upload_to="telegram_broadcasts/%Y/%m/",
        blank=True,
        null=True,
        help_text="Необязательно. Если указано — отправляется как пост с подписью.",
    )
    sent_at = models.DateTimeField("Отправлено", null=True, blank=True, editable=False)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="+",
    )

    class Meta:
        verbose_name = "рассылка в Telegram"
        verbose_name_plural = "рассылки в Telegram"
        ordering = ("-created_at",)

    def __str__(self):
        preview = (self.text or "")[:50] or "(пусто)"
        if self.sent_at:
            return f"{preview}… — отправлено {self.sent_at:%d.%m.%Y %H:%M}"
        return f"{preview}… — черновик"


class UserTelegramLinkProxy(CoreUserTelegramLink):
    """Прокси для отображения привязок Telegram в разделе «Телеграм» админки."""

    class Meta:
        proxy = True
        app_label = "telegram_bot"
        verbose_name = CoreUserTelegramLink._meta.verbose_name
        verbose_name_plural = CoreUserTelegramLink._meta.verbose_name_plural


class DeadlineExtensionRequestProxy(CoreDeadlineExtensionRequest):
    """Прокси для отображения запросов продления дедлайна в разделе «Телеграм» админки."""

    class Meta:
        proxy = True
        app_label = "telegram_bot"
        verbose_name = CoreDeadlineExtensionRequest._meta.verbose_name
        verbose_name_plural = CoreDeadlineExtensionRequest._meta.verbose_name_plural
