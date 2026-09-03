"""
Courts models.
"""

import logging
from typing import cast

from django.conf import settings
from django.db import models
from django.utils.text import slugify

from apps.core.geo import GeoRegion
from config.validators import CompressImageFieldsMixin, validate_image_max_2mb

from .surfaces import CourtSurface as CourtSurface
from .surfaces import compose_surface_display, normalize_surface_codes

logger = logging.getLogger(__name__)


class Court(CompressImageFieldsMixin, models.Model):
    """Tennis court / club model."""

    name = models.CharField("Название", max_length=200)
    slug = models.SlugField(
        "URL",
        unique=True,
        allow_unicode=True,
        help_text="Латиница или кириллица. Если пусто в админке — заполнится из названия.",
    )
    city = models.CharField("Населённый пункт", max_length=100)
    address = models.CharField("Адрес", max_length=255)
    district = models.CharField("Район города", max_length=100, blank=True)
    region = models.CharField(
        "Регион",
        max_length=32,
        choices=GeoRegion.choices,
        blank=True,
        default="",
        db_index=True,
        help_text="Москва или область. Определяет набор доступных зон и городов.",
    )
    geo_area = models.ForeignKey(
        "core.GeoArea",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="courts",
        verbose_name="Зона / город",
        help_text="Зона Москвы или город области, из справочника. Поле «Район города» — свободный текст.",
    )
    description = models.TextField("Описание", blank=True)

    surface = models.CharField(
        "Покрытие",
        max_length=200,
        blank=True,
        default="",
        help_text="Собирается автоматически из выбранных покрытий.",
    )
    indoor_surfaces = models.JSONField(
        "Покрытие крытых кортов",
        default=list,
        blank=True,
        help_text="Хард, грунт, терафлекс или другое. Можно выбрать несколько.",
    )
    outdoor_surfaces = models.JSONField(
        "Покрытие открытых кортов",
        default=list,
        blank=True,
        help_text="Хард, грунт, терафлекс или другое. Можно выбрать несколько.",
    )
    courts_count = models.PositiveSmallIntegerField("Количество кортов", default=1)
    has_lighting = models.BooleanField("Освещение", default=True)
    is_indoor = models.BooleanField("Крытый", default=False)
    is_outdoor = models.BooleanField(
        "Открытый",
        default=False,
        help_text="У одного корта могут быть и крытые, и открытые поля.",
    )

    racket_rental = models.BooleanField("Прокат инвентаря", default=False)
    has_parking = models.BooleanField("Автомобильная парковка", default=False)
    racket_stringing = models.BooleanField("Перетяжка ракеток", default=False)
    has_training = models.BooleanField("Тренировки", default=False)

    phone = models.CharField("Телефон", max_length=50, blank=True)
    working_hours = models.CharField(
        "Время работы",
        max_length=255,
        blank=True,
        default="",
        help_text="Например: 7:00 до 23:00",
    )
    whatsapp = models.CharField("WhatsApp", max_length=20, blank=True)
    website = models.URLField("Сайт", blank=True)
    sells_balls = models.BooleanField("Теннисные мячи в продаже", default=False)
    sells_water = models.BooleanField("Вода в продаже", default=False)
    multiple_payment_methods = models.BooleanField(
        "Возможность оплаты разными способами", default=False
    )

    image = models.ImageField(
        "Фото", upload_to="courts/", blank=True, validators=[validate_image_max_2mb]
    )
    latitude = models.DecimalField(
        "Широта", max_digits=9, decimal_places=6, null=True, blank=True
    )
    longitude = models.DecimalField(
        "Долгота", max_digits=9, decimal_places=6, null=True, blank=True
    )

    price_per_hour = models.DecimalField(
        "Цена/час", max_digits=8, decimal_places=2, null=True, blank=True
    )
    rental_price_min = models.DecimalField(
        "Цена аренды от (₽/час)",
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
    )
    rental_price_max = models.DecimalField(
        "Цена аренды до (₽/час)",
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
    )
    is_active = models.BooleanField("Активен", default=True)

    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    class Meta:
        verbose_name = "Корт"
        verbose_name_plural = "Корты"
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.city})"

    def save(self, *args, **kwargs) -> None:
        """Нормализовать выбранные покрытия и обновить строку для витрины."""
        self.sync_surface_display()
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = list(
                set(update_fields) | {"indoor_surfaces", "outdoor_surfaces", "surface"}
            )
        super().save(*args, **kwargs)

    def sync_surface_display(self) -> None:
        """Записать канонические коды и человекочитаемую строку покрытия."""
        self.indoor_surfaces = normalize_surface_codes(self.indoor_surfaces)
        self.outdoor_surfaces = normalize_surface_codes(self.outdoor_surfaces)
        self.surface = compose_surface_display(
            is_indoor=self.is_indoor,
            indoor_surfaces=self.indoor_surfaces,
            is_outdoor=self.is_outdoor,
            outdoor_surfaces=self.outdoor_surfaces,
        )

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


