"""
Tournament models: Tournaments, Matches, Ratings.
"""

from django.conf import settings
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
    GROUP_STAGE = "group_stage", "Групповой этап"
    PLAYOFFS = "playoffs", "Плей-офф"
    COMPLETED = "completed", "Завершён"
    CANCELLED = "cancelled", "Отменён"


class TournamentGender(models.TextChoices):
    """Tournament gender category."""

    MALE = "male", "Мужчины"
    FEMALE = "female", "Женщины"
    OPEN = "open", "Любой"
    MIXED = "mixed", "Микст"  # только для парных: М + Ж в команде


class TournamentDuration(models.TextChoices):
    """Tournament duration type (категория турнира)."""

    SINGLE_DAY = "single", "Однодневный"
    WEEKEND = "weekend", "Выходного дня"
    MULTI_DAY = "multi", "Многодневный"


class TournamentFormat(models.TextChoices):
    """Формат проведения турнира."""

    SINGLE_ELIMINATION = "single_elimination", "Олимпийский (до 1 поражения)"
    OLYMPIC_CONSOLATION = (
        "olympic_consolation",
        "Олимпийский (за все места)",
    )
    ROUND_ROBIN = "round_robin", "Круговой"
    WEEKEND_DAY = "weekend_day", "Однодневный турнир"


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
    court = models.ForeignKey(
        "courts.Court",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tournaments",
        verbose_name="Корт",
        help_text="Площадка проведения турнира (опционально).",
    )
    club = models.ForeignKey(
        "clubs.Club",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tournaments",
        verbose_name="Клуб",
        help_text="Клуб-организатор (пусто — общий турнир платформы).",
    )
    is_open_interclub = models.BooleanField(
        "Открытый межклубный",
        default=False,
        help_text="Другие клубы могут подавать заявки на участие (только тариф Про).",
    )

    # Subscription & Entry Fee fields
    entry_fee = models.DecimalField(
        "Вступительный взнос (руб)", max_digits=10, decimal_places=2, default=0
    )
    is_one_day = models.BooleanField(
        "Однодневный турнир",
        default=False,
        help_text="Если отмечено, взнос платный для всех (с учетом скидок)",
    )

    gender = models.CharField(
        "Категория по полу",
        max_length=10,
        choices=TournamentGender.choices,
        default=TournamentGender.MALE,
        help_text=(
            "Мужчины/Женщины — только указанный пол. "
            "Любой — любой пол (М против Ж, пары ММ против ЖЖ и т.д.). "
            "Микст — только для парных: в команде должны быть М + Ж."
        ),
    )
    duration = models.CharField(
        "Продолжительность",
        max_length=10,
        choices=TournamentDuration.choices,
        default=TournamentDuration.MULTI_DAY,
    )
    tournament_type = models.CharField(
        "Тип турнира",
        max_length=20,
        choices=TournamentType.choices,
        default=TournamentType.REGULAR,
    )
    format = models.CharField(
        "Формат",
        max_length=20,
        choices=TournamentFormat.choices,
        default=TournamentFormat.SINGLE_ELIMINATION,
        help_text="Одноэтапная сетка: турнир на выбывание с подвалом, посев по рейтингу, очки при вылете. \nКруговой: все играют со всеми, итоговая таблица по очкам.",
    )
    variant = models.CharField(
        "Вариант",
        max_length=20,
        choices=TournamentVariant.choices,
        default=TournamentVariant.SINGLES,
        help_text="Одиночный: 1 на 1. Парный: команды по 2 человека.",
    )
    status = models.CharField(
        "Статус",
        max_length=20,
        choices=TournamentStatus.choices,
        default=TournamentStatus.UPCOMING,
    )
    # DEPRECATED: points_winner / points_loser не используются в логике.
    # Поля оставлены в БД только для совместимости со старыми данными и миграциями.
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
        help_text="Для одиночных: обязателен для одноэтапной сетки и круговых. Оставьте пустым для неограниченного.",
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
    registration_deadline = models.DateTimeField(
        "Дедлайн регистрации", null=True, blank=True
    )

    # Одноэтапная сетка / Олимпийская: очки за раунд (начисляются при вылете / в конце турнира)
    fan_points_r1 = models.PositiveSmallIntegerField("Очки за 1 круг", default=10)
    fan_points_r2 = models.PositiveSmallIntegerField("Очки за 2 круг", default=25)
    fan_points_sf = models.PositiveSmallIntegerField("Очки за полуфинал", default=45)
    fan_points_final = models.PositiveSmallIntegerField("Очки финалисту", default=70)
    fan_points_winner = models.PositiveSmallIntegerField("Очки победителю", default=100)

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
        verbose_name_plural = "Многодневные Турниры"
        ordering = ["-start_date"]

    def __str__(self) -> str:
        return f"{self.name} ({self.city})"

    def is_singles(self) -> bool:
        """Check if tournament is singles (1v1)."""
        return getattr(self, "variant", "singles") == TournamentVariant.SINGLES

    def is_doubles(self) -> bool:
        """Check if tournament is doubles (2v2)."""
        return getattr(self, "variant", "singles") == TournamentVariant.DOUBLES

    def is_mixed_doubles(self) -> bool:
        """Check if tournament is mixed doubles (парный микст: мужчина + женщина в команде)."""
        return self.is_doubles() and self.gender == TournamentGender.MIXED

    def is_full(self) -> bool:
        """Check if tournament has reached max participants/teams."""
        if self.is_doubles():
            if self.max_teams is None:
                return False
            return bool(
                self.teams.filter(player2__isnull=False).count() >= self.max_teams
            )
        if self.max_participants is None:
            return False
        return bool(self.participants.count() >= self.max_participants)

    def full_teams_count(self) -> int:
        """Количество полных команд (с партнёром) в парном турнире."""
        if not self.is_doubles():
            return 0
        return int(self.teams.filter(player2__isnull=False).count())

    def available_slots(self) -> int:
        """Get number of available slots (participants or teams)."""
        if self.is_doubles():
            if self.max_teams is None:
                return 0
            full_teams = self.teams.filter(player2__isnull=False).count()
            return int(max(0, self.max_teams - full_teams))
        if self.max_participants is None:
            return 0
        return int(max(0, self.max_participants - self.participants.count()))

    def save(self, *args, **kwargs) -> None:
        """Custom save to normalize duration and reset notification flag when дедлайн сдвинут."""
        from django.utils import timezone

        # ТВД (format=weekend_day) задаёт duration в админке; остальные — многодневные.
        if self.format != TournamentFormat.WEEKEND_DAY:
            self.duration = TournamentDuration.MULTI_DAY
        # Если дедлайн регистрации перенесли вперёд после отправки уведомления,
        # сбрасываем отметку, чтобы при следующей проверке уведомление могло уйти снова.
        if self.registration_deadline and self.insufficient_participants_notified_at:
            if self.registration_deadline > timezone.now():
                self.insufficient_participants_notified_at = None
        super().save(*args, **kwargs)


