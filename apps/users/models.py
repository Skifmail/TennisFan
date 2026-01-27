"""
Custom User model with phone and email authentication.
"""

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils import timezone


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
        return user

    def create_superuser(
        self, email: str, password: str | None = None, **extra_fields
    ) -> "User":
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self.create_user(email, password, **extra_fields)


class User(AbstractUser):
    """Custom User model with email as username."""

    username = None
    email = models.EmailField("Email", unique=True)
    phone = models.CharField("Телефон", max_length=20, blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"

    def __str__(self) -> str:
        return self.email


class PlayerCategory(models.TextChoices):
    """Player skill categories based on NTRP."""

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
    ADVANCED = "advanced", "Продвинутый"
    PROFESSIONAL = "professional", "Профессионал"


class Player(models.Model):
    """Player profile extending User."""

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="player", verbose_name="Пользователь"
    )
    avatar = models.ImageField(
        "Аватар",
        upload_to="avatars/",
        blank=True,
        storage=None,  # Use default storage from settings
    )
    city = models.CharField("Город", max_length=100)
    category = models.CharField(
        "Категория", max_length=20, choices=SkillLevel.choices, default=SkillLevel.AMATEUR
    )
    ntrp_level = models.DecimalField(
        "NTRP уровень", max_digits=3, decimal_places=2, default=3.0
    )
    
    # Обязательные поля при регистрации
    skill_level = models.CharField(
        "Уровень мастерства", max_length=20, choices=SkillLevel.choices
    )
    birth_date = models.DateField("Дата рождения")
    gender = models.CharField(
        "Пол", max_length=10, choices=Gender.choices
    )
    forehand = models.CharField(
        "Forehand", max_length=10, choices=Forehand.choices
    )
    
    age = models.PositiveIntegerField("Возраст", null=True, blank=True)
    bio = models.TextField("О себе", blank=True)
    telegram = models.CharField("Telegram", max_length=100, blank=True)
    whatsapp = models.CharField("WhatsApp", max_length=20, blank=True)
    max_contact = models.CharField(
        "MAX",
        max_length=500,
        blank=True,
        help_text="Ссылка на профиль в мессенджере MAX (из раздела «Поделиться»)",
    )

    # Statistics (computed fields)
    total_points = models.IntegerField("Очки", default=0)
    matches_played = models.PositiveIntegerField("Сыграно матчей", default=0)
    matches_won = models.PositiveIntegerField("Побед", default=0)

    is_verified = models.BooleanField("Подтверждён", default=False)
    is_legend = models.BooleanField("Легенда", default=False)

    created_at = models.DateTimeField("Дата регистрации", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Игрок"
        verbose_name_plural = "Игроки"
        ordering = ["-total_points"]

    def __str__(self) -> str:
        return f"{self.user.first_name} {self.user.last_name}".strip() or self.user.email

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
        """Return subscription tier if player has an active paid subscription (Silver/Gold/Diamond), else None."""
        tier = self.active_subscription_tier
        if tier is None or tier.name == "free":
            return None
        return tier

    @property
    def telegram_url(self) -> str | None:
        """Link to Telegram profile."""
        if not self.telegram:
            return None
        u = self.telegram.strip().lstrip("@")
        return f"https://t.me/{u}" if u else None

    @property
    def whatsapp_url(self) -> str | None:
        """Link to WhatsApp chat."""
        if not self.whatsapp:
            return None
        phone = "".join(c for c in self.whatsapp if c.isdigit())
        if phone.startswith("8") and len(phone) == 11:
            phone = "7" + phone[1:]
        elif phone.startswith("7") and len(phone) == 11:
            pass
        elif len(phone) == 10:
            phone = "7" + phone
        else:
            return None
        return f"https://wa.me/{phone}"

    @property
    def max_url(self) -> str | None:
        """Link to MAX profile (stored URL)."""
        if not self.max_contact:
            return None
        s = self.max_contact.strip()
        if s.startswith(("http://", "https://")):
            return s
        return None

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
        return round(self.matches_won / self.matches_played * 100, 1)


class Notification(models.Model):
    """Simple notification for user actions."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    message = models.CharField("Сообщение", max_length=255)
    url = models.CharField("Ссылка", max_length=255, blank=True)
    is_read = models.BooleanField("Прочитано", default=False)
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        verbose_name = "Уведомление"
        verbose_name_plural = "Уведомления"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.message
