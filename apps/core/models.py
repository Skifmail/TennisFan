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
        Заглушка для обратной совместимости.

        Returns:
            int: Всегда 0, так как Telegram-support удален.
        """
        return 0


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


class LegalAcceptanceLog(models.Model):
    """Журнал фиксации согласий и акцептов юридических документов.

    Модель хранит подтверждение того, что пользователь принял конкретный
    юридический документ: согласие на обработку ПДн, политику
    конфиденциальности, публичную оферту и т.д.
    """

    class DocumentSlug(models.TextChoices):
        """Поддерживаемые типы юридических документов."""

        PERSONAL_DATA = "personal-data", "Согласие на обработку ПДн"
        PRIVACY = "privacy", "Политика конфиденциальности"
        OFFER = "offer", "Публичная оферта"
        TERMS = "terms", "Пользовательское соглашение"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="legal_acceptances",
        verbose_name="Пользователь",
    )
    document_slug = models.CharField(
        "Документ",
        max_length=32,
        choices=DocumentSlug.choices,
        db_index=True,
    )
    document_version = models.CharField(
        "Версия документа",
        max_length=64,
        default="unknown",
        db_index=True,
    )
    source = models.CharField(
        "Источник акцепта",
        max_length=64,
        default="site",
        help_text="Например: registration, payment, profile.",
    )
    ip_address = models.GenericIPAddressField(
        "IP-адрес",
        null=True,
        blank=True,
    )
    user_agent = models.TextField("User-Agent", blank=True, default="")
    metadata = models.JSONField(
        "Дополнительные данные",
        default=dict,
        blank=True,
    )
    accepted_at = models.DateTimeField("Дата и время фиксации", auto_now_add=True)

    class Meta:
        ordering = ["-accepted_at"]
        verbose_name = "фиксация согласия с документом"
        verbose_name_plural = "фиксации согласий с документами"

    def __str__(self) -> str:
        """Вернуть краткое представление записи журнала.

        Returns:
            str: Пользователь, документ и время фиксации.
        """
        return (
            f"{self.user} / {self.get_document_slug_display()} / "
            f"{self.accepted_at:%d.%m.%Y %H:%M}"
        )


class UserConsent(models.Model):
    """Запись о согласии пользователя с юридическим документом (платформа или клуб)."""

    class ConsentType(models.TextChoices):
        """Тип согласия для учёта и отчётности."""

        PLATFORM_OFFER = "platform_offer", "Публичная оферта платформы"
        PRIVACY_POLICY = "privacy_policy", "Политика конфиденциальности"
        CLUB_ORGANIZER_RULES = (
            "club_organizer_rules",
            "Правила для организаторов клубов",
        )
        CLUB_OFFER = "club_offer", "Оферта клуба"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="user_consents",
        verbose_name="Пользователь",
    )
    consent_type = models.CharField(
        "Тип согласия",
        max_length=64,
        choices=ConsentType.choices,
        db_index=True,
    )
    club = models.ForeignKey(
        "clubs.Club",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="user_consents",
        verbose_name="Клуб",
        help_text="Только для согласия с офертой клуба",
    )
    document_version = models.CharField("Версия документа", max_length=64)
    accepted_at = models.DateTimeField("Принято", auto_now_add=True)
    ip_address = models.GenericIPAddressField(
        "IP-адрес",
        null=True,
        blank=True,
    )
    user_agent = models.CharField(
        "User-Agent",
        max_length=500,
        blank=True,
        default="",
    )

    class Meta:
        verbose_name = "согласие пользователя"
        verbose_name_plural = "согласия пользователей"
        ordering = ["-accepted_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "consent_type", "document_version"],
                condition=models.Q(club__isnull=True),
                name="uniq_userconsent_platform",
            ),
            models.UniqueConstraint(
                fields=["user", "consent_type", "club", "document_version"],
                condition=models.Q(club__isnull=False),
                name="uniq_userconsent_club",
            ),
        ]

    def __str__(self) -> str:
        club_part = f" / {self.club_id}" if self.club_id else ""
        return (
            f"{self.user_id} / {self.consent_type}{club_part} / "
            f"v{self.document_version}"
        )


class SupportThread(models.Model):
    """
    Диалог поддержки между пользователем (или гостем) и администрацией.

    Хранит агрегированное состояние диалога и счетчики непрочитанных сообщений
    для каждой стороны.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="support_threads",
        null=True,
        blank=True,
        help_text="Зарегистрированный пользователь (null для гостя).",
    )
    guest_email = models.EmailField(
        "Email гостя",
        blank=True,
        help_text="Email гостя, если диалог создан без авторизации.",
    )
    guest_name = models.CharField(
        "Имя гостя",
        max_length=200,
        blank=True,
        help_text="Имя гостя, если диалог создан без авторизации.",
    )
    guest_session_key = models.CharField(
        "Ключ сессии гостя",
        max_length=64,
        blank=True,
        db_index=True,
        help_text="Используется для доступа гостя к своему диалогу из виджета.",
    )
    last_message_at = models.DateTimeField("Последнее сообщение", db_index=True)
    admin_unread_count = models.PositiveIntegerField(
        "Непрочитано админом",
        default=0,
    )
    user_unread_count = models.PositiveIntegerField(
        "Непрочитано пользователем",
        default=0,
    )
    is_closed = models.BooleanField("Диалог закрыт", default=False)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        ordering = ["-last_message_at"]
        verbose_name = "диалог поддержки"
        verbose_name_plural = "диалоги поддержки"

    def __str__(self) -> str:
        if self.user_id:
            return f"Диалог #{self.pk} / user={self.user_id}"
        return f"Диалог #{self.pk} / guest={self.guest_email or 'unknown'}"


class SupportMessage(models.Model):
    """
    Сообщение в диалоге поддержки.
    """

    thread = models.ForeignKey(
        SupportThread,
        on_delete=models.CASCADE,
        related_name="messages",
    )

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
    subject = models.CharField("Тема", max_length=200, blank=True)
    text = models.TextField("Текст сообщения")
    is_from_admin = models.BooleanField("От администратора", default=False)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    edited_at = models.DateTimeField("Изменено", null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "сообщение поддержки"
        verbose_name_plural = "сообщения поддержки"

    def __str__(self):
        if self.user:
            return f"#{self.pk} {'(админ)' if self.is_from_admin else ''} {self.user}"
        return f"#{self.pk} {'(админ)' if self.is_from_admin else ''} Гость: {self.guest_name or 'Без имени'}"


class SupportConversation(SupportThread):
    """
    Прокси-модель для отображения диалогов в Django Admin.
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
    lat = models.FloatField(
        "Широта",
        null=True,
        blank=True,
        help_text="Широта города для отображения на карте.",
    )
    lng = models.FloatField(
        "Долгота",
        null=True,
        blank=True,
        help_text="Долгота города для отображения на карте.",
    )

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