class TournamentPhoto(models.Model):
    """Дополнительное фото турнира для галереи (до 5 штук на турнир)."""

    tournament = models.ForeignKey(
        Tournament,
        on_delete=models.CASCADE,
        related_name="photos",
        verbose_name="Турнир",
    )
    image = models.ImageField(
        "Фото",
        upload_to="tournaments/gallery/",
        validators=[validate_image_max_2mb],
    )
    order = models.PositiveSmallIntegerField(
        "Порядок",
        default=0,
        help_text="Меньшее число — раньше в галерее.",
    )
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        verbose_name = "Фото турнира"
        verbose_name_plural = "Фото турниров"
        ordering = ["order", "id"]

    def __str__(self) -> str:
        return f"Фото турнира {self.tournament.name}"


class TournamentEntryPayment(models.Model):
    """Факт оплаты вступительного взноса пользователем за турнир (для проверки при добавлении админом)."""

    tournament = models.ForeignKey(
        Tournament,
        on_delete=models.CASCADE,
        related_name="entry_payments",
        verbose_name="Турнир",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tournament_entry_payments",
        verbose_name="Пользователь",
    )
    paid_at = models.DateTimeField("Дата оплаты", auto_now_add=True)

    class Meta:
        verbose_name = "Оплата взноса за турнир"
        verbose_name_plural = "Оплаты взносов за турниры"
        unique_together = [("tournament", "user")]

    def __str__(self) -> str:
        return f"{self.tournament.name} — {self.user}"


