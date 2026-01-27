"""
Training models for adult tennis training.
"""

from django.db import models

from apps.users.models import SkillLevel


class TrainingType(models.TextChoices):
    """Training types."""

    INDIVIDUAL = "individual", "Индивидуальная"
    GROUP = "group", "Групповая"
    MINI_GROUP = "mini_group", "Мини-группа (2-4 чел.)"


class Coach(models.Model):
    """Tennis coach model."""

    name = models.CharField("Имя", max_length=100)
    slug = models.SlugField("URL", unique=True)
    photo = models.ImageField("Фото", upload_to="coaches/", blank=True)
    bio = models.TextField("Биография", blank=True)
    experience_years = models.PositiveSmallIntegerField("Опыт (лет)", default=0)
    specialization = models.CharField("Специализация", max_length=200, blank=True)

    phone = models.CharField("Телефон", max_length=20, blank=True)
    telegram = models.CharField("Telegram", max_length=100, blank=True)
    whatsapp = models.CharField("WhatsApp", max_length=20, blank=True)

    city = models.CharField("Город", max_length=100)
    is_active = models.BooleanField("Активен", default=True)

    created_at = models.DateTimeField("Создан", auto_now_add=True)

    class Meta:
        verbose_name = "Тренер"
        verbose_name_plural = "Тренеры"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


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
    preferred_datetime = models.DateTimeField("Предпочтительное время", null=True, blank=True)
    message = models.TextField("Сообщение", blank=True)

    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Запись на тренировку"
        verbose_name_plural = "Записи на тренировки"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.player} на {self.training}"
