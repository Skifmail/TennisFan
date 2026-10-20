"""Настройки каналов, устройства и долговечная очередь уведомлений."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import ClassVar

from django.conf import settings
from django.core.validators import MaxLengthValidator
from django.db import models

from apps.users.models import Notification, User

from .domain.rules import BODY_LIMIT, TITLE_LIMIT


class EventPolicy(models.Model):
    """Разрешения администратора для одного события.

    Attributes:
        key: Стабильный ключ из реестра событий.
        push_enabled: Разрешена внешняя push-доставка.
        email_enabled: Разрешены письма.
        inbox_enabled: Разрешены новые записи в ЛК.
    """

    key: models.CharField[str, str] = models.CharField(
        "Код события", max_length=100, primary_key=True
    )
    title: models.CharField[str, str] = models.CharField("Событие", max_length=160)
    group: models.CharField[str, str] = models.CharField("Раздел", max_length=60)
    description: models.TextField[str, str] = models.TextField(
        "Когда и кому отправляется"
    )
    push_enabled: models.BooleanField[bool, bool] = models.BooleanField(
        "Push", default=True
    )
    email_enabled: models.BooleanField[bool, bool] = models.BooleanField(
        "Email", default=True
    )
    inbox_enabled: models.BooleanField[bool, bool] = models.BooleanField(
        "ЛК", default=True
    )

    class Meta:
        verbose_name = "Событие и каналы"
        verbose_name_plural = "События и каналы"
        ordering: ClassVar = ["group", "title"]

    def __str__(self) -> str:
        """Возвращает понятное администратору название события."""
        return str(self.title)


class NotificationPreference(models.Model):
    """Два глобальных пользовательских согласия без настройки ЛК.

    Attributes:
        user: Владелец настроек.
        push_enabled: Явное согласие на персональные push; изначально выключено.
        email_enabled: Сохраняет существующее поведение почты по умолчанию.
    """

    user_id: int
    user: models.OneToOneField[User, User] = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE
    )
    push_enabled: models.BooleanField[bool, bool] = models.BooleanField(
        "Push-уведомления", default=False
    )
    email_enabled: models.BooleanField[bool, bool] = models.BooleanField(
        "Email-уведомления", default=True
    )
    updated_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        "Изменено", auto_now=True
    )

    class Meta:
        verbose_name = "Согласие пользователя"
        verbose_name_plural = "Согласия пользователей"


class PushDevice(models.Model):
    """Подтверждённая подписка браузера без передачи email и ID игрока провайдеру.

    Attributes:
        device_hash: Хэш случайного HttpOnly-cookie устройства.
        external_id: Случайное поколение привязки для отзыва старых вкладок.
        subscription_id: Проверенный доставленным кодом адрес подписки OneSignal.
        user: Текущий владелец привязки.
        active: Подписка подтверждена одноразовым кодом и разрешена пользователем.
        expires_at: Конечный срок привязки, вычисленный из настроек текущей сессии.
    """

    device_hash: models.CharField[str, str] = models.CharField(
        max_length=64, unique=True
    )
    external_id: models.UUIDField[uuid.UUID, uuid.UUID] = models.UUIDField(
        default=uuid.uuid4, unique=True, editable=False
    )
    subscription_id: models.UUIDField[uuid.UUID | None, uuid.UUID | None] = (
        models.UUIDField(null=True, blank=True, unique=True, editable=False)
    )
    user_id: int
    user: models.ForeignKey[User, User] = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE
    )
    active: models.BooleanField[bool, bool] = models.BooleanField(default=False)
    expires_at: models.DateTimeField[datetime, datetime] = models.DateTimeField()
    updated_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now=True
    )

    class Meta:
        verbose_name = "Push-устройство"
        verbose_name_plural = "Push-устройства"


class PushConfirmation(models.Model):
    """Одноразовое доказательство получения push конкретным браузером.

    Attributes:
        device: Браузер, запросивший подтверждение в авторизованной сессии.
        subscription_id: Единственная подписка, на которую отправлен секрет.
        generation: Поколение привязки до выхода или смены аккаунта.
        token_hash: SHA-256 длинного секрета либо HMAC короткого кода; без открытого кода.
        expires_at: Предельный срок использования кода.
        requested_at: Время запроса для ограничения повторных отправок.
        manual_code: Признак короткого кода с отдельным лимитом ввода.
    """

    device: models.OneToOneField[PushDevice, PushDevice] = models.OneToOneField(
        PushDevice, on_delete=models.CASCADE
    )
    subscription_id: models.UUIDField[uuid.UUID, uuid.UUID] = models.UUIDField(
        unique=True
    )
    generation: models.UUIDField[uuid.UUID, uuid.UUID] = models.UUIDField()
    token_hash: models.CharField[str, str] = models.CharField(
        max_length=64, unique=True
    )
    expires_at: models.DateTimeField[datetime, datetime] = models.DateTimeField()
    requested_at: models.DateTimeField[datetime, datetime] = models.DateTimeField()
    manual_code: models.BooleanField[bool, bool] = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Подтверждение push-подписки"
        verbose_name_plural = "Подтверждения push-подписок"


class Broadcast(models.Model):
    """Короткая ручная рассылка с неизменяемым подтверждённым содержимым.

    Attributes:
        status: Черновик, очередь, сформирована или отменена.
        cursor_user_id: Курсор пакетного обхода пользователей.
        upper_user_id: Верхняя граница аудитории на момент подтверждения.
        target_user: При тесте единственный адресат — сам администратор.
        submission_id: Уникальный ключ формы для защиты от повторного POST.
        processed_users: Число обработанных получателей при формировании.
        inbox_created: Число реально созданных уведомлений ЛК.
    """

    title: models.CharField[str, str] = models.CharField(
        "Заголовок",
        max_length=TITLE_LIMIT,
        validators=[MaxLengthValidator(TITLE_LIMIT)],
    )
    body: models.TextField[str, str] = models.TextField(
        "Текст", max_length=BODY_LIMIT, validators=[MaxLengthValidator(BODY_LIMIT)]
    )
    url: models.CharField[str, str] = models.CharField(
        "Страница платформы", max_length=255, blank=True
    )
    send_push: models.BooleanField[bool, bool] = models.BooleanField(
        "Push", default=False
    )
    send_email: models.BooleanField[bool, bool] = models.BooleanField(
        "Email", default=False
    )
    send_inbox: models.BooleanField[bool, bool] = models.BooleanField(
        "ЛК", default=True
    )
    status: models.CharField[str, str] = models.CharField(
        "Состояние",
        max_length=16,
        default="draft",
        choices=[
            ("draft", "Черновик"),
            ("queued", "В очереди"),
            ("expanded", "Сформирована"),
            ("cancelled", "Отменена"),
        ],
    )
    created_by_id: int | None
    created_by: models.ForeignKey[User | None, User | None] = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="notification_broadcasts",
    )
    confirmed_by_id: int | None
    confirmed_by: models.ForeignKey[User | None, User | None] = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="confirmed_notification_broadcasts",
    )
    target_user_id: int | None
    target_user: models.ForeignKey[User | None, User | None] = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="test_notification_broadcasts",
    )
    is_test: models.BooleanField[bool, bool] = models.BooleanField(default=False)
    submission_id: models.UUIDField[uuid.UUID | None, uuid.UUID | None] = (
        models.UUIDField(null=True, unique=True, editable=False)
    )
    processed_users: models.PositiveIntegerField[int, int] = (
        models.PositiveIntegerField(default=0, editable=False)
    )
    inbox_created: models.PositiveIntegerField[int, int] = models.PositiveIntegerField(
        default=0, editable=False
    )
    cursor_user_id: models.BigIntegerField[int, int] = models.BigIntegerField(default=0)
    upper_user_id: models.BigIntegerField[int, int] = models.BigIntegerField(default=0)
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        "Создана", auto_now_add=True
    )
    confirmed_at: models.DateTimeField[datetime | None, datetime | None] = (
        models.DateTimeField("Подтверждена", null=True)
    )

    class Meta:
        verbose_name = "Ручная рассылка"
        verbose_name_plural = "Ручные рассылки"
        permissions: ClassVar = [
            ("send_broadcast", "Может подтверждать массовую отправку уведомлений")
        ]
        ordering: ClassVar = ["-created_at"]

    def __str__(self) -> str:
        """Возвращает заголовок рассылки без текста сообщения."""
        return str(self.title)


class Delivery(models.Model):
    """Одно задание доставки с уникальным ключом и ограниченными повторами.

    Attributes:
        key: Неизменяемый ключ идемпотентности API.
        status: Очередь, принято провайдером, пропущено, ошибка или неопределённо.
        attempts: Число попыток.
        provider_id: ID принятого сообщения; не подтверждение прочтения.
    """

    key: models.UUIDField[uuid.UUID, uuid.UUID] = models.UUIDField(
        default=uuid.uuid4, unique=True, editable=False
    )
    event_key: models.CharField[str, str] = models.CharField(max_length=100)
    user_id: int
    user: models.ForeignKey[User, User] = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE
    )
    channel: models.CharField[str, str] = models.CharField(
        max_length=8, choices=[("push", "Push"), ("email", "Email")]
    )
    title: models.CharField[str, str] = models.CharField(max_length=TITLE_LIMIT)
    body: models.TextField[str, str] = models.TextField(max_length=BODY_LIMIT)
    url: models.CharField[str, str] = models.CharField(max_length=255)
    broadcast_id: int | None
    broadcast: models.ForeignKey[Broadcast | None, Broadcast | None] = (
        models.ForeignKey(Broadcast, null=True, on_delete=models.PROTECT)
    )
    notification_id: int | None
    notification: models.ForeignKey[Notification | None, Notification | None] = (
        models.ForeignKey("users.Notification", null=True, on_delete=models.SET_NULL)
    )
    status: models.CharField[str, str] = models.CharField(
        "Статус", max_length=16, default="queued"
    )
    attempts: models.PositiveSmallIntegerField[int, int] = (
        models.PositiveSmallIntegerField(default=0)
    )
    available_at: models.DateTimeField[datetime, datetime] = models.DateTimeField()
    expires_at: models.DateTimeField[datetime, datetime] = models.DateTimeField()
    provider_id: models.CharField[str, str] = models.CharField(
        max_length=100, blank=True
    )
    error_code: models.CharField[str, str] = models.CharField(max_length=80, blank=True)
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now_add=True
    )
    finished_at: models.DateTimeField[datetime | None, datetime | None] = (
        models.DateTimeField(null=True)
    )

    class Meta:
        verbose_name = "Задание доставки"
        verbose_name_plural = "Журнал доставки"
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["broadcast", "user", "channel"],
                name="unique_broadcast_user_channel",
            )
        ]
        indexes: ClassVar = [models.Index(fields=["status", "available_at"])]


class PushCodeLimit(models.Model):
    """Ограничивает подбор коротких push-кодов для всего аккаунта.

    Attributes:
        user: Владелец общего лимита всех браузеров.
        failures: Количество неверных вводов в текущем окне.
        resets_at: Момент сброса ограничения.
    """

    user: models.OneToOneField[User, User] = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE
    )
    failures: models.PositiveSmallIntegerField[int, int] = (
        models.PositiveSmallIntegerField(default=0)
    )
    resets_at: models.DateTimeField[datetime, datetime] = models.DateTimeField()
