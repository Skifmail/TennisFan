"""
Tournament models: Tournaments, Matches, Ratings.
"""

from django.db import models

from apps.users.models import Player, SkillLevel


class TournamentType(models.TextChoices):
    """Types of tournaments."""

    REGULAR = "regular", "Регулярный"
    PLAYOFF = "playoff", "Плей-офф"
    CHAMPIONS_LEAGUE = "champions", "Лига чемпионов"
    CHALLENGER = "challenger", "Challenger"


class TournamentStatus(models.TextChoices):
    """Tournament status."""

    UPCOMING = "upcoming", "Предстоящий"
    ACTIVE = "active", "Активный"
    COMPLETED = "completed", "Завершён"
    CANCELLED = "cancelled", "Отменён"


class TournamentGender(models.TextChoices):
    """Tournament gender category."""

    MALE = "male", "Мужчины"
    FEMALE = "female", "Женщины"
    MIXED = "mixed", "Смешанный"


class TournamentDuration(models.TextChoices):
    """Tournament duration type (категория турнира)."""

    SINGLE_DAY = "single", "Однодневный"
    WEEKEND = "weekend", "Выходного дня"
    MULTI_DAY = "multi", "Многодневный"


class TournamentFormat(models.TextChoices):
    """Формат проведения: FAN = single elimination с посевом по рейтингу."""

    SINGLE_ELIMINATION = "single_elimination", "FAN (одноэтапная сетка)"
    OTHER = "other", "Другой"


class Tournament(models.Model):
    """Tournament model."""

    name = models.CharField("Название", max_length=200)
    slug = models.SlugField("URL", unique=True)
    description = models.TextField("Описание", blank=True)
    city = models.CharField("Город", max_length=100)
    
    # Subscription & Entry Fee fields
    entry_fee = models.DecimalField("Вступительный взнос (руб)", max_digits=10, decimal_places=2, default=0)
    is_one_day = models.BooleanField("Однодневный турнир", default=False, help_text="Если отмечено, взнос платный для всех (с учетом скидок)")

    category = models.CharField(
        "Категория", max_length=20, choices=SkillLevel.choices, default=SkillLevel.AMATEUR
    )
    gender = models.CharField(
        "Категория по полу", max_length=10, choices=TournamentGender.choices, default=TournamentGender.MALE
    )
    duration = models.CharField(
        "Продолжительность", max_length=10, choices=TournamentDuration.choices, default=TournamentDuration.MULTI_DAY
    )
    tournament_type = models.CharField(
        "Тип турнира", max_length=20, choices=TournamentType.choices, default=TournamentType.REGULAR
    )
    format = models.CharField(
        "Формат",
        max_length=20,
        choices=TournamentFormat.choices,
        default=TournamentFormat.OTHER,
        help_text="FAN: одноэтапная сетка, посев по рейтингу, очки при вылете.",
    )
    status = models.CharField(
        "Статус", max_length=20, choices=TournamentStatus.choices, default=TournamentStatus.UPCOMING
    )
    points_winner = models.IntegerField("Очки за победу", default=100)
    points_loser = models.IntegerField("Очки за проигрыш", default=-50)
    max_participants = models.PositiveIntegerField(
        "Максимальное количество участников",
        null=True,
        blank=True,
        help_text="Обязательно для FAN. Оставьте пустым для неограниченного количества.",
    )
    bracket_generated = models.BooleanField(
        "Сетка сформирована",
        default=False,
        help_text="FAN: сетка создана по рейтингу, регистрация закрыта.",
    )
    match_days_per_round = models.PositiveSmallIntegerField(
        "Дней на раунд (дедлайн матча)",
        default=7,
        help_text="FAN: сколько дней у игроков на проведение матча раунда.",
    )

    start_date = models.DateField("Дата начала")
    end_date = models.DateField("Дата окончания", null=True, blank=True)
    registration_deadline = models.DateTimeField("Дедлайн регистрации", null=True, blank=True)

    # FAN: очки за раунд (начисляются при вылете / в конце турнира)
    fan_points_r1 = models.PositiveSmallIntegerField("FAN: очки за 1 круг", default=10)
    fan_points_r2 = models.PositiveSmallIntegerField("FAN: очки за 2 круг", default=25)
    fan_points_sf = models.PositiveSmallIntegerField("FAN: очки за полуфинал", default=45)
    fan_points_final = models.PositiveSmallIntegerField("FAN: очки финалисту", default=70)
    fan_points_winner = models.PositiveSmallIntegerField("FAN: очки победителю", default=100)

    image = models.ImageField("Изображение", upload_to="tournaments/", blank=True)
    participants = models.ManyToManyField(
        Player, related_name="tournaments", blank=True, verbose_name="Участники"
    )

    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    class Meta:
        verbose_name = "Турнир"
        verbose_name_plural = "Турниры"
        ordering = ["-start_date"]

    def __str__(self) -> str:
        return f"{self.name} ({self.city})"

    def is_full(self) -> bool:
        """Check if tournament has reached max participants."""
        if self.max_participants is None:
            return False
        return self.participants.count() >= self.max_participants

    def available_slots(self) -> int:
        """Get number of available slots."""
        if self.max_participants is None:
            return None
        return max(0, self.max_participants - self.participants.count())


