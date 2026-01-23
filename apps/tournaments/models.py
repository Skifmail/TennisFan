"""
Tournament models: Tournaments, Matches, Ratings.
"""

from django.db import models

from apps.users.models import City, Player, PlayerCategory


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
    """Tournament duration type."""

    SINGLE_DAY = "single", "Однодневный"
    WEEKEND = "weekend", "Выходного дня"
    MULTI_DAY = "multi", "Многодневный"


class Tournament(models.Model):
    """Tournament model."""

    name = models.CharField("Название", max_length=200)
    slug = models.SlugField("URL", unique=True)
    description = models.TextField("Описание", blank=True)
    city = models.CharField("Город", max_length=20, choices=City.choices, default=City.MOSCOW)
    category = models.CharField(
        "Категория", max_length=20, choices=PlayerCategory.choices, default=PlayerCategory.BASE
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
    status = models.CharField(
        "Статус", max_length=20, choices=TournamentStatus.choices, default=TournamentStatus.UPCOMING
    )
    points_winner = models.IntegerField("Очки за победу", default=100)
    points_loser = models.IntegerField("Очки за проигрыш", default=-50)
    max_participants = models.PositiveIntegerField(
        "Максимальное количество участников", 
        null=True, 
        blank=True,
        help_text="Оставьте пустым для неограниченного количества участников"
    )

    start_date = models.DateField("Дата начала")
    end_date = models.DateField("Дата окончания", null=True, blank=True)
    registration_deadline = models.DateTimeField("Дедлайн регистрации", null=True, blank=True)

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
        return f"{self.name} ({self.get_city_display()})"

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

    player1 = models.ForeignKey(
        Player, on_delete=models.CASCADE, related_name="matches_as_player1", verbose_name="Игрок 1"
    )
    player2 = models.ForeignKey(
        Player, on_delete=models.CASCADE, related_name="matches_as_player2", verbose_name="Игрок 2"
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
        "Категория", max_length=20, choices=PlayerCategory.choices
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
