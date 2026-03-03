"""
Training models for adult tennis training.
"""

from typing import cast

from django.db import models
from django.utils.text import slugify

from apps.users.models import SkillLevel
from config.validators import CompressImageFieldsMixin, validate_image_max_2mb

# Соответствие уровней NTRP (строковые ключи совпадают с SkillLevel)
SKILL_LEVEL_NTRP: dict[str, str] = {
    "novice": "1.5–2.5",
    "amateur": "2.6–3.5",
    "experienced": "3.6–4.5",
    "advanced": "4.6–5.5",
    "professional": "5.6–7.0",
}


class TrainingType(models.TextChoices):
    """Типы тренировок."""

    INDIVIDUAL = "individual", "Индивидуальная"
    GROUP = "group", "Групповая"
    MINI_GROUP = "mini_group", "Мини-группа (2-4 чел.)"
    SPARRING = "sparring", "Спарринг тренировка"
    SPLIT = "split", "Сплит"


class Coach(models.Model):
    """Tennis coach model. Может быть связан с User (тренером становится зарегистрированный пользователь)."""

    user = models.OneToOneField(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="coach",
        verbose_name="Пользователь",
    )
    name = models.CharField("Имя", max_length=100)
    slug = models.SlugField("URL", unique=True)
    photo = models.ImageField(
        "Фото", upload_to="coaches/", blank=True, validators=[validate_image_max_2mb]
    )
    bio = models.TextField("Биография", blank=True)
    experience_years = models.PositiveSmallIntegerField("Опыт (лет)", default=0)
    specialization = models.CharField("Специализация", max_length=200, blank=True)

    phone = models.CharField("Телефон", max_length=20, blank=True)
    telegram = models.CharField("Telegram", max_length=100, blank=True)
    whatsapp = models.CharField("WhatsApp", max_length=20, blank=True)
    max_contact = models.CharField(
        "MAX",
        max_length=500,
        blank=True,
        help_text="Ссылка на профиль в мессенджере MAX",
    )

    city = models.CharField("Город", max_length=100)
    is_active = models.BooleanField("Активен", default=True)

    created_at = models.DateTimeField("Создан", auto_now_add=True)

    class Meta:
        verbose_name = "Тренер"
        verbose_name_plural = "Тренеры"
        ordering = ["name"]

    def __str__(self) -> str:
        return str(self.name)

    @property
    def player_profile(self):
        """Связанный профиль игрока (если есть)."""
        from apps.users.models import Player  # локальный импорт, чтобы избежать циклов

        if not self.user_id:
            return None
        try:
            return self.user.player
        except (AttributeError, Player.DoesNotExist):
            return None

    @property
    def photo_or_avatar(self):
        """Фото тренера: собственное фото или аватар игрока, если фото не загружено."""
        if self.photo:
            return self.photo
        player = self.player_profile
        if player and player.avatar:
            return player.avatar
        return None

    @property
    def telegram_url(self) -> str | None:
        handle = self.telegram or (
            self.player_profile.telegram if self.player_profile else ""
        )
        if not handle:
            return None
        u = handle.strip().lstrip("@")
        return str(f"https://t.me/{u}") if u else None

    @property
    def whatsapp_url(self) -> str | None:
        number = self.whatsapp or (
            self.player_profile.whatsapp if self.player_profile else ""
        )
        if not number:
            return None
        phone = "".join(c for c in number if c.isdigit())
        if phone.startswith("8") and len(phone) == 11:
            phone = "7" + phone[1:]
        elif phone.startswith("7") and len(phone) == 11:
            pass
        elif len(phone) == 10:
            phone = "7" + phone
        else:
            return None
        return str(f"https://wa.me/{phone}")

    @property
    def max_url(self) -> str | None:
        if not self.max_contact:
            return None
        s = self.max_contact.strip()
        if s.startswith(("http://", "https://")):
            return str(s)
        return None


class CoachApplicationStatus(models.TextChoices):
    PENDING = "pending", "На рассмотрении"
    APPROVED = "approved", "Одобрена"
    REJECTED = "rejected", "Отклонена"