class Match(models.Model):
    """Match between two players."""

    class MatchStatus(models.TextChoices):
        SCHEDULED = "scheduled", "Запланирован"
        IN_PROGRESS = "in_progress", "В процессе"
        COMPLETED = "completed", "Завершён"
        CANCELLED = "cancelled", "Отменён"
        WALKOVER = "walkover", "Без игры"

    class ResultChoice(models.TextChoices):
        WIN = "win", "Победа"
        LOSS = "loss", "Поражение"
        WALKOVER_WIN = "walkover_win", "Тех. победа"
        WALKOVER_LOSS = "walkover_loss", "Тех. поражение"

    class ProposalStatus(models.TextChoices):
        PENDING = "pending", "Ожидает подтверждения"
        ACCEPTED = "accepted", "Подтверждено"
        REJECTED = "rejected", "Отклонено"

    tournament = models.ForeignKey(
        Tournament, on_delete=models.CASCADE, related_name="matches", verbose_name="Турнир"
    )
    court = models.ForeignKey(
        "courts.Court",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="matches",
        verbose_name="Корт",
    )
    round_name = models.CharField("Раунд", max_length=50, blank=True)
    round_index = models.PositiveSmallIntegerField(
        "Индекс раунда (1=1 круг, 2=2 круг, …)",
        default=1,
        help_text="Для сортировки и FAN-очков.",
    )
    round_order = models.PositiveSmallIntegerField(
        "Порядок матча в раунде",
        default=1,
        help_text="Номер пары в раунде (1–8 для 16 участников в R1).",
    )
    is_consolation = models.BooleanField(
        "Подвал (матч вылетевших)",
        default=False,
    )
    deadline = models.DateTimeField(
        "Дедлайн матча",
        null=True,
        blank=True,
        help_text="До этой даты матч должен быть сыгран (FAN).",
    )
    next_match = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="prev_matches",
        verbose_name="Следующий матч (победитель)",
    )

    player1 = models.ForeignKey(
        Player,
        on_delete=models.CASCADE,
        related_name="matches_as_player1",
        verbose_name="Игрок 1",
    )
    player2 = models.ForeignKey(
        Player,
        on_delete=models.CASCADE,
        related_name="matches_as_player2",
        verbose_name="Игрок 2",
    )
    winner = models.ForeignKey(
        Player,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="matches_won_rel",
        verbose_name="Победитель",
    )

    # Score as sets
    player1_set1 = models.PositiveSmallIntegerField("П1 Сет 1", null=True, blank=True)
    player2_set1 = models.PositiveSmallIntegerField("П2 Сет 1", null=True, blank=True)
    player1_set2 = models.PositiveSmallIntegerField("П1 Сет 2", null=True, blank=True)
    player2_set2 = models.PositiveSmallIntegerField("П2 Сет 2", null=True, blank=True)
    player1_set3 = models.PositiveSmallIntegerField("П1 Сет 3", null=True, blank=True)
    player2_set3 = models.PositiveSmallIntegerField("П2 Сет 3", null=True, blank=True)

    scheduled_datetime = models.DateTimeField("Дата и время", null=True, blank=True)
    completed_datetime = models.DateTimeField("Завершён", null=True, blank=True)
    status = models.CharField(
        "Статус", max_length=20, choices=MatchStatus.choices, default=MatchStatus.SCHEDULED
    )

    points_player1 = models.IntegerField("Очки П1", default=0)
    points_player2 = models.IntegerField("Очки П2", default=0)

    created_at = models.DateTimeField("Создан", auto_now_add=True)

    class Meta:
        verbose_name = "Матч"
        verbose_name_plural = "Матчи"
        ordering = ["-scheduled_datetime"]

    def __str__(self) -> str:
        return f"{self.player1} vs {self.player2}"

    @property
    def score_display(self) -> str:
        """Return formatted score string."""
        sets = []
        for i in range(1, 4):
            s1 = getattr(self, f"player1_set{i}")
            s2 = getattr(self, f"player2_set{i}")
            if s1 is not None and s2 is not None:
                sets.append(f"{s1}:{s2}")
        return " ".join(sets) if sets else "—"


