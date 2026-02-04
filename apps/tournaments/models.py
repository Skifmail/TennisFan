"""
Tournament models: Tournaments, Matches, Ratings.
"""

from django.db import models

from apps.users.models import Player, SkillLevel
from config.validators import CompressImageFieldsMixin, validate_image_max_2mb


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
    """Формат проведения турнира."""

    SINGLE_ELIMINATION = "single_elimination", "FAN (одноэтапная сетка)"
    OLYMPIC_CONSOLATION = "olympic_consolation", "Олимпийская система (утешительная сетка)"
    ROUND_ROBIN = "round_robin", "Круговой"


class MatchFormat(models.TextChoices):
    """Формат матча для круговых турниров."""

    SET_6 = "1_set_6", "1 сет до 6 геймов"
    SET_TIEBREAK = "1_set_tiebreak", "1 сет с тай-брейком"
    BEST_OF_2 = "2_sets", "2 сета до победы"
    FAST4 = "fast4", "2 коротких сета + супертай-брейк"


class TournamentVariant(models.TextChoices):
    """Вариант турнира: одиночный или парный."""

    SINGLES = "singles", "Одиночный"
    DOUBLES = "doubles", "Парный"


class TournamentAllowedCategory(models.Model):
    """
    Допустимые категории участников турнира (Новичок, Любитель и т.д.).
    У турнира может быть от 1 до 5 категорий; регистрироваться могут только игроки с одной из них.
    """

    tournament = models.ForeignKey(
        "Tournament",
        on_delete=models.CASCADE,
        related_name="allowed_categories",
        verbose_name="Турнир",
    )
    category = models.CharField(
        "Категория",
        max_length=20,
        choices=SkillLevel.choices,
    )

    class Meta:
        verbose_name = "Допустимая категория турнира"
        verbose_name_plural = "Допустимые категории турнира"
        unique_together = [("tournament", "category")]
        ordering = ["tournament", "category"]

    def __str__(self) -> str:
        return f"{self.tournament.name}: {self.get_category_display()}"


