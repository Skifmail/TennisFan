"""
Comments models.
"""

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models


class Comment(models.Model):
    """Universal comment model for matches, players, news."""

    # Generic relation to any model
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey("content_type", "object_id")

    author = models.ForeignKey(
        "users.Player",
        on_delete=models.CASCADE,
        related_name="comments",
        verbose_name="Автор",
    )
    text = models.TextField("Текст комментария")

    # Rating scores for player reviews
    rating_agreement = models.PositiveSmallIntegerField(
        "Договороспособность", null=True, blank=True, help_text="1-5"
    )
    rating_judging = models.PositiveSmallIntegerField(
        "Судейство", null=True, blank=True, help_text="1-5"
    )

    is_approved = models.BooleanField("Одобрен", default=True)

    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    class Meta:
        verbose_name = "Комментарий"
        verbose_name_plural = "Комментарии"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
        ]

    def __str__(self) -> str:
        return f"Комментарий от {self.author} к {self.content_type.model}"
