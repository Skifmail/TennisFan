"""Хранилище городских комнат, участия и журнала изменений."""

from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from .domain import MAX_CHAT_TITLE_LENGTH, MAX_MESSAGE_LENGTH, MAX_REPORT_LENGTH


class ChatRoom(models.Model):
    """Общая комната населённого пункта.

    Attributes:
        city: Однозначная ссылка на населённый пункт либо пустое значение.
        title: Название отдельного чата без привязки к городу.
        is_active: Разрешён ли доступ к комнате.
        rules: Закреплённые правила общения.
        is_created: Создано ли сообщество, а не только техническая заготовка.
        administrator: Единственный управляющий комнатой, не сотрудник платформы.
    """

    city = models.OneToOneField(
        "core.City",
        on_delete=models.PROTECT,
        related_name="chat_room",
        null=True,
        blank=True,
    )
    title = models.CharField(
        "Название отдельного чата", max_length=MAX_CHAT_TITLE_LENGTH, blank=True
    )
    is_active = models.BooleanField("Открыта", default=True)
    is_created = models.BooleanField("Сообщество создано", default=True)
    administrator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Администратор чата",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="administered_chat_rooms",
    )
    rules = models.TextField(
        "Закреплённые правила",
        default="Общайтесь уважительно. Без спама, оскорблений и публикации чужих контактов.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(city__isnull=False, title="")
                    | (models.Q(city__isnull=True) & ~models.Q(title=""))
                ),
                name="chat_room_city_or_title",
            ),
        ]
        verbose_name = "городской чат"
        verbose_name_plural = "городские чаты"

    def __str__(self) -> str:
        """Возвращает название комнаты для администрирования."""
        return self.display_label

    @property
    def display_name(self) -> str:
        """Возвращает краткое имя городского или отдельного сообщества."""
        return self.city.name if self.city is not None else self.title

    @property
    def display_label(self) -> str:
        """Возвращает имя с регионом для городов и название отдельного чата."""
        return str(self.city) if self.city_id else self.title

    @property
    def join_query(self) -> str:
        """Возвращает однозначный параметр формы личных согласий."""
        return f"city_id={self.city_id}" if self.city_id else f"room_id={self.pk}"

    def clean(self) -> None:
        """Проверяет взаимоисключающие способы именования комнаты.

        Raises:
            ValidationError: Нет имени либо городскому чату задано отдельное название.
        """
        super().clean()
        self.title = self.title.strip()
        if bool(self.city_id) == bool(self.title):
            raise ValidationError(
                "Выберите город ИЛИ укажите название отдельного чата."
            )


