"""
Courts models.
"""

from django.db import models

from apps.users.models import City


class CourtSurface(models.TextChoices):
    """Court surface types."""

    HARD = "hard", "Хард"
    CLAY = "clay", "Грунт"
    GRASS = "grass", "Трава"
    INDOOR = "indoor", "Закрытый хард"


class Court(models.Model):
    """Tennis court / club model."""

    name = models.CharField("Название", max_length=200)
    slug = models.SlugField("URL", unique=True)
    city = models.CharField("Город", max_length=20, choices=City.choices, default=City.MOSCOW)
    address = models.CharField("Адрес", max_length=255)
    description = models.TextField("Описание", blank=True)

    surface = models.CharField(
        "Покрытие", max_length=20, choices=CourtSurface.choices, default=CourtSurface.HARD
    )
    courts_count = models.PositiveSmallIntegerField("Количество кортов", default=1)
    has_lighting = models.BooleanField("Освещение", default=True)
    is_indoor = models.BooleanField("Крытый", default=False)

    phone = models.CharField("Телефон", max_length=20, blank=True)
    whatsapp = models.CharField("WhatsApp", max_length=20, blank=True)
    website = models.URLField("Сайт", blank=True)

    image = models.ImageField("Фото", upload_to="courts/", blank=True)
    latitude = models.DecimalField("Широта", max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField("Долгота", max_digits=9, decimal_places=6, null=True, blank=True)

    price_per_hour = models.DecimalField("Цена/час", max_digits=8, decimal_places=2, null=True, blank=True)
    is_active = models.BooleanField("Активен", default=True)

    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    class Meta:
        verbose_name = "Корт"
        verbose_name_plural = "Корты"
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.get_city_display()})"
