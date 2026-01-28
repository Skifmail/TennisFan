"""
Navigation models.
"""

from django.db import models


class MenuItem(models.Model):
    """Пункт меню навигации. Админ может скрывать/показывать, менять порядок и названия."""

    title = models.CharField("Название", max_length=100)
    url = models.CharField(
        "URL",
        max_length=500,
        help_text="Внутренний путь (например /tournaments/) или внешняя ссылка (https://...).",
    )
    order = models.PositiveSmallIntegerField("Порядок сортировки", default=0)
    is_active = models.BooleanField("Активен (показывать в меню)", default=True)

    class Meta:
        verbose_name = "Пункт меню"
        verbose_name_plural = "Пункты меню"
        ordering = ["order", "id"]

    def __str__(self) -> str:
        return f"{self.title} ({self.url})"

    @property
    def is_external(self) -> bool:
        """Внешняя ссылка — открывать в новой вкладке."""
        u = (self.url or "").strip()
        return u.startswith("http://") or u.startswith("https://")
