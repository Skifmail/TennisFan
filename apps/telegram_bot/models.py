"""
Модели приложения Telegram-бота (рассылки и т.д.).
Прокси-модели для отображения в разделе «Телеграм» в админке.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import UserTelegramLink as CoreUserTelegramLink
from apps.tournaments.models import (
    DeadlineExtensionRequest as CoreDeadlineExtensionRequest,
)
from config.validators import CompressImageFieldsMixin, validate_image_max_2mb

if TYPE_CHECKING:
    from apps.tournaments.models import Tournament
    from apps.users.models import User


class TelegramBroadcast(CompressImageFieldsMixin, models.Model):
    """
    Рассылка в пользовательский Telegram-бот: текст и опционально фото.
    При сохранении в админке ставится в долговечную очередь доставки.
    Фото валидируется и сжимается до 500 КБ (как в остальных разделах сайта).
    """

    class Status(models.TextChoices):
        """Состояния подготовки и подтверждённого приёма сообщений Telegram."""

        DRAFT = "draft", "Черновик"
        QUEUED = "queued", "В очереди"
        SENT = "sent", "Все сообщения приняты Telegram"
        PARTIAL = "partial", "Частично принято Telegram"
        FAILED = "failed", "Ни одно сообщение не принято"
        EMPTY = "empty", "Нет получателей"
        LEGACY = "legacy", "Старая отправка без журнала"

    text: models.TextField[str, str] = models.TextField(
        "Текст сообщения", help_text='Поддерживается HTML: <b>, <i>, <a href="...">'
    )
    image: models.ImageField = models.ImageField(
        "Фото",
        upload_to="telegram_broadcasts/%Y/%m/",
        blank=True,
        null=True,
        validators=[validate_image_max_2mb],
        help_text="Необязательно. Если указано — отправляется как пост с подписью. До 500 КБ, при загрузке сжимается.",
    )
    status: models.CharField[str, str] = models.CharField(
        "Статус",
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
        editable=False,
    )
    prepared_at: models.DateTimeField[datetime | None, datetime | None] = (
        models.DateTimeField("Подготовлено", null=True, blank=True, editable=False)
    )
    finished_at: models.DateTimeField[datetime | None, datetime | None] = (
        models.DateTimeField("Завершено", null=True, blank=True, editable=False)
    )
    cursor_user_id: models.BigIntegerField[int, int] = models.BigIntegerField(
        default=0, editable=False
    )
    upper_user_id: models.BigIntegerField[int, int] = models.BigIntegerField(
        default=0, editable=False
    )
    sent_at: models.DateTimeField[datetime | None, datetime | None] = (
        models.DateTimeField(
            "Все сообщения приняты", null=True, blank=True, editable=False
        )
    )
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        "Создано", auto_now_add=True
    )
    created_by: models.ForeignKey[User | None, User | None] = models.ForeignKey(
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
        ordering: ClassVar = ("-created_at",)

    def __str__(self) -> str:
        """Коротко показать текст и честный статус рассылки."""
        preview = (self.text or "")[:50] or "(пусто)"
        if self.status == self.Status.SENT and self.sent_at:
            return f"{preview}… — {self.get_status_display()} {self.sent_at:%d.%m.%Y %H:%M}"
        return f"{preview}… — {self.get_status_display()}"


class TelegramDeliveryState(models.Model):
    """Общая пауза после 429 и аренда отправителя для всех процессов бота."""

    bot: models.CharField[str, str] = models.CharField(max_length=32, primary_key=True)
    cooldown_until: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        default=timezone.now
    )
    lease_until: models.DateTimeField[datetime | None, datetime | None] = (
        models.DateTimeField(null=True)
    )
    lease_key: models.UUIDField[uuid.UUID | None, uuid.UUID | None] = models.UUIDField(
        null=True
    )


class TelegramUpdateReceipt(models.Model):
    """Успешно обработанный update без хранения текста, токенов и личных данных."""

    bot: models.CharField[str, str] = models.CharField(max_length=64)
    update_id: models.PositiveBigIntegerField[int, int] = (
        models.PositiveBigIntegerField()
    )
    processed_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["bot", "update_id"], name="telegram_unique_bot_update"
            )
        ]


class TournamentAnnouncement(models.Model):
    """Сохраняемая пакетная подготовка ЛК, email и Telegram о новом турнире."""

    tournament: models.OneToOneField[Tournament, Tournament] = models.OneToOneField(
        "tournaments.Tournament", on_delete=models.CASCADE, related_name="announcement"
    )
    tournament_id: int
    status: models.CharField[str, str] = models.CharField(
        max_length=16, default="queued"
    )
    cursor_user_id: models.BigIntegerField[int, int] = models.BigIntegerField(default=0)
    upper_user_id: models.BigIntegerField[int, int] = models.BigIntegerField(default=0)
    started_at: models.DateTimeField[datetime | None, datetime | None] = (
        models.DateTimeField(null=True)
    )
    prepared_at: models.DateTimeField[datetime | None, datetime | None] = (
        models.DateTimeField(null=True)
    )
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now_add=True
    )


class UserTelegramLinkProxy(CoreUserTelegramLink):
    """Прокси для отображения привязок Telegram в разделе «Телеграм» админки."""

    class Meta:
        proxy = True
        app_label = "telegram_bot"
        verbose_name = CoreUserTelegramLink._meta.verbose_name or "Привязка Telegram"
        verbose_name_plural = (
            CoreUserTelegramLink._meta.verbose_name_plural or "Привязки Telegram"
        )


class DeadlineExtensionRequestProxy(CoreDeadlineExtensionRequest):
    """Прокси для отображения запросов продления дедлайна в разделе «Телеграм» админки."""

    class Meta:
        proxy = True
        app_label = "telegram_bot"
        verbose_name = (
            CoreDeadlineExtensionRequest._meta.verbose_name
            or "Запрос продления дедлайна"
        )
        verbose_name_plural = (
            CoreDeadlineExtensionRequest._meta.verbose_name_plural
            or "Запросы продления дедлайна"
        )