class MatchResultProposal(models.Model):
    """Pending match result that requires opponent confirmation."""

    match = models.ForeignKey(
        Match, on_delete=models.CASCADE, related_name="result_proposals", verbose_name="Матч"
    )
    proposer = models.ForeignKey(
        Player, on_delete=models.CASCADE, related_name="proposed_results", verbose_name="Инициатор"
    )
    result = models.CharField(
        "Результат",
        max_length=20,
        choices=Match.ResultChoice.choices,
        default=Match.ResultChoice.WIN,
    )
    # Proposed score
    player1_set1 = models.PositiveSmallIntegerField("П1 Сет 1", null=True, blank=True)
    player2_set1 = models.PositiveSmallIntegerField("П2 Сет 1", null=True, blank=True)
    player1_set2 = models.PositiveSmallIntegerField("П1 Сет 2", null=True, blank=True)
    player2_set2 = models.PositiveSmallIntegerField("П2 Сет 2", null=True, blank=True)
    player1_set3 = models.PositiveSmallIntegerField("П1 Сет 3", null=True, blank=True)
    player2_set3 = models.PositiveSmallIntegerField("П2 Сет 3", null=True, blank=True)

    status = models.CharField(
        "Статус",
        max_length=20,
        choices=Match.ProposalStatus.choices,
        default=Match.ProposalStatus.PENDING,
    )

    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        verbose_name = "Предложенный результат"
        verbose_name_plural = "Предложенные результаты"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.match} — {self.get_result_display()} ({self.get_status_display()})"


class HeadToHead(models.Model):
    """Head-to-head statistics between two players."""

    player1 = models.ForeignKey(
        Player, on_delete=models.CASCADE, related_name="h2h_as_player1"
    )
    player2 = models.ForeignKey(
        Player, on_delete=models.CASCADE, related_name="h2h_as_player2"
    )
    player1_wins = models.PositiveIntegerField("Победы П1", default=0)
    player2_wins = models.PositiveIntegerField("Победы П2", default=0)

    class Meta:
        verbose_name = "Личная встреча"
        verbose_name_plural = "Личные встречи"
        unique_together = ("player1", "player2")

    def __str__(self) -> str:
        return f"{self.player1} {self.player1_wins}:{self.player2_wins} {self.player2}"


class SeasonRating(models.Model):
    """Season rating for a player."""

    player = models.ForeignKey(
        Player, on_delete=models.CASCADE, related_name="season_ratings"
    )
    season = models.CharField("Сезон", max_length=20)  # e.g., "2026"
    category = models.CharField(
        "Категория", max_length=20, choices=SkillLevel.choices
    )
    points = models.IntegerField("Очки", default=0)
    rank = models.PositiveIntegerField("Место", default=0)

    class Meta:
        verbose_name = "Рейтинг сезона"
        verbose_name_plural = "Рейтинги сезонов"
        unique_together = ("player", "season", "category")
        ordering = ["-points"]

    def __str__(self) -> str:
        return f"{self.player} - {self.season} ({self.points} очков)"


class TournamentPlayerResult(models.Model):
    """FAN: результат игрока в турнире — раунд вылета и начисленные очки."""

    class RoundEliminated(models.TextChoices):
        R1 = "r1", "1 круг"
        R2 = "r2", "2 круг"
        SF = "sf", "Полуфинал"
        FINAL = "final", "Финал"
        WINNER = "winner", "Победитель"

    tournament = models.ForeignKey(
        Tournament, on_delete=models.CASCADE, related_name="fan_results"
    )
    player = models.ForeignKey(
        Player, on_delete=models.CASCADE, related_name="tournament_fan_results"
    )
    round_eliminated = models.CharField(
        "Раунд вылета",
        max_length=10,
        choices=RoundEliminated.choices,
    )
    fan_points = models.PositiveIntegerField("Начислено очков FAN", default=0)
    is_consolation = models.BooleanField("Вылет в подвале", default=False)

    class Meta:
        verbose_name = "Результат в турнире (FAN)"
        verbose_name_plural = "Результаты в турнирах (FAN)"
        unique_together = ("tournament", "player")
        ordering = ["-fan_points"]

    def __str__(self) -> str:
        return f"{self.player} — {self.get_round_eliminated_display()} ({self.fan_points} очков)"
