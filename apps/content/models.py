"""
Content models: News, Gallery, Pages.
"""

from django.db import models
from django.utils.text import slugify


class News(models.Model):
    """News article model."""

    title = models.CharField("Заголовок", max_length=200)
    slug = models.SlugField("URL", unique=True, blank=True)
    excerpt = models.CharField("Краткое описание", max_length=300, blank=True)
    content = models.TextField("Содержание")
    image = models.ImageField("Изображение", upload_to="news/", blank=True)

    is_published = models.BooleanField("Опубликовано", default=True)
    is_featured = models.BooleanField("На главной", default=False)

    views_count = models.PositiveIntegerField("Просмотры", default=0)

    created_at = models.DateTimeField("Дата создания", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)
    published_at = models.DateTimeField("Дата публикации", null=True, blank=True)

    class Meta:
        verbose_name = "Новость"
        verbose_name_plural = "Новости"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title, allow_unicode=True)
        super().save(*args, **kwargs)


class Gallery(models.Model):
    """Photo gallery model."""

    title = models.CharField("Название", max_length=200)
    slug = models.SlugField("URL", unique=True, blank=True)
    description = models.TextField("Описание", blank=True)
    cover_image = models.ImageField("Обложка", upload_to="galleries/covers/", blank=True)

    tournament = models.ForeignKey(
        "tournaments.Tournament",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="galleries",
        verbose_name="Турнир",
    )

    is_published = models.BooleanField("Опубликовано", default=True)
    created_at = models.DateTimeField("Дата создания", auto_now_add=True)

    class Meta:
        verbose_name = "Галерея"
        verbose_name_plural = "Галереи"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title, allow_unicode=True)
        super().save(*args, **kwargs)

    @property
    def photos_count(self) -> int:
        return self.photos.count()


class Photo(models.Model):
    """Photo in a gallery."""

    gallery = models.ForeignKey(
        Gallery, on_delete=models.CASCADE, related_name="photos", verbose_name="Галерея"
    )
    image = models.ImageField("Фото", upload_to="galleries/photos/")
    caption = models.CharField("Подпись", max_length=200, blank=True)
    order = models.PositiveSmallIntegerField("Порядок", default=0)

    created_at = models.DateTimeField("Загружено", auto_now_add=True)

    class Meta:
        verbose_name = "Фотография"
        verbose_name_plural = "Фотографии"
        ordering = ["order", "-created_at"]

    def __str__(self) -> str:
        return f"Фото {self.id} в {self.gallery}"


class Page(models.Model):
    """Static page model (rules, about, etc.). Content supports Markdown."""

    title = models.CharField("Заголовок", max_length=200)
    slug = models.SlugField("URL", unique=True)
    content = models.TextField(
        "Содержание",
        help_text="Поддерживается Markdown (заголовки, списки, ссылки, жирный и т.п.).",
    )

    is_published = models.BooleanField("Опубликовано", default=True)
    show_in_footer = models.BooleanField("Показывать в футере", default=False)
    order = models.PositiveSmallIntegerField("Порядок", default=0)

    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Страница"
        verbose_name_plural = "Страницы"
        ordering = ["order"]

    def __str__(self) -> str:
        return self.title