class TournamentEntryRefundRequest(models.Model):
    """
    Заявка на возврат взноса: участник удалён админом, взнос был оплачен.
    Идентификатор refund_ref подставляется в форму обратной связи для админа.
    """

    tournament = models.ForeignKey(
        Tournament,
        on_delete=models.CASCADE,
        related_name="entry_refund_requests",
        verbose_name="Турнир",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tournament_entry_refund_requests",
        verbose_name="Пользователь",
    )
    removed_at = models.DateTimeField("Дата удаления", auto_now_add=True)
    amount = models.DecimalField(
        "Сумма к возврату (руб)",
        max_digits=10,
        decimal_places=2,
        default=0,
    )
    refund_ref = models.CharField(
        "Идентификатор заявки (для админа)",
        max_length=32,
        unique=True,
        db_index=True,
    )

    class Meta:
        verbose_name = "Заявка на возврат взноса"
        verbose_name_plural = "Заявки на возврат взносов"
        ordering = ["-removed_at"]

    def __str__(self) -> str:
        return f"{self.refund_ref}: {self.tournament.name} — {self.user}"


class TVDTournament(Tournament):
    """Прокси-модель для раздела однодневных турниров в админке."""

    class Meta:
        proxy = True
        verbose_name = "Однодневный турнир"
        verbose_name_plural = "Однодневные турниры"


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


class TVDGroup(models.Model):
    """Группа в турнире выходного дня (ТВД)."""

    tournament = models.ForeignKey(
        Tournament,
        on_delete=models.CASCADE,
        related_name="tvd_groups",
        verbose_name="Турнир",
    )
    name = models.CharField("Название группы", max_length=10)  # A, B, C, ...
    order = models.PositiveSmallIntegerField("Порядок", default=0)
    is_completed = models.BooleanField("Группа завершена", default=False)

    class Meta:
        verbose_name = "Группа ТВД"
        verbose_name_plural = "Группы ТВД"
        unique_together = [("tournament", "name")]
        ordering = ["tournament", "order", "name"]

    def __str__(self) -> str:
        return f"{self.tournament.name} — Группа {self.name}"


class TVDGroupMember(models.Model):
    """
    Участник группы в ТВД-турнире.
    Одиночный: group + player (team=null). Парный: group + team (player=null, для совместимости можно player=team.player1).
    """

    group = models.ForeignKey(
        TVDGroup,
        on_delete=models.CASCADE,
        related_name="members",
        verbose_name="Группа",
    )
    player = models.ForeignKey(
        Player,
        on_delete=models.CASCADE,
        related_name="tvd_group_memberships",
        verbose_name="Игрок",
        null=True,
        blank=True,
        help_text="Для одиночного ТВД. Для парного — используйте team.",
    )
    team = models.ForeignKey(
        "TournamentTeam",
        on_delete=models.CASCADE,
        related_name="tvd_group_memberships",
        verbose_name="Команда",
        null=True,
        blank=True,
        help_text="Для парного ТВД. Для одиночного — используйте player.",
    )
    seed = models.PositiveSmallIntegerField(
        "Посев",
        null=True,
        blank=True,
        help_text="Номер посева (1 = сильнейший в группе).",
    )
    wins = models.PositiveIntegerField("Победы", default=0)
    losses = models.PositiveIntegerField("Поражения", default=0)
    games_won = models.PositiveIntegerField("Геймов выиграно", default=0)
    games_lost = models.PositiveIntegerField("Геймов проиграно", default=0)
    final_place = models.PositiveSmallIntegerField(
        "Место в группе",
        null=True,
        blank=True,
        help_text="1, 2, 3 (или 4 для групп по 4 человека).",
    )

    class Meta:
        verbose_name = "Участник группы ТВД"
        verbose_name_plural = "Участники групп ТВД"
        ordering = ["group", "final_place", "seed"]
        constraints = [
            models.UniqueConstraint(
                fields=["group", "player"],
                condition=models.Q(player__isnull=False),
                name="tvdgroupmember_unique_group_player",
            ),
            models.UniqueConstraint(
                fields=["group", "team"],
                condition=models.Q(team__isnull=False),
                name="tvdgroupmember_unique_group_team",
            ),
        ]

    def __str__(self) -> str:
        if self.team_id:
            return f"{self.team} в {self.group}"
        return f"{self.player} в {self.group}"

    def get_entity_display(self):
        """Игрок или команда для отображения."""
        if self.team_id:
            return str(self.team)
        return str(self.player) if self.player_id else "—"


