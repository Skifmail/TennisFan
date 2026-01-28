"""
Training models for adult tennis training.
"""

from django.db import models
from django.utils.text import slugify

from apps.users.models import SkillLevel


class TrainingType(models.TextChoices):
    """Training types."""

    INDIVIDUAL = "individual", "Индивидуальная"
    GROUP = "group", "Групповая"
    MINI_GROUP = "mini_group", "Мини-группа (2-4 чел.)"


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
    photo = models.ImageField("Фото", upload_to="coaches/", blank=True)
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
        return self.name

    @property
    def telegram_url(self) -> str | None:
        if not self.telegram:
            return None
        u = self.telegram.strip().lstrip("@")
        return f"https://t.me/{u}" if u else None

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

    @property
    def max_url(self) -> str | None:
        if not self.max_contact:
            return None
        s = self.max_contact.strip()
        if s.startswith(("http://", "https://")):
            return s
        return None


class CoachApplicationStatus(models.TextChoices):
    PENDING = "pending", "На рассмотрении"
    APPROVED = "approved", "Одобрена"
    REJECTED = "rejected", "Отклонена"


class CoachApplication(models.Model):
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
    photo = models.ImageField("Фото", upload_to="coaches/applications/", blank=True)
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
            raise ValueError("Можно одобрять только заявки со статусом «На рассмотрении».")
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
        return coach


class Training(models.Model):
    """Adult training program model."""

    title = models.CharField("Название", max_length=200)
    slug = models.SlugField("URL", unique=True)
    description = models.TextField("Описание")
    short_description = models.CharField("Краткое описание", max_length=300, blank=True)

    training_type = models.CharField(
        "Тип тренировки", max_length=20, choices=TrainingType.choices, default=TrainingType.INDIVIDUAL
    )
    skill_level = models.CharField(
        "Уровень", max_length=20, choices=SkillLevel.choices, default=SkillLevel.AMATEUR
    )
    target_category = models.CharField(
        "Целевой уровень (NTRP)", max_length=20, choices=SkillLevel.choices, blank=True
    )

    coach = models.ForeignKey(
        Coach, on_delete=models.SET_NULL, null=True, blank=True, related_name="trainings", verbose_name="Тренер"
    )
    court = models.ForeignKey(
        "courts.Court", on_delete=models.SET_NULL, null=True, blank=True, related_name="trainings", verbose_name="Корт"
    )
    city = models.CharField("Город", max_length=100)

    duration_minutes = models.PositiveSmallIntegerField("Длительность (мин)", default=60)
    max_participants = models.PositiveSmallIntegerField("Макс. участников", default=1)
    price = models.DecimalField("Цена", max_digits=10, decimal_places=2, null=True, blank=True)

    schedule = models.TextField("Расписание", blank=True, help_text="Дни и время проведения")
    image = models.ImageField("Изображение", upload_to="trainings/", blank=True)

    is_active = models.BooleanField("Активно", default=True)
    is_featured = models.BooleanField("На главной", default=False)

    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Тренировка"
        verbose_name_plural = "Тренировки"
        ordering = ["-is_featured", "-created_at"]

    def __str__(self) -> str:
        return self.title


class TrainingEnrollment(models.Model):
    """Training enrollment model."""

    class Status(models.TextChoices):
        PENDING = "pending", "Ожидает"
        CONTACTED = "contacted", "Связались"
        CONFIRMED = "confirmed", "Подтверждено"
        CANCELLED = "cancelled", "Отменено"
        COMPLETED = "completed", "Завершено"

    training = models.ForeignKey(
        Training, on_delete=models.CASCADE, related_name="enrollments", verbose_name="Тренировка"
    )
    player = models.ForeignKey(
        "users.Player", on_delete=models.CASCADE, related_name="training_enrollments", verbose_name="Игрок"
    )
    status = models.CharField(
        "Статус", max_length=20, choices=Status.choices, default=Status.PENDING
    )
    full_name = models.CharField("ФИО", max_length=200, blank=True)
    telegram = models.CharField("Telegram", max_length=100, blank=True)
    whatsapp = models.CharField("WhatsApp", max_length=20, blank=True)
    email = models.EmailField("Email", blank=True)
    preferred_datetime = models.DateTimeField("Предпочтительное время", null=True, blank=True)
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
        return f"https://t.me/{u}" if u else None

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
