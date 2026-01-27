"""
Sparring models.
"""

from django.db import models

from apps.users.models import Player, SkillLevel


class SparringRequest(models.Model):
    """Sparring partner request."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Активна"
        CLOSED = "closed", "Закрыта"

    player = models.ForeignKey(
        Player, on_delete=models.CASCADE, related_name="sparring_requests", verbose_name="Игрок"
    )
    city = models.CharField("Город", max_length=100)
    desired_category = models.CharField(
        "Желаемый уровень (NTRP)",
        max_length=20,
        choices=SkillLevel.choices,
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
        return f"Спарринг: {self.player} в {self.city}"

    def has_responses(self) -> bool:
        """Return True if at least one user has responded to this request."""
        return self.responses.exists()


class SparringResponse(models.Model):
    """User response to a sparring request (отклик)."""

    class ContactMethod(models.TextChoices):
        TELEGRAM = "telegram", "Telegram"
        WHATSAPP = "whatsapp", "WhatsApp"
        MAX = "max", "Max"

    sparring_request = models.ForeignKey(
        SparringRequest,
        on_delete=models.CASCADE,
        related_name="responses",
        verbose_name="Заявка",
    )
    respondent = models.ForeignKey(
        Player,
        on_delete=models.CASCADE,
        related_name="sparring_responses",
        verbose_name="Кто откликнулся",
    )
    contact_method = models.CharField(
        "Способ связи",
        max_length=20,
        choices=ContactMethod.choices,
    )
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        verbose_name = "Отклик на спарринг"
        verbose_name_plural = "Отклики на спарринг"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["sparring_request", "respondent"],
                name="sparring_unique_response_per_user",
            )
        ]

    def __str__(self) -> str:
        return f"{self.respondent} → {self.sparring_request}"