class Match(models.Model):
    """Match between two players."""

    class MatchStatus(models.TextChoices):
        PENDING = "pending", "Ожидает участников"
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

    class MatchType(models.TextChoices):
        TOURNAMENT = "tournament", "Турнирный матч"
        SPARRING = "sparring", "Спарринг (личная встреча)"

    tournament = models.ForeignKey(
        Tournament,
        on_delete=models.CASCADE,
        related_name="matches",
        verbose_name="Турнир",
        null=True,
        blank=True,
        help_text="Для турнирных матчей. Для спаррингов оставить пустым.",
    )
    match_type = models.CharField(
        "Тип матча",
        max_length=20,
        choices=MatchType.choices,
        default=MatchType.TOURNAMENT,
        help_text="Турнирный матч или спарринг (личная встреча)",
    )
    sparring_response = models.ForeignKey(
        "sparring.SparringResponse",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="matches",
        verbose_name="Отклик на спарринг",
        help_text="Связь с откликом на спарринг, если матч создан из спарринга",
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
        help_text="Для сортировки и начисления сезонных очков.",
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
    tvd_group = models.ForeignKey(
        TVDGroup,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="matches",
        verbose_name="Группа ТВД",
        help_text="Для матчей группового этапа ТВД.",
    )
    tvd_stage = models.CharField(
        "Этап ТВД",
        max_length=30,
        blank=True,
        help_text="group, main_qf, main_sf, main_final, third_place, consolation_sf, consolation_final",
    )
    deadline = models.DateTimeField(
        "Дедлайн матча",
        null=True,
        blank=True,
        help_text="До этой даты матч должен быть сыгран (одноэтапная сетка).",
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
    partner1 = models.ForeignKey(
        Player,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="matches_as_partner1",
        verbose_name="Партнёр стороны 1",
        help_text="Для парного спарринга: второй игрок команды 1.",
    )
    partner2 = models.ForeignKey(
        Player,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="matches_as_partner2",
        verbose_name="Партнёр стороны 2",
        help_text="Для парного спарринга: второй игрок команды 2.",
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
        "Статус",
        max_length=20,
        choices=MatchStatus.choices,
        default=MatchStatus.SCHEDULED,
    )

    points_player1 = models.IntegerField("Очки П1", default=0)
    points_player2 = models.IntegerField("Очки П2", default=0)

    # FAN rating tracking
    class RatingCalcStatus(models.TextChoices):
        NOT_APPLICABLE = "na", "Не применимо"
        PENDING = "pending_calc", "Ожидает расчёта"
        CALCULATED = "calculated", "Рассчитано"

    rating_status = models.CharField(
        "Статус рейтинга",
        max_length=20,
        choices=RatingCalcStatus.choices,
        default=RatingCalcStatus.NOT_APPLICABLE,
        db_index=True,
        help_text="pending_calc — матч ждёт ежемесячного пересчёта; calculated — рейтинг обновлён.",
    )
    rating_delta_player1 = models.FloatField(
        "Изменение рейтинга П1",
        default=0.0,
        help_text="Дельта рейтинга для игрока 1 / команды 1 после расчёта.",
    )
    rating_delta_player2 = models.FloatField(
        "Изменение рейтинга П2",
        default=0.0,
        help_text="Дельта рейтинга для игрока 2 / команды 2 после расчёта.",
    )

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

    def is_sparring(self) -> bool:
        """Проверка, является ли матч спаррингом (личной встречей)."""
        return bool(self.match_type == self.MatchType.SPARRING)

    def is_friendly_sparring(self) -> bool:
        """Проверка, является ли матч дружеским спаррингом (результат не влияет на рейтинг)."""
        if not self.is_sparring():
            return False
        try:
            if self.sparring_response_id:
                request = self.sparring_response.sparring_request
                return bool(getattr(request, "is_friendly", False))
            rel = getattr(self, "doubles_match_request", None)
            if rel is not None:
                req = rel.first() if hasattr(rel, "first") else rel
                if req is not None:
                    return bool(getattr(req, "is_friendly", False))
        except Exception:
            pass
        return False

    def is_doubles_sparring(self) -> bool:
        """Проверка, создан ли матч из заявки на парный спарринг (2×2)."""
        if not self.is_sparring():
            return False
        if self.partner1_id and self.partner2_id:
            return True
        if not self.sparring_response_id:
            return False
        try:
            request = self.sparring_response.sparring_request
            return getattr(request, "match_type", None) == "doubles"
        except Exception:
            return False

    def is_tournament_match(self) -> bool:
        """Проверка, является ли матч турнирным."""
        return (
            self.match_type == self.MatchType.TOURNAMENT
            and self.tournament_id is not None
        )

    def get_player1_display(self) -> str:
        """Отображаемое имя стороны 1 (игрок или команда)."""
        if self.team1:
            return str(self.team1)
        if self.partner1 and self.player1:
            return f"{self.player1} / {self.partner1}"
        return str(self.player1) if self.player1 else "—"

    def get_player2_display(self) -> str:
        """Отображаемое имя стороны 2 (игрок или команда)."""
        if self.team2:
            return str(self.team2)
        if self.partner2 and self.player2:
            return f"{self.player2} / {self.partner2}"
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

    def is_walkover_loss(self) -> bool:
        """
        Проверка, является ли матч тех.поражением (Retired).
        Покрывает оба случая:
        - WALKOVER_LOSS (игрок признал, что не может играть)
        - WALKOVER_WIN (соперник заявил, что оппонент не вышел)
        В обоих случаях проигравший получает штраф.
        """
        # Проверяем через принятые proposals
        if self.result_proposals.filter(
            status=Match.ProposalStatus.ACCEPTED,
            result__in=[
                Match.ResultChoice.WALKOVER_LOSS,
                Match.ResultChoice.WALKOVER_WIN,
            ],
        ).exists():
            return True
        # Альтернативная проверка: статус WALKOVER и счёт 6:0 6:0 или 0:6 0:6
        if self.status == Match.MatchStatus.WALKOVER:
            if (
                self.player1_set1 == 6
                and self.player2_set1 == 0
                and self.player1_set2 == 6
                and self.player2_set2 == 0
            ):
                return True
            if (
                self.player1_set1 == 0
                and self.player2_set1 == 6
                and self.player1_set2 == 0
                and self.player2_set2 == 6
            ):
                return True
        return False


class MatchResultProposal(models.Model):
    """Pending match result that requires opponent confirmation."""

    match = models.ForeignKey(
        Match,
        on_delete=models.CASCADE,
        related_name="result_proposals",
        verbose_name="Матч",
    )
    proposer = models.ForeignKey(
        Player,
        on_delete=models.CASCADE,
        related_name="proposed_results",
        verbose_name="Инициатор",
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
        return (
            f"{self.match} — {self.get_result_display()} ({self.get_status_display()})"
        )


class DeadlineExtensionRequest(models.Model):
    """Запрос участника матча на продление дедлайна (кнопка в Telegram-боте)."""

    class Status(models.TextChoices):
        PENDING = "pending", "На рассмотрении"
        APPROVED = "approved", "Одобрено"
        REJECTED = "rejected", "Отклонено"

    match = models.ForeignKey(
        Match,
        on_delete=models.CASCADE,
        related_name="deadline_extension_requests",
        verbose_name="Матч",
    )
    requested_by = models.ForeignKey(
        Player,
        on_delete=models.CASCADE,
        related_name="deadline_extension_requests",
        verbose_name="Кто запросил",
    )
    status = models.CharField(
        "Статус",
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    processed_at = models.DateTimeField("Обработано", null=True, blank=True)

    class Meta:
        verbose_name = "Запрос продления дедлайна"
        verbose_name_plural = "Запросы продления дедлайна"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.match} — {self.requested_by} ({self.get_status_display()})"


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
    category = models.CharField("Категория", max_length=20, choices=SkillLevel.choices)
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
    """Результат игрока в турнире (одноэтапная сетка / олимпийская) — раунд вылета или место и начисленные очки."""

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
    fan_points = models.PositiveIntegerField("Начислено сезонных очков", default=0)
    is_consolation = models.BooleanField("Вылет в подвале", default=False)

    class Meta:
        verbose_name = "Результат в турнире"
        verbose_name_plural = "Результаты в турнирах"
        unique_together = ("tournament", "player")
        ordering = ["-fan_points"]

    def __str__(self) -> str:
        return f"{self.player} — {self.get_round_eliminated_display()} ({self.fan_points} очков)"


class SeasonPoints(models.Model):
    """Сезонные очки игрока за текущий сезон.

    Очки накапливаются в течение сезона (Зима: Октябрь-Апрель, Лето: Май-Сентябрь)
    и сбрасываются в ноль в конце сезона с архивацией результатов.
    """

    player = models.OneToOneField(
        Player,
        on_delete=models.CASCADE,
        related_name="season_points",
        verbose_name="Игрок",
    )
    current_season_points = models.PositiveIntegerField(
        "Очки текущего сезона",
        default=0,
        help_text="Сезонные очки, накопленные в текущем сезоне. Сбрасываются в конце сезона.",
    )
    season_name = models.CharField(
        "Название сезона",
        max_length=20,
        default="",
        help_text="Текущий сезон: 'Зима' или 'Лето'",
    )
    season_year = models.IntegerField(
        "Год сезона",
        default=0,
        help_text="Год текущего сезона",
    )
    updated_at = models.DateTimeField(
        "Обновлено",
        auto_now=True,
        help_text="Время последнего обновления сезонных очков",
    )

    class Meta:
        verbose_name = "Сезонные очки"
        verbose_name_plural = "Сезонные очки"
        ordering = ["-current_season_points"]

    def __str__(self) -> str:
        return f"{self.player}: {self.current_season_points} очков ({self.season_name} {self.season_year})"


class SeasonArchive(models.Model):
    """Архив результатов сезонов для Зала Славы.

    Сохраняет итоговые результаты игроков по завершении сезона.
    """

    player = models.ForeignKey(
        Player,
        on_delete=models.CASCADE,
        related_name="season_archives",
        verbose_name="Игрок",
    )
    season_name = models.CharField(
        "Название сезона",
        max_length=20,
        help_text="'Зима' или 'Лето'",
    )
    season_year = models.IntegerField(
        "Год сезона",
        help_text="Год сезона (для зимы - год начала, для лета - текущий год)",
    )
    final_points = models.PositiveIntegerField(
        "Итоговые очки",
        help_text="Количество сезонных очков, накопленных за сезон",
    )
    final_rank = models.PositiveIntegerField(
        "Итоговое место",
        help_text="Место игрока в рейтинге сезона",
        null=True,
        blank=True,
    )
    archived_at = models.DateTimeField(
        "Дата архивации",
        auto_now_add=True,
        help_text="Когда сезон был завершён и результаты заархивированы",
    )

    class Meta:
        verbose_name = "Архив сезона"
        verbose_name_plural = "Архивы сезонов"
        unique_together = ("player", "season_name", "season_year")
        ordering = ["-season_year", "-season_name", "-final_points"]
        indexes = [
            models.Index(fields=["season_name", "season_year", "-final_points"]),
        ]

    def __str__(self) -> str:
        return f"{self.player}: {self.final_points} очков, место {self.final_rank or '?'} ({self.season_name} {self.season_year})"