class ChatMembership(models.Model):
    """Участие пользователя независимо от города его профиля.

    Attributes:
        is_active: Вступил ли пользователь в комнату.
        is_moderator: Может ли модерировать эту комнату.
        banned_until: До какого момента запрещено писать.
        last_read_message_id: Монотонная отметка прочтения.
        notifications_enabled: Получать ли уведомления об ответах.
        consent_version: Отпечаток комплекта лично подтверждённых документов.
        consent_id: Идентификатор соответствующего события в журнале Ядра.
        consented_at: Время личного подтверждения, не заполняемое администратором.
        consent_withdrawn_at: Момент отзыва согласий.
        disclosure_name: Разрешён ли показ имени и фамилии.
        disclosure_avatar: Разрешён ли показ фотографии.
        disclosure_conditions: Дополнительные запреты и условия субъекта.
        disclosure_reviewed: Проверил ли оператор возможность соблюдения условий.
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
    consent_version = models.CharField(
        "Комплект принятых документов", max_length=64, blank=True
    )
    consent_id = models.UUIDField("Идентификатор подтверждения", null=True, blank=True)
    consented_at = models.DateTimeField("Согласия получены", null=True, blank=True)
    consent_withdrawn_at = models.DateTimeField(
        "Согласия отозваны", null=True, blank=True
    )
    disclosure_name = models.BooleanField(
        "Разрешён показ имени и фамилии", default=False
    )
    disclosure_avatar = models.BooleanField("Разрешён показ фотографии", default=False)
    disclosure_conditions = models.TextField(
        "Особые условия распространения", max_length=1000, blank=True
    )
    disclosure_reviewed = models.BooleanField(
        "Возможность соблюдения особых условий проверена оператором",
        default=False,
        help_text="Не заменяет согласие игрока. Отметьте только после проверки возможности выполнить все его условия.",
    )

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


class ChatManagementEvent(models.Model):
    """Неизменяемая история создания и назначения управляющих.

    Attributes:
        room: Городское сообщество.
        actor: Инициатор действия; сохраняется событие и после удаления аккаунта.
        target: Пользователь, чьи права изменены.
        action: Вид совершённого действия.
        created_at: Время изменения.
    """

    room = models.ForeignKey(
        ChatRoom, on_delete=models.CASCADE, related_name="management_events"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="chat_management_actions",
    )
    target = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="chat_role_changes",
    )
    action = models.CharField(
        "Действие",
        max_length=32,
        choices=[
            ("created", "Создание сообщества"),
            ("transferred", "Передача управления"),
            ("assigned", "Назначение платформой"),
            ("moderator_added", "Назначение модератора"),
            ("moderator_removed", "Снятие модератора"),
            ("released", "Освобождение управления"),
        ],
    )
    created_at = models.DateTimeField("Время", auto_now_add=True)

    class Meta:
        verbose_name = "событие управления чатом"
        verbose_name_plural = "история управления чатами"
        ordering = ("-pk",)


class ChatArchive(models.Model):
    """Независимый от жизненного цикла комнаты закрытый архив поколения чата.

    Attributes:
        room: Живая комната; после её удаления ссылка обнуляется.
        source_room_id: Неизменяемый номер исходной комнаты.
        city_label: Название населённого пункта на момент создания архива.
        room_created_at: Время появления исходной комнаты.
        room_removed_at: Время удаления комнаты, не архива.
    """

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    room = models.OneToOneField(
        ChatRoom,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="private_archive",
    )
    source_room_id = models.PositiveBigIntegerField("Номер исходного чата", unique=True)
    city_label = models.CharField("Город", max_length=512)
    room_created_at = models.DateTimeField("Чат создан")
    room_removed_at = models.DateTimeField("Чат удалён", null=True, blank=True)
    created_at = models.DateTimeField("Архив создан", auto_now_add=True)

    class Meta:
        verbose_name = "архив городского чата"
        verbose_name_plural = "архивы городских чатов"
        ordering = ("-room_created_at", "-pk")

    def __str__(self) -> str:
        """Возвращает город и номер поколения, различая повторно созданные чаты."""
        return f"{self.city_label} · чат #{self.source_room_id}"


class ChatArchivedMessage(models.Model):
    """Снимок сообщения без каскадной зависимости от живой переписки и аккаунта.

    Attributes:
        archive: Архив исходного поколения комнаты.
        source_message_id: Номер исходного сообщения.
        author_reference: Номер автора, сохраняемый после удаления аккаунта.
        author_label: Имя автора для закрытого служебного просмотра, без контактов.
        text: Последний известный текст, включая удалённые сообщения.
        history_incomplete: До появления архива часть истории могла быть утрачена.
    """

    archive = models.ForeignKey(
        ChatArchive, on_delete=models.CASCADE, related_name="messages"
    )
    source_message_id = models.PositiveBigIntegerField("Номер сообщения")
    author_reference = models.PositiveBigIntegerField("Номер автора", null=True)
    author_label = models.CharField("Автор", max_length=384)
    text = models.TextField("Последний известный текст", blank=True)
    sent_at = models.DateTimeField("Отправлено")
    edited_at = models.DateTimeField("Изменено", null=True)
    deleted_at = models.DateTimeField("Удалено из переписки", null=True)
    history_incomplete = models.BooleanField("Ранняя история неполная", default=False)

    class Meta:
        verbose_name = "архивное сообщение"
        verbose_name_plural = "архивные сообщения"
        ordering = ("-sent_at", "-pk")
        constraints = [
            models.UniqueConstraint(
                fields=["archive", "source_message_id"],
                name="chat_archive_message_unique",
            )
        ]
        indexes = [
            models.Index(
                fields=["archive", "-sent_at", "-id"], name="chat_archive_page_idx"
            )
        ]

    def __str__(self) -> str:
        """Возвращает техническое обозначение сообщения без раскрытия его текста."""
        return f"Сообщение #{self.source_message_id}"


class ChatMessageVersion(models.Model):
    """Сохранённая версия сообщения, не редактируемая из интерфейса.

    Attributes:
        message: Независимый архивный снимок сообщения.
        sequence: Порядок версии внутри сообщения.
        action: Создание, редактирование, удаление либо перенос старых данных.
        text: Текст данной версии.
        actor_reference: Номер инициатора операции; не ссылка на удаляемый аккаунт.
        actor_label: Служебное обозначение инициатора без email и телефона.
        occurred_at: Время операции или снимка перенесённых данных.
    """

    message = models.ForeignKey(
        ChatArchivedMessage, on_delete=models.CASCADE, related_name="versions"
    )
    sequence = models.PositiveIntegerField("Версия")
    action = models.CharField(
        "Действие",
        max_length=16,
        choices=[
            ("created", "Отправлено"),
            ("edited", "Изменено"),
            ("deleted", "Удалено из чата"),
            ("imported", "Перенесённый снимок"),
        ],
    )
    text = models.TextField("Текст версии", blank=True)
    actor_reference = models.PositiveBigIntegerField("Номер инициатора", null=True)
    actor_label = models.CharField("Инициатор", max_length=384, blank=True)
    occurred_at = models.DateTimeField("Время")

    class Meta:
        verbose_name = "версия сообщения"
        verbose_name_plural = "версии сообщений"
        ordering = ("sequence",)
        constraints = [
            models.UniqueConstraint(
                fields=["message", "sequence"], name="chat_message_version_unique"
            )
        ]


class ChatInvitationCampaign(models.Model):
    """Хранит подтверждённую отправку и курсор фонового отбора получателей.

    Attributes:
        id: Одноразовый идентификатор подписанного подтверждения.
        room: Чат назначения.
        sender: Инициатор с проверяемыми при доставке полномочиями.
        audience: Персональная, городская либо общеплатформенная аудитория.
        upper_user_id: Верхняя граница аккаунтов на момент предпросмотра.
        cursor_user_id: Последний проверенный аккаунт.
        created_count: Число созданных приглашений без дублей.
    """

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    room = models.ForeignKey(
        ChatRoom, on_delete=models.CASCADE, related_name="invitation_campaigns"
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL
    )
    audience = models.CharField(
        "Аудитория",
        max_length=16,
        choices=[
            ("person", "Один игрок"),
            ("city", "Игроки города"),
            ("platform", "Вся платформа"),
        ],
    )
    target_id = models.PositiveBigIntegerField(
        "Номер выбранного игрока", null=True, blank=True
    )
    upper_user_id = models.PositiveBigIntegerField()
    cursor_user_id = models.PositiveBigIntegerField(default=0)
    estimated_count = models.PositiveIntegerField("Получателей при подтверждении")
    created_count = models.PositiveIntegerField("Приглашений создано", default=0)
    status = models.CharField(
        "Состояние",
        max_length=16,
        choices=[
            ("queued", "В очереди"),
            ("done", "Получатели обработаны"),
            ("cancelled", "Отменено: нет доступа"),
        ],
        default="queued",
        db_index=True,
    )
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    finished_at = models.DateTimeField("Обработка завершена", null=True, blank=True)

    class Meta:
        verbose_name = "отправка приглашений в чат"
        verbose_name_plural = "отправки приглашений в чаты"
        ordering = ["-created_at", "-pk"]

    def __str__(self) -> str:
        """Возвращает номер комнаты и тип аудитории без контактов получателей."""
        return f"Чат #{self.room_id}: {self.get_audience_display()}"


class ChatInvitation(models.Model):
    """Связывает единственное приглашение игрока с уведомлением и состоянием письма.

    Attributes:
        recipient: Получатель, самостоятельно подтверждающий вступление.
        campaign: Исходная подтверждённая отправка.
        status: Решение игрока, не заменяющее согласия.
        email_status: Отдельное состояние доставки письма.
        email_reason: Технический код причины без текста SMTP и адреса.
    """

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    room = models.ForeignKey(
        ChatRoom, on_delete=models.CASCADE, related_name="invitations"
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="chat_invitations",
    )
    campaign = models.ForeignKey(
        ChatInvitationCampaign, on_delete=models.CASCADE, related_name="invitations"
    )
    notification = models.OneToOneField(
        "users.Notification",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="chat_invitation",
    )
    status = models.CharField(
        "Решение",
        max_length=16,
        default="pending",
        choices=[
            ("pending", "Ожидает решения"),
            ("accepted", "Игрок вступил"),
            ("declined", "Отклонено"),
        ],
    )
    email_status = models.CharField(
        "Email",
        max_length=16,
        default="pending",
        db_index=True,
        choices=[
            ("pending", "В очереди"),
            ("sending", "Передача почтовому сервису"),
            ("sent", "Принято почтовым сервисом"),
            ("failed", "Ошибка отправки"),
            ("skipped", "Не отправляется"),
        ],
    )
    email_reason = models.CharField("Причина", max_length=40, blank=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    email_attempted_at = models.DateTimeField("Попытка email", null=True, blank=True)
    email_sent_at = models.DateTimeField("Email передан", null=True, blank=True)
    answered_at = models.DateTimeField("Решение принято", null=True, blank=True)

    class Meta:
        verbose_name = "приглашение в чат"
        verbose_name_plural = "приглашения в чаты"
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["room", "recipient"], name="chat_invitation_room_user_uniq"
            )
        ]

    def __str__(self) -> str:
        """Возвращает технические номера без email и телефона."""
        return f"Чат #{self.room_id}, игрок #{self.recipient_id}"


class ChatInvitationEmailBlock(models.Model):
    """Запоминает отказ игрока от email-приглашений из любых городских чатов.

    Attributes:
        user: Игрок, отписавшийся от данного типа писем.
        created_at: Время явного отказа.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="chat_invitation_email_block",
    )
    created_at = models.DateTimeField("Отключено", auto_now_add=True)

    class Meta:
        verbose_name = "отказ от email-приглашений в чаты"
        verbose_name_plural = "отказы от email-приглашений в чаты"
