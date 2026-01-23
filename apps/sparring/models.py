"""
Sparring models.
"""

from django.db import models

from apps.users.models import City, Player, PlayerCategory


class SparringRequest(models.Model):
    """Sparring partner request."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Активна"
        CLOSED = "closed", "Закрыта"

    player = models.ForeignKey(
        Player, on_delete=models.CASCADE, related_name="sparring_requests", verbose_name="Игрок"
    )
    city = models.CharField("Город", max_length=20, choices=City.choices, default=City.MOSCOW)
    desired_category = models.CharField(
        "Желаемая категория партнёра",
        max_length=20,
        choices=PlayerCategory.choices,
        blank=True,
    )
    description = models.TextField("Описание", help_text="Опишите себя, когда и где хотите играть")
    preferred_days = models.CharField("Предпочтительные дни", max_length=100, blank=True)
    preferred_time = models.CharField("Предпочтительное время", max_length=100, blank=True)

    status = models.CharField(
        "Статус", max_length=20, choices=Status.choices, default=Status.ACTIVE
    )

    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Заявка на спарринг"
        verbose_name_plural = "Заявки на спарринг"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Спарринг: {self.player} в {self.get_city_display()}"
