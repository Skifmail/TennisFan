"""
Sparring models.
"""

from django.db import models

from apps.users.models import Player, SkillLevel


class SparringMatchType(models.TextChoices):
    """Тип матча: одиночный или парный."""

    SINGLES = "singles", "Одиночная"
    DOUBLES = "doubles", "Парная"


class SparringPreferredGender(models.TextChoices):
    """Предпочтительный пол (для одиночных — соперник; для парных — категория: мужской/женский парный, микст)."""

    MALE = "male", "Мужчины"
    FEMALE = "female", "Женщины"
    OPEN = "open", "Смешанный"
    MIXED = "mixed", "Микст"  # для парных: мужчина + женщина в паре


class SparringRequest(models.Model):
    """Sparring partner request."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Активна"
        CLOSED = "closed", "Закрыта"

    player = models.ForeignKey(
        Player,
        on_delete=models.CASCADE,
        related_name="sparring_requests",
        verbose_name="Игрок",
    )
    city = models.CharField("Город", max_length=100)
    desired_category = models.CharField(
        "Желаемый уровень силы",
        max_length=20,
        choices=SkillLevel.choices,
        blank=True,
    )
    description = models.TextField(
        "Описание", help_text="Опишите себя, когда и где хотите играть"
    )
    preferred_days = models.CharField(
        "Предпочтительные дни", max_length=100, blank=True
    )
    preferred_time = models.CharField(
        "Предпочтительное время", max_length=100, blank=True
    )

    # Детальные предпочтения
    desired_partner_age_min = models.PositiveIntegerField(
        "Минимальный возраст партнера",
        null=True,
        blank=True,
        help_text="Желаемый минимальный возраст партнера для спарринга",
    )
    desired_partner_age_max = models.PositiveIntegerField(
        "Максимальный возраст партнера",
        null=True,
        blank=True,
        help_text="Желаемый максимальный возраст партнера для спарринга",
    )
    preferred_location = models.CharField(
        "Предпочтительное место",
        max_length=200,
        blank=True,
        help_text="Конкретное место или район для игры (например, название корта или района)",
    )

    is_friendly = models.BooleanField(
        "Дружеский матч",
        default=False,
        help_text="Если отмечено, результат матча не влияет на рейтинг и силу",
    )

    match_type = models.CharField(
        "Тип матча",
        max_length=20,
        choices=SparringMatchType.choices,
        default=SparringMatchType.SINGLES,
    )
    preferred_gender = models.CharField(
        "Предпочтительный пол",
        max_length=20,
        choices=SparringPreferredGender.choices,
        default=SparringPreferredGender.OPEN,
        help_text="Для одиночных: пол соперника. Для парных: мужской/женский парный, смешанный или микст.",
    )

    status = models.CharField(
        "Статус", max_length=20, choices=Status.choices, default=Status.ACTIVE
    )

    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Заявка на спарринг"
        verbose_name_plural = "Заявки на спарринг"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Спарринг: {self.player} в {self.city}"

    def has_responses(self) -> bool:
        """Return True if at least one user has responded to this request."""
        return bool(self.responses.exists())


class SparringResponse(models.Model):
    """User response to a sparring request (отклик)."""

    class ContactMethod(models.TextChoices):
        TELEGRAM = "telegram", "Telegram"
        WHATSAPP = "whatsapp", "WhatsApp"
        MAX = "max", "Max"

    class ResponseStatus(models.TextChoices):
        PENDING = "pending", "Ожидает рассмотрения"
        ACCEPTED = "accepted", "Принят"
        REJECTED = "rejected", "Отклонен"

    sparring_request = models.ForeignKey(
        SparringRequest,
        on_delete=models.CASCADE,
        related_name="responses",
        verbose_name="Заявка",
    )
    respondent = models.ForeignKey(
        Player,
        on_delete=models.CASCADE,
        related_name="sparring_responses",
        verbose_name="Кто откликнулся",
    )
    contact_method = models.CharField(
        "Способ связи",
        max_length=20,
        choices=ContactMethod.choices,
    )
    status = models.CharField(
        "Статус отклика",
        max_length=20,
        choices=ResponseStatus.choices,
        default=ResponseStatus.PENDING,
    )
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Отклик на спарринг"
        verbose_name_plural = "Отклики на спарринг"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["sparring_request", "respondent"],
                name="sparring_unique_response_per_user",
            )
        ]

    def __str__(self) -> str:
        return f"{self.respondent} → {self.sparring_request}"


# ---------------------------------------------------------------------------
# Парный спарринг 2×2: формирование команд через MatchRequest / Team / JoinRequest
# ---------------------------------------------------------------------------


class DoublesMatchRequestStatus(models.TextChoices):
    OPEN = "open", "Открыта"
    FORMING = "forming", "Формирование"
    READY = "ready", "Готова к подтверждению"
    CONFIRMED = "confirmed", "Подтверждена (матч создан)"
    CANCELLED = "cancelled", "Отменена"


class TeamSide(models.TextChoices):
    AUTHOR = "author", "Команда автора"
    OPPONENT = "opponent", "Команда соперников"


class DoublesJoinRequestStatus(models.TextChoices):
    PENDING = "pending", "Ожидает"
    ACCEPTED = "accepted", "Принят"
    REJECTED = "rejected", "Отклонён"
    CANCELLED = "cancelled", "Отменён заявителем"


class DoublesMatchRequest(models.Model):
    """
    Заявка на формирование парного матча 2×2.
    У автора всегда есть команда (author_team), команда соперников создаётся при первом принятом отклике.
    """

    status = models.CharField(
        max_length=20,
        choices=DoublesMatchRequestStatus.choices,
        default=DoublesMatchRequestStatus.OPEN,
        db_index=True,
        verbose_name="Статус",
    )
    created_by = models.ForeignKey(
        Player,
        on_delete=models.CASCADE,
        related_name="doubles_match_requests_created",
        verbose_name="Автор заявки",
    )
    city = models.CharField("Город", max_length=100, blank=True)
    preferred_gender = models.CharField(
        "Предпочтительный пол",
        max_length=20,
        blank=True,
    )
    is_friendly = models.BooleanField("Дружеский матч", default=False)
    description = models.TextField("Описание", blank=True)

    preferred_days = models.CharField(
        "Предпочтительные дни", max_length=100, blank=True
    )
    preferred_time = models.CharField(
        "Предпочтительное время", max_length=100, blank=True
    )
    desired_level = models.CharField(
        "Желаемый уровень партнёров",
        max_length=20,
        choices=SkillLevel.choices,
        blank=True,
    )
    desired_age_min = models.PositiveIntegerField(
        "Минимальный возраст партнёров",
        null=True,
        blank=True,
    )
    desired_age_max = models.PositiveIntegerField(
        "Максимальный возраст партнёров",
        null=True,
        blank=True,
    )
    preferred_location = models.CharField(
        "Предпочтительное место",
        max_length=200,
        blank=True,
    )

    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)
    confirmed_at = models.DateTimeField("Подтверждено", null=True, blank=True)

    match = models.ForeignKey(
        "tournaments.Match",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="doubles_match_request",
        verbose_name="Матч",
    )

    class Meta:
        db_table = "doubles_sparring_match_request"
        verbose_name = "Заявка на парный спарринг 2×2"
        verbose_name_plural = "Заявки на парный спарринг 2×2"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"Парный спарринг #{self.pk} ({self.get_status_display()})"


class DoublesTeam(models.Model):
    """Команда в рамках заявки на парный матч. Не более 2 участников."""

    match_request = models.ForeignKey(
        DoublesMatchRequest,
        on_delete=models.CASCADE,
        related_name="teams",
        verbose_name="Заявка",
    )
    side = models.CharField(
        max_length=20,
        choices=TeamSide.choices,
        verbose_name="Сторона",
    )

    class Meta:
        db_table = "doubles_sparring_team"
        verbose_name = "Команда (парный спарринг)"
        verbose_name_plural = "Команды (парный спарринг)"
        constraints = [
            models.UniqueConstraint(
                fields=["match_request", "side"],
                name="doubles_team_unique_side_per_request",
            ),
        ]
        indexes = [
            models.Index(fields=["match_request", "side"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_side_display()} (заявка #{self.match_request_id})"

    @property
    def is_full(self) -> bool:
        return bool(self.members.count() >= 2)


class DoublesTeamMember(models.Model):
    """Участник команды. В команде не более 2 человек."""

    team = models.ForeignKey(
        DoublesTeam,
        on_delete=models.CASCADE,
        related_name="members",
        verbose_name="Команда",
    )
    player = models.ForeignKey(
        Player,
        on_delete=models.CASCADE,
        related_name="doubles_team_memberships",
        verbose_name="Игрок",
    )
    is_captain = models.BooleanField("Капитан команды", default=False)

    class Meta:
        db_table = "doubles_sparring_team_member"
        verbose_name = "Участник команды (парный спарринг)"
        verbose_name_plural = "Участники команд (парный спарринг)"
        constraints = [
            models.UniqueConstraint(
                fields=["team", "player"],
                name="doubles_team_member_unique_player_per_team",
            ),
        ]
        indexes = [
            models.Index(fields=["team"]),
            models.Index(fields=["player"]),
        ]

    def __str__(self) -> str:
        return f"{self.player} в {self.team}"


class DoublesJoinRequest(models.Model):
    """
    Заявка на присоединение к формируемому матчу (одиночкой или парой).
    target_side — в какую команду хотят: author или opponent.
    """

    match_request = models.ForeignKey(
        DoublesMatchRequest,
        on_delete=models.CASCADE,
        related_name="join_requests",
        verbose_name="Заявка на матч",
    )
    target_side = models.CharField(
        max_length=20,
        choices=TeamSide.choices,
        verbose_name="Целевая команда",
    )
    status = models.CharField(
        max_length=20,
        choices=DoublesJoinRequestStatus.choices,
        default=DoublesJoinRequestStatus.PENDING,
        db_index=True,
        verbose_name="Статус",
    )
    created_by = models.ForeignKey(
        Player,
        on_delete=models.CASCADE,
        related_name="doubles_join_requests_created",
        verbose_name="Кто подал заявку",
    )
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)
    processed_at = models.DateTimeField("Обработано", null=True, blank=True)

    class Meta:
        db_table = "doubles_sparring_join_request"
        verbose_name = "Заявка на присоединение (парный спарринг)"
        verbose_name_plural = "Заявки на присоединение (парный спарринг)"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["match_request", "status"]),
        ]

    def __str__(self) -> str:
        return f"Отклик #{self.pk} → заявка #{self.match_request_id} ({self.get_status_display()})"


class DoublesJoinRequestMember(models.Model):
    """Игроки в заявке на присоединение: 1 (solo) или 2 (pair)."""

    join_request = models.ForeignKey(
        DoublesJoinRequest,
        on_delete=models.CASCADE,
        related_name="members",
        verbose_name="Заявка на присоединение",
    )
    player = models.ForeignKey(
        Player,
        on_delete=models.CASCADE,
        related_name="doubles_join_request_memberships",
        verbose_name="Игрок",
    )
    order = models.PositiveSmallIntegerField("Порядок", default=1)

    class Meta:
        db_table = "doubles_sparring_join_request_member"
        verbose_name = "Участник заявки на присоединение"
        verbose_name_plural = "Участники заявок на присоединение"
        constraints = [
            models.UniqueConstraint(
                fields=["join_request", "player"],
                name="doubles_join_req_member_unique_player",
            ),
            models.UniqueConstraint(
                fields=["join_request", "order"],
                name="doubles_join_req_member_unique_order",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.player} (порядок {self.order}) в отклике #{self.join_request_id}"
