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


class AboutUs(models.Model):
    """
    Singleton model for "О нас" page.
    Заголовок "О НАС" фиксирован в шаблоне.
    """

    subtitle = models.CharField("Подзаголовок", max_length=300, blank=True)
    image = models.ImageField("Фото", upload_to="about/", blank=True)
    body = models.TextField(
        "Статья",
        blank=True,
        help_text="Поддерживается Markdown (заголовки, списки, ссылки, жирный и т.п.).",
    )
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "О нас"
        verbose_name_plural = "О нас"

    def __str__(self) -> str:
        return "О нас"

    @classmethod
    def get_singleton(cls) -> "AboutUs":
        """Return the single AboutUs instance, creating if needed."""
        obj = cls.objects.first()
        if obj is None:
            obj = cls.objects.create(subtitle="", body="")
        return obj


class ContactPage(models.Model):
    """
    Singleton для страницы «Контакты».
    Текстовое поле перед списком контактов — редактируется в админке.
    """

    intro_text = models.TextField(
        "Текст перед контактами",
        blank=True,
        help_text="Произвольный текст (приветствие, описание и т.д.). Поддерживается Markdown.",
    )
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Контакты"
        verbose_name_plural = "Контакты"

    def __str__(self) -> str:
        return "Контакты"

    @classmethod
    def get_singleton(cls) -> "ContactPage":
        """Return the single ContactPage instance, creating if needed."""
        obj = cls.objects.first()
        if obj is None:
            obj = cls.objects.create(intro_text="")
        return obj


class ContactItem(models.Model):
    """
    Элемент контакта на странице «Контакты».
    Админ добавляет способы связи: адрес, телефон, мессенджеры и т.д.
    """

    contact_page = models.ForeignKey(
        ContactPage,
        on_delete=models.CASCADE,
        related_name="contact_items",
        verbose_name="Страница контактов",
        null=True,
        blank=True,
    )
    class ItemType(models.TextChoices):
        ADDRESS = "address", "Адрес"
        PHONE = "phone", "Телефон"
        EMAIL = "email", "Email"
        TELEGRAM = "telegram", "Telegram"
        WHATSAPP = "whatsapp", "WhatsApp"
        MAX = "max", "MAX"
        VK = "vk", "VK"
        WEBSITE = "website", "Сайт"
        WORK_HOURS = "work_hours", "Режим работы"
        OTHER = "other", "Другое"

    item_type = models.CharField(
        "Тип",
        max_length=20,
        choices=ItemType.choices,
        default=ItemType.OTHER,
    )
    label = models.CharField(
        "Подпись (опционально)",
        max_length=100,
        blank=True,
        help_text="Например: «Поддержка», «Офис». Если пусто — используется тип.",
    )
    value = models.CharField(
        "Значение",
        max_length=500,
        help_text="Телефон, адрес, @username, email и т.д.",
    )
    url = models.URLField(
        "Ссылка (опционально)",
        blank=True,
        help_text="Для мессенджеров: t.me/xxx, wa.me/xxx. Для email: mailto:...",
    )
    order = models.PositiveSmallIntegerField("Порядок", default=0)

    class Meta:
        verbose_name = "Контакт"
        verbose_name_plural = "Контакты"
        ordering = ["order", "id"]

    def __str__(self) -> str:
        label = self.label or self.get_item_type_display()
        return f"{label}: {self.value[:50]}{'…' if len(self.value) > 50 else ''}"

    @property
    def display_label(self) -> str:
        """Подпись для отображения."""
        return self.label or self.get_item_type_display()

    @property
    def clickable_url(self) -> str | None:
        """URL для перехода или None."""
        if self.url:
            return self.url
        if self.item_type == self.ItemType.EMAIL and self.value:
            return f"mailto:{self.value.strip()}"
        if self.item_type == self.ItemType.PHONE and self.value:
            tel = "".join(c for c in self.value if c.isdigit() or c in "+")
            return f"tel:{tel}" if tel else None
        if self.item_type == self.ItemType.TELEGRAM and self.value:
            uname = self.value.strip().lstrip("@")
            return f"https://t.me/{uname}" if uname else None
        if self.item_type == self.ItemType.WHATSAPP and self.value:
            tel = "".join(c for c in self.value if c.isdigit())
            return f"https://wa.me/{tel}" if tel else None
        if self.item_type == self.ItemType.MAX and self.value:
            v = self.value.strip()
            if v.startswith("http"):
                return v
            return None  # Для MAX укажите ссылку в поле «Ссылка» или полный URL в значении
        if self.item_type == self.ItemType.VK and self.value:
            v = self.value.strip()
            if v.startswith("http"):
                return v
            return f"https://vk.com/{v.lstrip('/')}" if v else None
        if self.item_type == self.ItemType.WEBSITE and self.value:
            v = self.value.strip()
            return v if v.startswith("http") else f"https://{v}"
        return None


class Page(models.Model):
    """Static page model (about, etc.). Content supports Markdown."""

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
