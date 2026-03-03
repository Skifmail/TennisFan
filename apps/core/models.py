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
        null=True,
        unique=True,
        db_index=True,
        help_text="Пусто/NULL = токен не выдан или уже использован. Уникален, чтобы один токен не привязал двух пользователей.",
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
        from datetime import timedelta

        from django.utils import timezone

        if self.binding_token:
            if (
                self.token_created_at
                and timezone.now() - self.token_created_at < timedelta(hours=24)
            ):
                return self.binding_token
        self.binding_token = secrets.token_urlsafe(32)
        self.token_created_at = timezone.now()
        self.save(update_fields=["binding_token", "token_created_at"])
        return self.binding_token

    def migrate_guest_messages(self):
        """
        Переносит гостевые SupportMessage к этому пользователю, если они связаны с тем же chat_id.
        Вызывается после привязки Telegram бота зарегистрированным пользователем.
        Возвращает количество перенесенных сообщений.
        """
        if not self.telegram_chat_id:
            return 0

        # Ищем гостевые сообщения с таким же chat_id
        guest_messages = SupportMessage.objects.filter(
            user__isnull=True, guest_telegram_chat_id=self.telegram_chat_id
        )

        # Переносим найденные сообщения к пользователю
        updated_count = guest_messages.update(
            user=self.user,
            guest_telegram_chat_id=None,  # Очищаем, так как теперь это зарегистрированный пользователь
            guest_binding_token="",  # Очищаем токен
        )

        return updated_count


class TelegramTransferConsentLog(models.Model):
    """
    Журнал согласий на передачу данных в Telegram.
    Хранит юридически значимые атрибуты согласия для возможных споров/проверок.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="telegram_transfer_consents",
    )
    consent_version = models.CharField(
        "Версия текста согласия",
        max_length=32,
        default="v1",
        db_index=True,
    )
    ip_address = models.GenericIPAddressField(
        "IP-адрес",
        null=True,
        blank=True,
    )
    user_agent = models.TextField("User-Agent", blank=True, default="")
    consented_at = models.DateTimeField("Дата и время согласия", auto_now_add=True)

    class Meta:
        ordering = ["-consented_at"]
        verbose_name = "согласие на передачу данных в Telegram"
        verbose_name_plural = "согласия на передачу данных в Telegram"

    def __str__(self):
        return (
            f"{self.user} / {self.consent_version} / {self.consented_at:%d.%m.%Y %H:%M}"
        )


class SupportMessage(models.Model):
    """
    Сообщение в системе поддержки: от пользователя (с сайта или из Telegram)
    или от администратора (ответ в Telegram).
    Поддерживает как зарегистрированных пользователей, так и гостей (незарегистрированных).
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="support_messages",
        null=True,
        blank=True,
        help_text="Зарегистрированный пользователь (null для гостей)",
    )
    guest_name = models.CharField(
        "Имя гостя",
        max_length=200,
        blank=True,
        help_text="Имя незарегистрированного пользователя",
    )
    guest_contact = models.CharField(
        "Контакт гостя",
        max_length=200,
        blank=True,
        help_text="Email или телефон незарегистрированного пользователя",
    )
    guest_telegram_username = models.CharField(
        "Telegram username гостя",
        max_length=100,
        blank=True,
        help_text="Telegram username незарегистрированного пользователя (без @)",
    )
    guest_telegram_chat_id = models.BigIntegerField(
        "Telegram chat_id гостя",
        null=True,
        blank=True,
        db_index=True,
        help_text="Chat ID гостя в Telegram (заполняется после привязки бота)",
    )
    guest_binding_token = models.CharField(
        "Токен привязки для гостя",
        max_length=64,
        blank=True,
        unique=True,
        db_index=True,
        help_text="Токен для привязки Telegram бота гостю (t.me/bot?start=TOKEN)",
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
        if self.user:
            return f"#{self.pk} {'(админ)' if self.is_from_admin else ''} {self.user}"
        else:
            return f"#{self.pk} {'(админ)' if self.is_from_admin else ''} Гость: {self.guest_name or 'Без имени'}"


class SupportConversation(SupportMessage):
    """
    Прокси-модель для группировки сообщений по пользователям в админке.
    Представляет "диалог" с пользователем.
    """

    class Meta:
        proxy = True
        verbose_name = "диалог поддержки"
        verbose_name_plural = "диалоги поддержки"


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


# ---------------------------------------------------------------------------
# Города для автодополнения (заполняется из кортов, тренеров, тренировок и т.д.)
# ---------------------------------------------------------------------------


class City(models.Model):
    """
    Справочник городов для автодополнения в полях ввода.
    Заполняется миграцией и при необходимости синхронизируется из других моделей.
    """

    name = models.CharField("Название", max_length=100, unique=True, db_index=True)

    class Meta:
        verbose_name = "город"
        verbose_name_plural = "города"
        ordering = ["name"]

    def __str__(self) -> str:
        return str(self.name)


# ---------------------------------------------------------------------------
# Соцсети в футере (редактируются в админке)
# ---------------------------------------------------------------------------


class FooterSocialLink(models.Model):
    """
    Одна ссылка на соцсеть в футере: URL + иконка (загрузка SVG или путь в static).
    """

    name = models.CharField(
        "Название",
        max_length=100,
        help_text="Например: Telegram, ВКонтакте. Используется в подсказке (aria-label).",
    )
    url = models.URLField("Ссылка", max_length=500)
    icon = models.FileField(
        "Иконка (SVG)",
        upload_to="footer_social/",
        blank=True,
        help_text="Загрузите SVG-файл иконки. Либо укажите путь в поле «Путь к иконке».",
    )
    icon_path = models.CharField(
        "Путь к иконке в static",
        max_length=200,
        blank=True,
        help_text="Если иконка не загружена: путь относительно static, например images/VK.svg.",
    )
    order = models.PositiveSmallIntegerField("Порядок", default=0)

    class Meta:
        verbose_name = "ссылка на соцсеть"
        verbose_name_plural = "Соцсети"
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.name} ({self.url})"

    def get_icon_url(self):
        """URL иконки: загруженный файл или None (в шаблоне подставится static по icon_path)."""
        if self.icon:
            return self.icon.url
        return None
