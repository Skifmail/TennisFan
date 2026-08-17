"""
Core models.
"""

import secrets

from django.conf import settings
from django.contrib.admin.models import LogEntry
from django.db import models
from django.utils import timezone

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


# ---------------------------------------------------------------------------
# Лента активности платформы (вечный журнал действий пользователей)
# ---------------------------------------------------------------------------


# Соответствие кода валюты её символу для компактного отображения сумм.
_CURRENCY_SYMBOLS: dict[str, str] = {
    "RUB": "₽",
    "USD": "$",
    "EUR": "€",
}


class PlatformActivityEvent(models.Model):
    """Событие активности на платформе.

    Единый журнал всех значимых действий пользователей и игроков: регистрации,
    оплаты (подписки, тарифы, турниры, донаты), внесение и подтверждение
    результатов матчей, регистрации на турниры, спарринги и т.д. Записи никогда
    не удаляются автоматически и используются для построения ленты активности
    на «Панели платформы».

    Имя действующего лица сохраняется отдельным снимком (``actor_name``), чтобы
    запись оставалась читаемой даже после удаления пользователя или смены ФИО.
    """

    class EventType(models.TextChoices):
        """Тип события для фильтрации и отображения в ленте."""

        REGISTRATION = "registration", "Регистрация"
        PAYMENT_SUBSCRIPTION = "payment_subscription", "Оплата подписки"
        PAYMENT_CLUB_PLAN = "payment_club_plan", "Оплата тарифа клуба"
        PAYMENT_CLUB_FEE = "payment_club_fee", "Оплата членского взноса"
        PAYMENT_TOURNAMENT = "payment_tournament", "Оплата турнира"
        PAYMENT_DONATION = "payment_donation", "Донат"
        TOURNAMENT_REGISTERED = "tournament_registered", "Регистрация на турнир"
        MATCH_RESULT_PROPOSED = "match_result_proposed", "Внесён результат матча"
        MATCH_RESULT_CONFIRMED = (
            "match_result_confirmed",
            "Подтверждён результат матча",
        )
        MATCH_RESULT_REJECTED = "match_result_rejected", "Отклонён результат матча"
        SPARRING_CREATED = "sparring_created", "Создан спарринг"
        SPARRING_APPLIED = "sparring_applied", "Отклик на спарринг"
        SPARRING_APPROVED = "sparring_approved", "Одобрен отклик на спарринг"
        SPARRING_REJECTED = "sparring_rejected", "Отклонён отклик на спарринг"
        SPARRING_INVITED = "sparring_invited", "Приглашение на спарринг"
        DOUBLES_CREATED = "doubles_created", "Создан парный спарринг"
        DOUBLES_JOIN_REQUESTED = "doubles_join_requested", "Заявка в парный спарринг"
        DOUBLES_JOIN_APPROVED = (
            "doubles_join_approved",
            "Одобрена заявка в парный спарринг",
        )
        DOUBLES_JOIN_REJECTED = (
            "doubles_join_rejected",
            "Отклонена заявка в парный спарринг",
        )
        CLUB_JOIN_REQUESTED = "club_join_requested", "Заявка в клуб"
        CLUB_JOINED = "club_joined", "Вступление в клуб"
        CLUB_JOIN_REJECTED = "club_join_rejected", "Отклонена заявка в клуб"
        COACH_APPLICATION = "coach_application", "Заявка на тренера"
        TRAINING_PUBLISHED = "training_published", "Опубликована тренировка"
        TRAINING_ENROLLED = "training_enrolled", "Запись на тренировку"
        COMMENT_ADDED = "comment_added", "Комментарий"
        SUBSCRIPTION_CANCELLED = "subscription_cancelled", "Отмена подписки"
        FANCOIN_CHARGE = "fancoin_charge", "Списание FAN-коинов"
        FANCOIN_REFUND = "fancoin_refund", "Возврат FAN-коинов"
        PHOTO_ADDED = "photo_added", "Добавлено фото"

    #: Типы событий, относящиеся к оплатам (для подсветки и группировки в UI).
    PAYMENT_EVENT_TYPES = frozenset(
        {
            EventType.PAYMENT_SUBSCRIPTION,
            EventType.PAYMENT_CLUB_PLAN,
            EventType.PAYMENT_CLUB_FEE,
            EventType.PAYMENT_TOURNAMENT,
            EventType.PAYMENT_DONATION,
        }
    )

    #: События, которые можно показать всем посетителям главной страницы.
    PUBLIC_FEED_EVENT_TYPES = frozenset(
        {
            EventType.REGISTRATION,
            EventType.TOURNAMENT_REGISTERED,
            EventType.MATCH_RESULT_PROPOSED,
            EventType.MATCH_RESULT_CONFIRMED,
            EventType.SPARRING_CREATED,
            EventType.SPARRING_APPLIED,
            EventType.SPARRING_APPROVED,
            EventType.DOUBLES_CREATED,
            EventType.DOUBLES_JOIN_REQUESTED,
            EventType.DOUBLES_JOIN_APPROVED,
            EventType.CLUB_JOINED,
            EventType.TRAINING_PUBLISHED,
            EventType.TRAINING_ENROLLED,
            EventType.COMMENT_ADDED,
            EventType.PHOTO_ADDED,
        }
    )

    event_type = models.CharField(
        "Тип события",
        max_length=40,
        choices=EventType.choices,
        db_index=True,
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="platform_activity_events",
        verbose_name="Пользователь",
        help_text="Кто совершил действие. NULL — системное событие или удалённый пользователь.",
    )
    actor_name = models.CharField(
        "Имя на момент события",
        max_length=255,
        blank=True,
        db_index=True,
        help_text="Снимок ФИО/email действующего лица, сохраняется навсегда.",
    )
    actor_role = models.CharField(
        "Статус пользователя",
        max_length=32,
        blank=True,
        default="",
        help_text="Снимок роли действующего лица: Игрок, Тренер, Админ и т.д.",
    )
    description = models.CharField(
        "Описание действия",
        max_length=500,
        blank=True,
    )
    amount = models.DecimalField(
        "Сумма",
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Заполняется только для событий-оплат.",
    )
    currency = models.CharField(
        "Валюта",
        max_length=8,
        default="RUB",
        blank=True,
    )
    target_url = models.CharField(
        "Ссылка на объект",
        max_length=500,
        blank=True,
        help_text="Относительный URL связанного объекта (матч, турнир и т.д.).",
    )
    metadata = models.JSONField(
        "Дополнительные данные",
        default=dict,
        blank=True,
    )
    dedupe_key = models.CharField(
        "Ключ дедупликации",
        max_length=120,
        blank=True,
        default="",
        db_index=True,
        help_text="Стабильный ключ источника события для защиты от дубликатов.",
    )
    created_at = models.DateTimeField(
        "Когда произошло",
        default=timezone.now,
        db_index=True,
    )

    class Meta:
        verbose_name = "событие активности"
        verbose_name_plural = "лента активности платформы"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["event_type", "created_at"]),
            models.Index(fields=["actor", "created_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["dedupe_key"],
                condition=models.Q(dedupe_key__gt=""),
                name="uniq_activity_dedupe_key",
            ),
        ]

    def __str__(self) -> str:
        """Вернуть краткое строковое представление события.

        Returns:
            str: Дата, имя действующего лица и тип события.
        """
        return (
            f"{self.created_at:%d.%m.%Y %H:%M} / {self.actor_name or 'Система'} / "
            f"{self.get_event_type_display()}"
        )

    def get_actor_display(self) -> str:
        """Вернуть имя действующего лица для отображения в ленте.

        Returns:
            str: Снимок имени, либо актуальное имя пользователя, либо «Система».
        """
        if self.actor_name:
            return str(self.actor_name)
        if self.actor_id:
            from apps.users.display import format_user_display_name

            name = format_user_display_name(self.actor)
            if name:
                return name
        return "Система"

    def get_role_modifier(self) -> str:
        """Вернуть CSS-модификатор бейджа статуса пользователя.

        Returns:
            str: Суффикс класса «admin», «coach», «club_organizer», «player»
            или «default».
        """
        role_map: dict[str, str] = {
            "Админ": "admin",
            "Тренер": "coach",
            "Организатор клуба": "club_organizer",
            "Игрок": "player",
        }
        return role_map.get(self.actor_role or "", "default")

    @property
    def is_payment(self) -> bool:
        """Является ли событие оплатой.

        Returns:
            bool: True, если тип события относится к оплатам.
        """
        return self.event_type in self.PAYMENT_EVENT_TYPES

    def get_tone(self) -> str:
        """Вернуть визуальный тон события для подсветки бейджа в UI.

        Returns:
            str: Один из «payment», «registration», «match», «tournament»,
            «sparring», «photo», «default».
        """
        et = self.EventType
        if self.is_payment:
            return "payment"
        if self.event_type in {
            et.MATCH_RESULT_REJECTED,
            et.SPARRING_REJECTED,
            et.DOUBLES_JOIN_REJECTED,
            et.CLUB_JOIN_REJECTED,
            et.SUBSCRIPTION_CANCELLED,
        }:
            return "rejected"
        if self.event_type == et.REGISTRATION:
            return "registration"
        if self.event_type in {
            et.MATCH_RESULT_PROPOSED,
            et.MATCH_RESULT_CONFIRMED,
        }:
            return "match"
        if self.event_type == et.TOURNAMENT_REGISTERED:
            return "tournament"
        if self.event_type in {
            et.SPARRING_CREATED,
            et.SPARRING_APPLIED,
            et.SPARRING_APPROVED,
            et.SPARRING_INVITED,
            et.DOUBLES_CREATED,
            et.DOUBLES_JOIN_REQUESTED,
            et.DOUBLES_JOIN_APPROVED,
        }:
            return "sparring"
        if self.event_type in {
            et.CLUB_JOIN_REQUESTED,
            et.CLUB_JOINED,
        }:
            return "club"
        if self.event_type in {
            et.COACH_APPLICATION,
            et.TRAINING_PUBLISHED,
            et.TRAINING_ENROLLED,
        }:
            return "training"
        if self.event_type == et.COMMENT_ADDED:
            return "comment"
        if self.event_type == et.PHOTO_ADDED:
            return "photo"
        if self.event_type in {
            et.FANCOIN_CHARGE,
            et.FANCOIN_REFUND,
        }:
            return "fancoin"
        return "default"

    def get_public_target_url(self) -> str:
        """Вернуть публичную ссылку события, скрывая адреса админки.

        Returns:
            str: Относительный URL для посетителей сайта либо пустая строка.
        """
        url = (self.target_url or "").strip()
        if not url:
            return ""
        if url.startswith("/admin/") or url.startswith("admin/"):
            return ""
        return url

    def get_actor_profile_url(self) -> str:
        """Вернуть URL публичного профиля действующего лица.

        Returns:
            str: Ссылка на профиль игрока либо пустая строка.
        """
        actor = self.actor
        if actor is None:
            return ""
        player = getattr(actor, "player", None)
        if player is None or getattr(player, "is_bye", False):
            return ""
        from django.urls import reverse

        try:
            return str(reverse("profile", kwargs={"pk": player.pk}))
        except Exception:  # noqa: BLE001
            return ""

    def get_amount_display(self) -> str:
        """Отформатировать сумму с символом валюты.

        Returns:
            str: Например «1 500 ₽» или пустая строка, если суммы нет.
        """
        if self.amount is None:
            return ""
        symbol = _CURRENCY_SYMBOLS.get(self.currency, self.currency or "")
        # Целые суммы показываем без копеек, дробные — с двумя знаками.
        if self.amount == self.amount.to_integral_value():
            amount_str = f"{int(self.amount):,}".replace(",", " ")
        else:
            amount_str = f"{self.amount:.2f}"
        return f"{amount_str} {symbol}".strip()


