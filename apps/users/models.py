"""
Custom User model with phone and email authentication.
"""

from typing import cast

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils import timezone

from apps.core.contact_utils import (
    build_max_url,
    build_telegram_url,
    build_whatsapp_url,
    get_max_display_contact,
)
from config.validators import CompressImageFieldsMixin, validate_image_max_2mb


class UserManager(BaseUserManager):
    """Custom manager for User model."""

    def create_user(
        self, email: str, password: str | None = None, **extra_fields
    ) -> "User":
        if not email:
            raise ValueError("Email обязателен")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return cast("User", user)

    def create_superuser(
        self, email: str, password: str | None = None, **extra_fields
    ) -> "User":
        """Создание суперпользователя с проверкой обязательных флагов."""
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return cast("User", self.create_user(email, password, **extra_fields))


class User(AbstractUser):
    """Custom User model with email as username."""

    username = None
    email = models.EmailField("Email", unique=True)
    phone = models.CharField("Телефон", max_length=20, blank=True)
    email_verified = models.BooleanField("Email подтвержден", default=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    objects = UserManager()

    class Meta:
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"

    def __str__(self) -> str:
        return str(self.email)

    def get_display_name(self) -> str:
        """Отображаемое имя в формате «Имя Фамилия».

        Returns:
            str: Имя и фамилия или email, если ФИО не заполнены.
        """
        from apps.users.display import format_user_display_name

        return format_user_display_name(self)


class PlayerCategory(models.TextChoices):
    """Player skill categories based on level of strength."""

    FUTURES = "futures", "Фьючерс"
    BASE = "base", "База"
    TOUR = "tour", "Тур"
    HARD = "hard", "Хард"
    CHALLENGER = "challenger", "Челленджер"
    MASTERS = "masters", "Мастерс"


class City(models.TextChoices):
    """Available cities."""

    MOSCOW = "moscow", "Москва"
    SPB = "spb", "Санкт-Петербург"


class Gender(models.TextChoices):
    """Player gender."""

    MALE = "male", "Мужской"
    FEMALE = "female", "Женский"


class Forehand(models.TextChoices):
    """Player forehand preference."""

    RIGHT = "right", "Правша"
    LEFT = "left", "Левша"


class SkillLevel(models.TextChoices):
    """Player skill level."""

    NOVICE = "novice", "Новичок"
    AMATEUR = "amateur", "Любитель"
    EXPERIENCED = "experienced", "Опытный"
    ADVANCED = "advanced", "Мастерс"
    PROFESSIONAL = "professional", "Профессионал"


class Player(CompressImageFieldsMixin, models.Model):
    """Player profile extending User."""

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="player",
        verbose_name="Пользователь",
    )
    avatar = models.ImageField(
        "Аватар",
        upload_to="avatars/",
        blank=True,
        storage=None,  # Use default storage from settings
        validators=[validate_image_max_2mb],
    )
    city = models.CharField("Населённый пункт", max_length=100, blank=True, default="")
    ntrp_level = models.DecimalField(
        "Уровень силы", max_digits=3, decimal_places=1, default=1.5
    )
    skill_level = models.CharField(
        "Уровень силы",
        max_length=20,
        choices=SkillLevel.choices,
        default=SkillLevel.NOVICE,
        help_text="Определяется тестом уровня силы или админкой. Пользователь не редактирует вручную.",
    )
    birth_date = models.DateField("Дата рождения", null=True, blank=True)
    gender = models.CharField(
        "Пол", max_length=10, choices=Gender.choices, blank=True, default=""
    )
    forehand = models.CharField(
        "Ведущая рука", max_length=10, choices=Forehand.choices, blank=True, default=""
    )

    age = models.PositiveIntegerField("Возраст", null=True, blank=True)
    bio = models.TextField("О себе", blank=True)
    telegram = models.CharField("Telegram", max_length=100, blank=True)
    whatsapp = models.CharField("WhatsApp", max_length=20, blank=True)
    max_contact = models.CharField(
        "MAX",
        max_length=500,
        blank=True,
        help_text="Ссылка на профиль в мессенджере MAX или номер телефона, привязанный к MAX.",
    )

    # Statistics / Rating
    total_points = models.FloatField(
        "Рейтинг FAN",
        default=0.0,
        help_text="Рейтинг силы (FAN). Обновляется после каждого матча.",
    )
    hidden_rating = models.FloatField(
        "Скрытый рейтинг",
        default=0.0,
        help_text="Теневой рейтинг, пересчитывается хронологически после каждого матча.",
    )
    matches_played = models.PositiveIntegerField("Сыграно матчей", default=0)
    matches_won = models.PositiveIntegerField("Побед", default=0)

    is_verified = models.BooleanField("Подтверждён", default=False)
    is_legend = models.BooleanField("Легенда", default=False)
    is_hidden_on_home = models.BooleanField(
        "Не показывать на главной",
        default=False,
        help_text="Если включено, игрок не отображается в блоке рейтинга на главной странице.",
    )
    has_ever_paid_subscription = models.BooleanField(
        "Уже покупал подписку",
        default=False,
        help_text="Становится True после первой оплаты любой подписки (для акции «первая за 1 ₽»).",
    )
    is_bye = models.BooleanField(
        "Свободный круг (bye)",
        default=False,
        help_text="Служебный игрок для матчей «игрок — свободный круг» при нечётном числе участников.",
    )

    created_at = models.DateTimeField("Дата регистрации", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Игрок"
        verbose_name_plural = "Игроки"
        ordering = ["-total_points"]
        indexes = [
            models.Index(fields=["is_bye", "is_verified"]),
            models.Index(fields=["city", "skill_level"]),
            models.Index(fields=["-total_points"]),
        ]

    def get_display_name(self) -> str:
        """Отображаемое имя игрока в формате «Имя Фамилия».

        Returns:
            str: Имя для UI или «Свободный круг» для служебного игрока bye.
        """
        from apps.users.display import format_player_display_name

        return format_player_display_name(self)

    def __str__(self) -> str:
        return self.get_display_name()

    @property
    def active_subscription_tier(self):
        try:
            sub = self.user.subscription
            if sub.is_valid():
                return sub.tier
        except Exception:
            pass
        return None

    @property
    def paid_subscription_tier(self):
        """Возвращает тариф подписки для отображения «особого статуса» (значка) только если у тарифа включён has_badge.
        Если в админке у тарифа снята галочка «Особый значок», возвращается None — значок нигде не показывается.
        """
        tier = self.active_subscription_tier
        if tier is None or tier.is_free_tier:
            return None
        if not tier.has_badge:
            return None
        return tier

    @property
    def telegram_url(self) -> str | None:
        """Link to Telegram profile."""
        return build_telegram_url(self.telegram)

    @property
    def whatsapp_url(self) -> str | None:
        """Link to WhatsApp chat."""
        return build_whatsapp_url(self.whatsapp)

    @property
    def max_url(self) -> str | None:
        """Link to MAX profile (stored URL)."""
        return build_max_url(self.max_contact)

    @property
    def max_contact_display(self) -> str | None:
        """Нормализованное отображаемое значение контакта MAX."""
        return get_max_display_contact(self.max_contact)

    @staticmethod
    def _calculate_age(birth_date):
        if not birth_date:
            return None
        today = timezone.now().date()
        years = today.year - birth_date.year
        if (today.month, today.day) < (birth_date.month, birth_date.day):
            years -= 1
        return years

    @property
    def calculated_age(self):
        return self._calculate_age(self.birth_date)

    def save(self, *args, **kwargs):
        self.age = self._calculate_age(self.birth_date)
        super().save(*args, **kwargs)

    @property
    def win_rate(self) -> float:
        if self.matches_played == 0:
            return 0.0
        return float(round(self.matches_won / self.matches_played * 100, 1))

    def get_rating_changes(self) -> dict[str, dict]:
        """
        Получить информацию об изменениях рейтинга после последнего матча.
        Возвращает:
        {
            'ntrp': {'delta': float, 'direction': 'up'|'down'|'none'},
            'fan': {'delta': float, 'direction': 'up'|'down'|'none'}
        }
        """
        from django.db.models import Q

        from apps.tournaments.models import Match

        # Находим последний завершенный матч игрока
        last_match = (
            Match.objects.filter(
                Q(player1=self)
                | Q(player2=self)
                | Q(team1__player1=self)
                | Q(team1__player2=self)
                | Q(team2__player1=self)
                | Q(team2__player2=self)
            )
            .filter(
                status__in=[Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER]
            )
            .order_by("-completed_datetime", "-scheduled_datetime", "-pk")
            .first()
        )

        result = {
            "ntrp": {"delta": 0.0, "direction": "none"},
            "fan": {"delta": 0.0, "direction": "none"},
        }

        if not last_match:
            return result

        # Определяем, на какой стороне был игрок
        is_player1 = False
        if last_match.partner1_id and last_match.partner2_id:
            is_player1 = self.pk in (last_match.player1_id, last_match.partner1_id)
        elif last_match.player1_id == self.pk:
            is_player1 = True
        elif last_match.player2_id == self.pk:
            is_player1 = False
        elif last_match.team1_id and (
            last_match.team1.player1_id == self.pk
            or last_match.team1.player2_id == self.pk
        ):
            is_player1 = True
        elif last_match.team2_id and (
            last_match.team2.player1_id == self.pk
            or last_match.team2.player2_id == self.pk
        ):
            is_player1 = False

        # Получаем дельту FAN рейтинга
        fan_delta = (
            last_match.rating_delta_player1
            if is_player1
            else last_match.rating_delta_player2
        )
        if fan_delta:
            result["fan"]["delta"] = float(fan_delta)
            if fan_delta > 0:
                result["fan"]["direction"] = "up"
            elif fan_delta < 0:
                result["fan"]["direction"] = "down"

        # Вычисляем реальное изменение уровня силы на основе рейтинга до и после матча
        if fan_delta is not None:
            from apps.users.rating_utils import rating_to_ntrp_level

            # Текущий рейтинг (после матча)
            rating_after = float(self.total_points)
            # Рейтинг до матча
            rating_before = rating_after - float(fan_delta)

            # Вычисляем силу до и после матча
            ntrp_before = rating_to_ntrp_level(rating_before)
            ntrp_after = rating_to_ntrp_level(rating_after)

            # Вычисляем дельту уровня силы
            ntrp_delta = float(ntrp_after) - float(ntrp_before)
            result["ntrp"]["delta"] = round(ntrp_delta, 1)

            if ntrp_delta > 0:
                result["ntrp"]["direction"] = "up"
            elif ntrp_delta < 0:
                result["ntrp"]["direction"] = "down"
            else:
                result["ntrp"]["direction"] = "none"

        return result


class NtrpTestResult(models.Model):
    """Результат теста уровня силы (NTRP) для игрока."""

    class Source(models.TextChoices):
        """Источник прохождения теста."""

        REGISTRATION = "registration", "Регистрация"
        MANUAL_TEST = "manual_test", "Тест без изменения рейтинга"

    player = models.ForeignKey(
        Player,
        on_delete=models.CASCADE,
        related_name="ntrp_tests",
        verbose_name="Игрок",
    )
    created_at = models.DateTimeField("Дата прохождения", auto_now_add=True)
    source = models.CharField(
        "Источник",
        max_length=32,
        choices=Source.choices,
        default=Source.REGISTRATION,
    )
    total_score = models.PositiveIntegerField(
        "Суммарные баллы по тесту", null=True, blank=True
    )
    level = models.DecimalField(
        "Рассчитанный уровень силы (NTRP)",
        max_digits=3,
        decimal_places=1,
        null=True,
        blank=True,
    )
    starting_points = models.FloatField(
        "Стартовый рейтинг FAN",
        null=True,
        blank=True,
        help_text="Значение рейтинга FAN, установленное на основании этого теста (если применимо).",
    )
    applied_to_rating = models.BooleanField(
        "Применён к рейтингу игрока",
        default=False,
        help_text="Показывает, использовался ли этот тест для установки стартового рейтинга игрока.",
    )
    answers = models.JSONField(
        "Ответы по вопросам",
        default=list,
        blank=True,
        help_text=(
            "Структура: список словарей с вопросом, выбранным вариантом и баллами. "
            "Например: [{'index': 0, 'question': 'Опыт игры', 'option_index': 2, "
            "'option_label': '...', 'option_score': 30}, ...]."
        ),
    )

    class Meta:
        verbose_name = "Результат теста NTRP"
        verbose_name_plural = "Результаты теста NTRP"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"NTRP тест для {self.player} от {self.created_at:%Y-%m-%d %H:%M}"

    def get_answers_display(self) -> str:
        """Сформировать краткое текстовое представление ответов для админки.

        Returns:
            str: Человекочитаемая строка с перечислением вопросов и выбранных ответов.
        """
        if not self.answers:
            return "Ответы отсутствуют."

        parts: list[str] = []
        for item in self.answers:
            index = item.get("index")
            question = item.get("question") or ""
            option_label = item.get("option_label") or ""
            option_score = item.get("option_score")
            num = f"{int(index) + 1}" if isinstance(index, int) else "?"
            if option_score is not None:
                parts.append(
                    f"{num}) {question} — {option_label} (баллы: {option_score})"
                )
            else:
                parts.append(f"{num}) {question} — {option_label}")

        return " | ".join(parts)


class Notification(models.Model):
    """Simple notification for user actions."""

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="notifications"
    )
    message = models.CharField("Сообщение", max_length=255)
    url = models.CharField("Ссылка", max_length=255, blank=True)
    is_read = models.BooleanField("Прочитано", default=False)
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        verbose_name = "Уведомление"
        verbose_name_plural = "Уведомления"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return str(self.message)


class EmailVerificationToken(models.Model):
    """Токен подтверждения email пользователя."""

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="email_verification_tokens",
        verbose_name="Пользователь",
    )
    token = models.CharField("Токен", max_length=128, unique=True)
    expires_at = models.DateTimeField("Истекает")
    used_at = models.DateTimeField("Использован", null=True, blank=True)
    created_at = models.DateTimeField("Создан", auto_now_add=True)

    class Meta:
        verbose_name = "Токен подтверждения email"
        verbose_name_plural = "Токены подтверждения email"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.token[:8]}"