class CoachApplication(CompressImageFieldsMixin, models.Model):
    """Заявка «Стать тренером». После одобрения создаётся Coach."""

    class Meta:
        verbose_name = "Заявка на тренера"
        verbose_name_plural = "Заявки на тренера"
        ordering = ["-created_at"]

    status = models.CharField(
        "Статус",
        max_length=20,
        choices=CoachApplicationStatus.choices,
        default=CoachApplicationStatus.PENDING,
    )
    coach = models.OneToOneField(
        Coach,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="application",
        verbose_name="Созданный тренер",
    )
    applicant_user = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="coach_applications",
        verbose_name="Заявитель (пользователь)",
    )

    applicant_name = models.CharField("Контактное лицо", max_length=200)
    applicant_email = models.EmailField("Email заявителя")
    applicant_phone = models.CharField("Телефон заявителя", max_length=20, blank=True)

    name = models.CharField("Имя", max_length=100)
    photo = models.ImageField(
        "Фото",
        upload_to="coaches/applications/",
        blank=True,
        validators=[validate_image_max_2mb],
    )
    bio = models.TextField("Биография", blank=True)
    experience_years = models.PositiveSmallIntegerField("Опыт (лет)", default=0)
    specialization = models.CharField("Специализация", max_length=200, blank=True)

    phone = models.CharField("Телефон", max_length=20, blank=True)
    telegram = models.CharField("Telegram", max_length=100, blank=True)
    whatsapp = models.CharField("WhatsApp", max_length=20, blank=True)
    max_contact = models.CharField("MAX", max_length=500, blank=True)

    city = models.CharField("Город", max_length=100)

    created_at = models.DateTimeField("Создана", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлена", auto_now=True)

    def __str__(self) -> str:
        return f"{self.name} ({self.city}) — {self.get_status_display()}"

    def approve_and_create_coach(self) -> Coach:
        if self.status != CoachApplicationStatus.PENDING:
            raise ValueError(
                "Можно одобрять только заявки со статусом «На рассмотрении»."
            )
        base_slug = slugify(self.name, allow_unicode=True) or "coach"
        slug = base_slug
        n = 0
        while Coach.objects.filter(slug=slug).exists():
            n += 1
            slug = f"{base_slug}-{n}"
        coach = Coach.objects.create(
            user=self.applicant_user,
            name=self.name,
            slug=slug,
            bio=self.bio,
            experience_years=self.experience_years,
            specialization=self.specialization or "",
            phone=self.phone or "",
            telegram=self.telegram or "",
            whatsapp=self.whatsapp or "",
            max_contact=self.max_contact or "",
            city=self.city,
            is_active=True,
        )
        if self.photo:
            coach.photo = self.photo
            coach.save(update_fields=["photo"])
        self.coach = coach
        self.status = CoachApplicationStatus.APPROVED
        self.save(update_fields=["coach", "status", "updated_at"])
        return cast(Coach, coach)


class Training(CompressImageFieldsMixin, models.Model):
    """Модель тренировки для взрослых."""

    title = models.CharField("Название", max_length=200)
    slug = models.SlugField("URL", unique=True)
    description = models.TextField("Описание")
    short_description = models.CharField("Краткое описание", max_length=300, blank=True)

    type_prices = models.JSONField(
        "Типы и цены",
        default=dict,
        blank=True,
        help_text="Словарь {тип_тренировки: цена}. Пример: {'individual': 3000, 'group': 1500}.",
    )
    skill_levels = models.JSONField(
        "Уровни",
        default=list,
        blank=True,
        help_text="Список выбранных уровней (novice, amateur, experienced, advanced, professional).",
    )
    target_levels = models.JSONField(
        "Целевые уровни силы",
        default=list,
        blank=True,
        help_text="Список целевых уровней силы.",
    )

    coach = models.ForeignKey(
        Coach,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="trainings",
        verbose_name="Тренер",
    )
    courts = models.ManyToManyField(
        "courts.Court",
        blank=True,
        related_name="trainings",
        verbose_name="Корты",
        help_text="Один тренер может проводить тренировки на разных площадках.",
    )
    city = models.CharField("Город", max_length=100)

    duration_minutes = models.PositiveSmallIntegerField(
        "Длительность (мин)", default=60
    )
    max_participants = models.PositiveSmallIntegerField("Макс. участников", default=1)

    price_min = models.DecimalField(
        "Цена от", max_digits=10, decimal_places=2, null=True, blank=True
    )
    price_max = models.DecimalField(
        "Цена до", max_digits=10, decimal_places=2, null=True, blank=True
    )

    court_price_min = models.DecimalField(
        "Цена за корт от", max_digits=10, decimal_places=2, null=True, blank=True
    )
    court_price_max = models.DecimalField(
        "Цена за корт до", max_digits=10, decimal_places=2, null=True, blank=True
    )

    schedule = models.TextField(
        "Расписание", blank=True, help_text="Дни и время проведения"
    )
    image = models.ImageField(
        "Изображение",
        upload_to="trainings/",
        blank=True,
        validators=[validate_image_max_2mb],
    )

    is_active = models.BooleanField("Активно", default=True)
    is_featured = models.BooleanField("На главной", default=False)

    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Тренировка"
        verbose_name_plural = "Тренировки"
        ordering = ["-is_featured", "-created_at"]

    def __str__(self) -> str:
        return str(self.title)

    @property
    def training_types(self) -> list[str]:
        """Список выбранных типов тренировки (ключи из type_prices)."""
        return list((self.type_prices or {}).keys())

    @property
    def training_types_display(self) -> list[str]:
        """Человекочитаемые названия выбранных типов тренировки."""
        mapping = dict(TrainingType.choices)
        return [mapping.get(t, t) for t in self.training_types]

    @property
    def type_prices_display(self) -> list[tuple[str, str]]:
        """Список (название типа, цена) для отображения."""
        mapping = dict(TrainingType.choices)
        result: list[tuple[str, str]] = []
        for t, price in (self.type_prices or {}).items():
            label = mapping.get(t, t)
            result.append((label, f"{price:,.0f} ₽" if price else "—"))
        return result

    @property
    def skill_levels_display(self) -> list[str]:
        """Человекочитаемые названия выбранных уровней с числовым диапазоном."""
        mapping = dict(SkillLevel.choices)
        result: list[str] = []
        for lvl in self.skill_levels or []:
            label = mapping.get(lvl, lvl)
            ntrp = SKILL_LEVEL_NTRP.get(lvl, "")
            result.append(f"{label} ({ntrp})" if ntrp else label)
        return result

    @property
    def target_levels_display(self) -> list[str]:
        """Человекочитаемые названия выбранных целевых уровней с числовым диапазоном."""
        mapping = dict(SkillLevel.choices)
        result: list[str] = []
        for lvl in self.target_levels or []:
            label = mapping.get(lvl, lvl)
            ntrp = SKILL_LEVEL_NTRP.get(lvl, "")
            result.append(f"{label} ({ntrp})" if ntrp else label)
        return result

    @property
    def price_display(self) -> str:
        """Строковое представление диапазона цены."""
        if self.price_min and self.price_max:
            if self.price_min == self.price_max:
                return f"{self.price_min:,.0f} ₽"
            return f"{self.price_min:,.0f} – {self.price_max:,.0f} ₽"
        if self.price_min:
            return f"от {self.price_min:,.0f} ₽"
        if self.price_max:
            return f"до {self.price_max:,.0f} ₽"
        return ""

    @property
    def court_price_display(self) -> str:
        """Строковое представление диапазона цены за корт."""
        if self.court_price_min and self.court_price_max:
            if self.court_price_min == self.court_price_max:
                return f"+ корт {self.court_price_min:,.0f} ₽"
            return f"+ корт {self.court_price_min:,.0f} – {self.court_price_max:,.0f} ₽"
        if self.court_price_min:
            return f"+ корт от {self.court_price_min:,.0f} ₽"
        if self.court_price_max:
            return f"+ корт до {self.court_price_max:,.0f} ₽"
        return ""


class TrainingEnrollment(models.Model):
    """Training enrollment model."""

    class Status(models.TextChoices):
        PENDING = "pending", "Ожидает"
        CONTACTED = "contacted", "Связались"
        CONFIRMED = "confirmed", "Подтверждено"
        CANCELLED = "cancelled", "Отменено"
        COMPLETED = "completed", "Завершено"

    training = models.ForeignKey(
        Training,
        on_delete=models.CASCADE,
        related_name="enrollments",
        verbose_name="Тренировка",
    )
    player = models.ForeignKey(
        "users.Player",
        on_delete=models.CASCADE,
        related_name="training_enrollments",
        verbose_name="Игрок",
    )
    status = models.CharField(
        "Статус", max_length=20, choices=Status.choices, default=Status.PENDING
    )
    full_name = models.CharField("ФИО", max_length=200, blank=True)
    telegram = models.CharField("Telegram", max_length=100, blank=True)
    whatsapp = models.CharField("WhatsApp", max_length=20, blank=True)
    email = models.EmailField("Email", blank=True)
    preferred_datetime = models.DateTimeField(
        "Предпочтительное время", null=True, blank=True
    )
    desired_court = models.ForeignKey(
        "courts.Court",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="enrollment_requests",
        verbose_name="Желаемый корт",
    )
    message = models.TextField("Сообщение", blank=True)

    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Запись на тренировку"
        verbose_name_plural = "Записи на тренировки"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.player} на {self.training}"

    @property
    def telegram_url(self) -> str | None:
        if not self.telegram:
            return None
        u = self.telegram.strip().lstrip("@")
        return str(f"https://t.me/{u}") if u else None

    @property
    def whatsapp_url(self) -> str | None:
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