class PlatformDashboardSeen(models.Model):
    """Отметка последнего просмотра панели управления staff-пользователем.

    Используется для индикатора новых событий в ленте активности: события,
    созданные после ``seen_at``, считаются непросмотренными.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="platform_dashboard_seen",
        verbose_name="Пользователь",
    )
    last_seen_event_id = models.PositiveBigIntegerField(
        "ID последнего просмотренного события",
        default=0,
        help_text="События с большим id считаются непросмотренными.",
    )
    seen_at = models.DateTimeField(
        "Последний просмотр",
        default=timezone.now,
    )

    class Meta:
        verbose_name = "просмотр панели управления"
        verbose_name_plural = "просмотры панели управления"

    def __str__(self) -> str:
        """Вернуть краткое представление отметки просмотра.

        Returns:
            str: Email пользователя и время последнего просмотра.
        """
        return f"{self.user_id} @ {self.seen_at:%d.%m.%Y %H:%M}"


class AdminActionLog(LogEntry):
    """Прокси-модель для просмотра полного журнала действий Django admin."""

    class Meta:
        proxy = True
        verbose_name = "Действие в админке"
        verbose_name_plural = "Журнал действий"
        ordering = ("-action_time", "-pk")

    def __str__(self) -> str:
        """Вернуть краткое представление записи журнала.

        Returns:
            str: Объект и время действия.
        """
        return f"{self.object_repr} ({self.action_time:%d.%m.%Y %H:%M})"


class OutboundEmail(models.Model):
    """Журнал исходящих электронных писем платформы.

    Args:
        models.Model: Базовый класс ORM.
    """

    class Status(models.TextChoices):
        """Статус отправки письма."""

        SENT = "sent", "Отправлено"
        FAILED = "failed", "Ошибка"

    class Category(models.TextChoices):
        """Раздел письма для фильтрации в админке."""

        REGISTRATION = "registration", "Регистрация и подтверждение почты"
        NEW_TOURNAMENT = "new_tournament", "Новые турниры"
        TOURNAMENT = "tournament", "Турниры"
        SUBSCRIPTION = "subscription", "Подписки и платежи"
        SECURITY = "security", "Безопасность"
        CLUBS = "clubs", "Клубы"
        SUPPORT = "support", "Поддержка"
        OTHER = "other", "Прочие"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="outbound_emails",
        verbose_name="Пользователь",
    )
    to_email = models.EmailField("Кому", db_index=True)
    from_email = models.CharField("От кого", max_length=254, blank=True)
    subject = models.CharField("Тема", max_length=998, blank=True)
    body_text = models.TextField("Текст", blank=True)
    body_html = models.TextField("HTML", blank=True)
    category = models.CharField(
        "Раздел",
        max_length=32,
        choices=Category.choices,
        default=Category.OTHER,
        db_index=True,
    )
    status = models.CharField(
        "Статус",
        max_length=16,
        choices=Status.choices,
        default=Status.SENT,
        db_index=True,
    )
    error_message = models.TextField("Ошибка", blank=True)
    sent_at = models.DateTimeField("Отправлено", default=timezone.now, db_index=True)

    class Meta:
        verbose_name = "Электронное письмо"
        verbose_name_plural = "Все письма"
        ordering = ("-sent_at", "-id")

    def __str__(self) -> str:
        """Краткое описание письма для админки.

        Returns:
            str: Тема, получатель и время.
        """
        subject = self.subject or "(без темы)"
        return f"{subject} → {self.to_email} ({self.sent_at:%d.%m.%Y %H:%M})"


class RegistrationOutboundEmail(OutboundEmail):
    """Прокси: письма регистрации и подтверждения email."""

    class Meta:
        proxy = True
        verbose_name = "Письмо регистрации"
        verbose_name_plural = "Регистрация и подтверждение почты"


class NewTournamentOutboundEmail(OutboundEmail):
    """Прокси: уведомления о новых турнирах."""

    class Meta:
        proxy = True
        verbose_name = "Письмо о новом турнире"
        verbose_name_plural = "Новые турниры"


class TournamentOutboundEmail(OutboundEmail):
    """Прокси: письма по турнирам (оплата, отмена, напоминания)."""

    class Meta:
        proxy = True
        verbose_name = "Письмо по турниру"
        verbose_name_plural = "Турниры"


class SubscriptionOutboundEmail(OutboundEmail):
    """Прокси: подписки, платежи и донаты."""

    class Meta:
        proxy = True
        verbose_name = "Письмо подписки/платежа"
        verbose_name_plural = "Подписки и платежи"


class SecurityOutboundEmail(OutboundEmail):
    """Прокси: security-письма аккаунта."""

    class Meta:
        proxy = True
        verbose_name = "Письмо безопасности"
        verbose_name_plural = "Безопасность"


class ClubsOutboundEmail(OutboundEmail):
    """Прокси: клубные письма."""

    class Meta:
        proxy = True
        verbose_name = "Клубное письмо"
        verbose_name_plural = "Клубы"


class SupportOutboundEmail(OutboundEmail):
    """Прокси: письма поддержки."""

    class Meta:
        proxy = True
        verbose_name = "Письмо поддержки"
        verbose_name_plural = "Поддержка"


class OtherOutboundEmail(OutboundEmail):
    """Прокси: прочие письма без отдельного раздела."""

    class Meta:
        proxy = True
        verbose_name = "Прочее письмо"
        verbose_name_plural = "Прочие"