class Tournament(CompressImageFieldsMixin, models.Model):
    """Tournament model."""

    name = models.CharField("Название", max_length=200)
    slug = models.SlugField("URL", unique=True)
    description = models.TextField("Описание", blank=True)
    city = models.CharField("Город", max_length=100)
    
    # Subscription & Entry Fee fields
    entry_fee = models.DecimalField("Вступительный взнос (руб)", max_digits=10, decimal_places=2, default=0)
    is_one_day = models.BooleanField("Однодневный турнир", default=False, help_text="Если отмечено, взнос платный для всех (с учетом скидок)")

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
        default=TournamentFormat.SINGLE_ELIMINATION,
        help_text="FAN: одноэтапная сетка, посев по рейтингу, очки при вылете. \nКруговой: все играют со всеми, итоговая таблица по очкам.",
    )
    variant = models.CharField(
        "Вариант",
        max_length=20,
        choices=TournamentVariant.choices,
        default=TournamentVariant.SINGLES,
        help_text="Одиночный: 1 на 1. Парный: команды по 2 человека.",
    )
    status = models.CharField(
        "Статус", max_length=20, choices=TournamentStatus.choices, default=TournamentStatus.UPCOMING
    )
    points_winner = models.IntegerField("Очки за победу", default=100)
    points_loser = models.IntegerField("Очки за проигрыш", default=-50)
    min_participants = models.PositiveIntegerField(
        "Минимальное количество участников",
        null=True,
        blank=True,
        help_text="Если к дедлайну регистрации меньше — админу уйдёт уведомление в Telegram; через 3 часа без продления турнир отменяется, лимиты регистраций возвращаются.",
    )
    max_participants = models.PositiveIntegerField(
        "Максимальное количество участников",
        null=True,
        blank=True,
        help_text="Для одиночных: обязателен для FAN и круговых. Оставьте пустым для неограниченного.",
    )
    min_teams = models.PositiveIntegerField(
        "Минимальное количество команд",
        null=True,
        blank=True,
        help_text="Для парных: если к дедлайну меньше — уведомление админу, через 3 ч без продления — отмена турнира.",
    )
    max_teams = models.PositiveIntegerField(
        "Максимальное количество команд",
        null=True,
        blank=True,
        help_text="Для парных: обязателен. Количество команд (пар) для регистрации.",
    )
    insufficient_participants_notified_at = models.DateTimeField(
        "Когда отправлено уведомление о недостатке участников",
        null=True,
        blank=True,
        help_text="Заполняется автоматически при первом срабатывании; сбрасывается при продлении дедлайна.",
    )
    bracket_generated = models.BooleanField(
        "Сетка сформирована",
        default=False,
        help_text="Сетка создана, участники зафиксированы, регистрация закрыта.",
    )
    match_days_per_round = models.PositiveSmallIntegerField(
        "Дней на раунд (дедлайн матча)",
        default=7,
        help_text="Сколько дней у игроков на проведение матча раунда/тура.",
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

    # Круговой: формат матча
    match_format = models.CharField(
        "Формат матча",
        max_length=20,
        choices=MatchFormat.choices,
        blank=True,
        help_text="Для круговых турниров: 1 сет до 6, с тай-брейком, 2 сета или Fast4.",
    )

    image = models.ImageField(
        "Изображение",
        upload_to="tournaments/",
        blank=True,
        validators=[validate_image_max_2mb],
    )
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

    def is_singles(self) -> bool:
        """Check if tournament is singles (1v1)."""
        return getattr(self, "variant", "singles") == TournamentVariant.SINGLES

    def is_doubles(self) -> bool:
        """Check if tournament is doubles (2v2)."""
        return getattr(self, "variant", "singles") == TournamentVariant.DOUBLES

    def is_full(self) -> bool:
        """Check if tournament has reached max participants/teams."""
        if self.is_doubles():
            if self.max_teams is None:
                return False
            return self.teams.filter(player2__isnull=False).count() >= self.max_teams
        if self.max_participants is None:
            return False
        return self.participants.count() >= self.max_participants

    def full_teams_count(self) -> int:
        """Количество полных команд (с партнёром) в парном турнире."""
        if not self.is_doubles():
            return 0
        return self.teams.filter(player2__isnull=False).count()

    def available_slots(self) -> int:
        """Get number of available slots (participants or teams)."""
        if self.is_doubles():
            if self.max_teams is None:
                return None
            full_teams = self.teams.filter(player2__isnull=False).count()
            return max(0, self.max_teams - full_teams)
        if self.max_participants is None:
            return None
        return max(0, self.max_participants - self.participants.count())

    def save(self, *args, **kwargs):
        from django.utils import timezone
        if self.registration_deadline and self.insufficient_participants_notified_at:
            if self.registration_deadline > timezone.now():
                self.insufficient_participants_notified_at = None
        super().save(*args, **kwargs)


class TournamentTeam(models.Model):
    """Команда (пара) в парном турнире. player2=null — ожидает партнёра."""

    tournament = models.ForeignKey(
        Tournament,
        on_delete=models.CASCADE,
        related_name="teams",
        verbose_name="Турнир",
    )
    player1 = models.ForeignKey(
        Player,
        on_delete=models.CASCADE,
        related_name="doubles_teams_as_player1",
        verbose_name="Игрок 1",
    )
    player2 = models.ForeignKey(
        Player,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="doubles_teams_as_player2",
        verbose_name="Игрок 2 (партнёр)",
    )
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        verbose_name = "Команда турнира"
        verbose_name_plural = "Команды турниров"
        unique_together = (("tournament", "player1"),)
        ordering = ["created_at"]

    def __str__(self) -> str:
        if self.player2:
            return f"{self.player1} / {self.player2}"
        return f"{self.player1} (ожидает партнёра)"

    def get_display_name(self) -> str:
        """Возвращает отображаемое имя команды."""
        if self.player2:
            return f"{self.player1.user.last_name} {self.player1.user.first_name} / {self.player2.user.last_name} {self.player2.user.first_name}"
        return f"{self.player1.user.last_name} {self.player1.user.first_name} (ожидает партнёра)"

    def is_complete(self) -> bool:
        """Команда полная (оба игрока указаны)."""
        return self.player2_id is not None


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
    loser_next_match = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="prev_matches_loser",
        verbose_name="Следующий матч (проигравший)",
        help_text="Для олимпийской системы: матч за следующее место (утешительная сетка).",
    )
    placement_min = models.PositiveSmallIntegerField(
        "Минимальное место (диапазон)",
        null=True,
        blank=True,
        help_text="Олимпийская система: за какое место идёт борьба (напр. 5 для сетки 5–8).",
    )
    placement_max = models.PositiveSmallIntegerField(
        "Максимальное место (диапазон)",
        null=True,
        blank=True,
        help_text="Олимпийская система: верхняя граница места (напр. 8).",
    )

    player1 = models.ForeignKey(
        Player,
        on_delete=models.CASCADE,
        related_name="matches_as_player1",
        verbose_name="Игрок 1",
        null=True,
        blank=True,
        help_text="Для одиночных: игрок 1. Для парных: первый игрок команды 1 (player1 из team1).",
    )
    player2 = models.ForeignKey(
        Player,
        on_delete=models.CASCADE,
        related_name="matches_as_player2",
        verbose_name="Игрок 2",
        null=True,
        blank=True,
        help_text="Для одиночных: игрок 2. Для парных: первый игрок команды 2 (player1 из team2).",
    )
    team1 = models.ForeignKey(
        TournamentTeam,
        on_delete=models.CASCADE,
        related_name="matches_as_team1",
        verbose_name="Команда 1",
        null=True,
        blank=True,
    )
    team2 = models.ForeignKey(
        TournamentTeam,
        on_delete=models.CASCADE,
        related_name="matches_as_team2",
        verbose_name="Команда 2",
        null=True,
        blank=True,
    )
    winner = models.ForeignKey(
        Player,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="matches_won_rel",
        verbose_name="Победитель",
        help_text="Для парных: один из игроков победившей команды.",
    )
    winner_team = models.ForeignKey(
        TournamentTeam,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="matches_won",
        verbose_name="Победившая команда",
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
        if self.team1 and self.team2:
            return f"{self.team1} vs {self.team2}"
        if self.player1 and self.player2:
            return f"{self.player1} vs {self.player2}"
        return "Матч"

    def get_player1_display(self) -> str:
        """Отображаемое имя стороны 1 (игрок или команда)."""
        if self.team1:
            return str(self.team1)
        return str(self.player1) if self.player1 else "—"

    def get_player2_display(self) -> str:
        """Отображаемое имя стороны 2 (игрок или команда)."""
        if self.team2:
            return str(self.team2)
        return str(self.player2) if self.player2 else "—"

    def get_side1_player(self):
        """Игрок стороны 1 для ссылки на профиль (player1 команды или player1)."""
        return self.team1.player1 if self.team1 else self.player1

    def get_side2_player(self):
        """Игрок стороны 2 для ссылки на профиль."""
        return self.team2.player1 if self.team2 else self.player2

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
        blank=True,
    )
    place = models.PositiveSmallIntegerField(
        "Итоговое место",
        null=True,
        blank=True,
        help_text="Олимпийская система: занятое место (1, 2, 3, …).",
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