class CourtPhoto(CompressImageFieldsMixin, models.Model):
    """Дополнительное фото корта для галереи (до 5 штук на корт)."""

    court = models.ForeignKey(
        Court,
        on_delete=models.CASCADE,
        related_name="photos",
        verbose_name="Корт",
    )
    image = models.ImageField(
        "Фото",
        upload_to="courts/gallery/",
        validators=[validate_image_max_2mb],
    )
    order = models.PositiveSmallIntegerField(
        "Порядок",
        default=0,
        help_text="Меньшее число — раньше в галерее.",
    )
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        verbose_name = "Фото корта"
        verbose_name_plural = "Фото кортов"
        ordering = ["order", "id"]

    def __str__(self) -> str:
        return f"Фото {self.court.name}"


class CourtApplicationStatus(models.TextChoices):
    """Статус заявки на добавление корта."""

    PENDING = "pending", "На рассмотрении"
    APPROVED = "approved", "Одобрена"
    REJECTED = "rejected", "Отклонена"


class CourtApplication(CompressImageFieldsMixin, models.Model):
    """Заявка владельца корта на добавление площадки на сайт. После одобрения создаётся Court."""

    class Meta:
        verbose_name = "Заявка на добавление корта"
        verbose_name_plural = "Заявки"
        ordering = ["-created_at"]

    status = models.CharField(
        "Статус",
        max_length=20,
        choices=CourtApplicationStatus.choices,
        default=CourtApplicationStatus.PENDING,
    )
    court = models.OneToOneField(
        Court,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="application",
        verbose_name="Созданный корт",
    )

    applicant_name = models.CharField("Контактное лицо", max_length=200)
    applicant_email = models.EmailField("Email заявителя")
    applicant_phone = models.CharField("Телефон заявителя", max_length=20, blank=True)

    name = models.CharField("Название", max_length=200)
    city = models.CharField("Населённый пункт", max_length=100)
    address = models.CharField("Адрес", max_length=255)
    description = models.TextField("Описание", blank=True)

    surface = models.CharField(
        "Покрытие",
        max_length=200,
        blank=True,
        default="",
        help_text="Собирается автоматически из выбранных покрытий.",
    )
    indoor_surfaces = models.JSONField(
        "Покрытие крытых кортов",
        default=list,
        blank=True,
        help_text="Хард, грунт, терафлекс или другое. Можно выбрать несколько.",
    )
    outdoor_surfaces = models.JSONField(
        "Покрытие открытых кортов",
        default=list,
        blank=True,
        help_text="Хард, грунт, терафлекс или другое. Можно выбрать несколько.",
    )
    courts_count = models.PositiveSmallIntegerField("Количество кортов", default=1)
    has_lighting = models.BooleanField("Освещение", default=True)
    is_indoor = models.BooleanField("Крытый", default=False)
    is_outdoor = models.BooleanField(
        "Открытый",
        default=False,
        help_text="Можно выбрать одновременно с «Крытый», если есть оба формата.",
    )

    phone = models.CharField("Телефон", max_length=20, blank=True)
    whatsapp = models.CharField("WhatsApp", max_length=20, blank=True)
    website = models.URLField("Сайт", blank=True)

    image = models.ImageField(
        "Фото",
        upload_to="courts/applications/",
        blank=True,
        validators=[validate_image_max_2mb],
    )
    latitude = models.DecimalField(
        "Широта", max_digits=9, decimal_places=6, null=True, blank=True
    )
    longitude = models.DecimalField(
        "Долгота", max_digits=9, decimal_places=6, null=True, blank=True
    )
    price_per_hour = models.DecimalField(
        "Цена/час", max_digits=8, decimal_places=2, null=True, blank=True
    )

    created_at = models.DateTimeField("Создана", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлена", auto_now=True)

    def __str__(self) -> str:
        return f"{self.name} ({self.city}) — {self.get_status_display()}"

    def save(self, *args, **kwargs) -> None:
        """Нормализовать покрытия заявки перед сохранением."""
        self.sync_surface_display()
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = list(
                set(update_fields) | {"indoor_surfaces", "outdoor_surfaces", "surface"}
            )
        super().save(*args, **kwargs)

    def sync_surface_display(self) -> None:
        """Записать канонические коды и человекочитаемую строку покрытия."""
        self.indoor_surfaces = normalize_surface_codes(self.indoor_surfaces)
        self.outdoor_surfaces = normalize_surface_codes(self.outdoor_surfaces)
        self.surface = compose_surface_display(
            is_indoor=self.is_indoor,
            indoor_surfaces=self.indoor_surfaces,
            is_outdoor=self.is_outdoor,
            outdoor_surfaces=self.outdoor_surfaces,
        )

    def approve_and_create_court(self) -> Court:
        """Создать Court из заявки, привязать к заявке, пометить одобренной."""
        if self.status != CourtApplicationStatus.PENDING:
            raise ValueError(
                "Можно одобрять только заявки со статусом «На рассмотрении»."
            )
        base_slug = slugify(self.name, allow_unicode=True) or "court"
        slug = base_slug
        n = 0
        while Court.objects.filter(slug=slug).exists():
            n += 1
            slug = f"{base_slug}-{n}"
        court = Court.objects.create(
            name=self.name,
            slug=slug,
            city=self.city,
            address=self.address,
            description=self.description,
            indoor_surfaces=self.indoor_surfaces,
            outdoor_surfaces=self.outdoor_surfaces,
            courts_count=self.courts_count,
            has_lighting=self.has_lighting,
            is_indoor=self.is_indoor,
            is_outdoor=self.is_outdoor,
            phone=self.phone,
            whatsapp=self.whatsapp,
            website=self.website or "",
            latitude=self.latitude,
            longitude=self.longitude,
            price_per_hour=self.price_per_hour,
            is_active=True,
        )
        if self.image:
            court.image = self.image
            court.save(update_fields=["image"])
        self.court = court
        self.status = CourtApplicationStatus.APPROVED
        self.save(update_fields=["court", "status", "updated_at"])
        try:
            from apps.core.email_service import send_court_application_decision_email

            send_court_application_decision_email(self, approved=True)
        except Exception:
            logger.exception(
                "send_court_application_decision_email failed | application=%s",
                self.pk,
            )
        return cast(Court, court)


class CourtRating(models.Model):
    """Оценка корта от зарегистрированного пользователя (1–5 звёзд). Один пользователь — одна оценка на корт."""

    court = models.ForeignKey(
        Court,
        on_delete=models.CASCADE,
        related_name="ratings",
        verbose_name="Корт",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="court_ratings",
        verbose_name="Пользователь",
    )
    score = models.PositiveSmallIntegerField(
        "Оценка",
        choices=[(i, str(i)) for i in range(1, 6)],
    )
    created_at = models.DateTimeField("Создана", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлена", auto_now=True)

    class Meta:
        verbose_name = "Оценка корта"
        verbose_name_plural = "Оценки кортов"
        unique_together = (("court", "user"),)
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return f"{self.court.name}: {self.score} от {self.user}"
