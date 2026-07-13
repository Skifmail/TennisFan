"""
Content models: News, Gallery, Pages.
"""

from typing import cast

from django.db import models

from apps.content.utils import generate_unique_slug
from apps.core.contact_utils import (
    build_max_url,
    build_telegram_url,
    build_whatsapp_url,
    normalize_russian_phone,
)
from config.validators import CompressImageFieldsMixin, validate_image_max_2mb


class News(CompressImageFieldsMixin, models.Model):
    """News article model."""

    title = models.CharField("Заголовок", max_length=200)
    slug = models.SlugField("URL", unique=True, blank=True)
    excerpt = models.CharField("Краткое описание", max_length=300, blank=True)
    content = models.TextField("Содержание")
    image = models.ImageField(
        "Изображение",
        upload_to="news/",
        blank=True,
        validators=[validate_image_max_2mb],
    )

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
        return str(self.title)

    def save(self, *args, **kwargs):
        """Сохраняет новость с гарантированно уникальным slug."""
        self.slug = generate_unique_slug(
            model=News,
            name=str(self.title),
            slug=self.slug,
            instance=self,
            fallback="news",
        )
        super().save(*args, **kwargs)


class NewsPhoto(CompressImageFieldsMixin, models.Model):
    """Фото в галерее новости (дополнительные изображения к статье)."""

    news = models.ForeignKey(
        News,
        on_delete=models.CASCADE,
        related_name="photos",
        verbose_name="Новость",
    )
    image = models.ImageField(
        "Фото", upload_to="news/gallery/", validators=[validate_image_max_2mb]
    )
    caption = models.CharField("Подпись", max_length=200, blank=True)
    order = models.PositiveSmallIntegerField("Порядок", default=0)

    class Meta:
        verbose_name = "Фото новости"
        verbose_name_plural = "Фото новостей"
        ordering = ["order", "id"]

    def __str__(self) -> str:
        return f"Фото {self.id} к новости «{self.news.title}»"


class Gallery(CompressImageFieldsMixin, models.Model):
    """Photo gallery model."""

    title = models.CharField("Название", max_length=200)
    slug = models.SlugField("URL", unique=True, blank=True)
    description = models.TextField("Описание", blank=True)
    cover_image = models.ImageField(
        "Обложка",
        upload_to="galleries/covers/",
        blank=True,
        validators=[validate_image_max_2mb],
    )

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
        return str(self.title)

    def save(self, *args, **kwargs):
        """Сохраняет галерею с гарантированно уникальным slug."""
        self.slug = generate_unique_slug(
            model=Gallery,
            name=str(self.title),
            slug=self.slug,
            instance=self,
            fallback="gallery",
        )
        super().save(*args, **kwargs)

    @property
    def photos_count(self) -> int:
        return int(self.photos.count())


class Photo(CompressImageFieldsMixin, models.Model):
    """Photo in a gallery."""

    gallery = models.ForeignKey(
        Gallery, on_delete=models.CASCADE, related_name="photos", verbose_name="Галерея"
    )
    image = models.ImageField(
        "Фото", upload_to="galleries/photos/", validators=[validate_image_max_2mb]
    )
    caption = models.CharField("Подпись", max_length=200, blank=True)
    order = models.PositiveSmallIntegerField("Порядок", default=0)

    created_at = models.DateTimeField("Загружено", auto_now_add=True)

    class Meta:
        verbose_name = "Фотография"
        verbose_name_plural = "Фотографии"
        ordering = ["order", "-created_at"]

    def __str__(self) -> str:
        return f"Фото {self.id} в {self.gallery}"


class AboutUs(CompressImageFieldsMixin, models.Model):
    """
    Singleton model for "О нас" page.
    Заголовок "О НАС" фиксирован в шаблоне.
    """

    subtitle = models.CharField("Подзаголовок", max_length=300, blank=True)
    image = models.ImageField(
        "Фото", upload_to="about/", blank=True, validators=[validate_image_max_2mb]
    )
    body = models.TextField(
        "Статья",
        blank=True,
        help_text="Поддерживается Markdown (заголовки, списки, ссылки, жирный и т.п.).",
    )
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Страница «О нас»"
        verbose_name_plural = "Страница «О нас»"

    def __str__(self) -> str:
        return "О нас"

    @classmethod
    def get_singleton(cls) -> "AboutUs":
        """Return the single AboutUs instance, creating if needed."""
        obj = cls.objects.first()
        if obj is None:
            obj = cls.objects.create(subtitle="", body="")
        return cast("AboutUs", obj)


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
        verbose_name = "Страница «Контакты»"
        verbose_name_plural = "Страница «Контакты»"

    def __str__(self) -> str:
        return "Контакты"

    @classmethod
    def get_singleton(cls) -> "ContactPage":
        """Return the single ContactPage instance, creating if needed."""
        obj = cls.objects.first()
        if obj is None:
            obj = cls.objects.create(intro_text="")
        return cast("ContactPage", obj)


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
        return f"{str(label)}: {self.value[:50]}{'…' if len(self.value) > 50 else ''}"

    @property
    def display_label(self) -> str:
        """Подпись для отображения."""
        return str(self.label or self.get_item_type_display())

    @property
    def clickable_url(self) -> str | None:
        """URL для перехода или None."""
        if self.url:
            return str(self.url)
        if self.item_type == self.ItemType.EMAIL and self.value:
            return f"mailto:{self.value.strip()}"
        if self.item_type == self.ItemType.PHONE and self.value:
            tel = normalize_russian_phone(self.value)
            return f"tel:{tel}" if tel else None
        if self.item_type == self.ItemType.TELEGRAM and self.value:
            return build_telegram_url(self.value)
        if self.item_type == self.ItemType.WHATSAPP and self.value:
            return build_whatsapp_url(self.value)
        if self.item_type == self.ItemType.MAX and self.value:
            return build_max_url(self.value)
        if self.item_type == self.ItemType.VK and self.value:
            v = self.value.strip()
            if v.startswith("http"):
                return str(v)
            return f"https://vk.com/{v.lstrip('/')}" if v else None
        if self.item_type == self.ItemType.WEBSITE and self.value:
            v = self.value.strip()
            return str(v) if v.startswith("http") else f"https://{v}"
        return None


class RulesSection(models.Model):
    """
    Редактируемый блок правил на странице «Правила».
    slug однозначно определяет раздел (tennis_rules, rules_fan, rules_round_robin,
    rules_doubles, site_usage_rules). body — HTML-контент для вставки в шаблон.
    Ссылки на PDF в разделе «Правила тенниса» остаются в шаблоне и не редактируются.
    """

    slug = models.SlugField("Код раздела", max_length=50, unique=True)
    title = models.CharField("Название (для админки)", max_length=200)
    body = models.TextField(
        "Содержание (HTML)",
        blank=True,
        help_text="HTML-разметка. Для раздела «Правила тенниса» здесь только текст без заголовка и ссылок на PDF.",
    )
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Раздел правил"
        verbose_name_plural = "Разделы правил"
        ordering = ["slug"]

    def __str__(self) -> str:
        return str(self.title)


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
        verbose_name = "Страница (текстовая)"
        verbose_name_plural = "Страницы (текстовые)"
        ordering = ["order"]

    def __str__(self) -> str:
        return str(self.title)


class VideoPage(models.Model):
    """
    Singleton модель для страницы «Видео».
    Хранит настройки страницы: заголовки блоков.
    """

    live_streams_title = models.CharField(
        "Заголовок блока «Прямые трансляции»",
        max_length=200,
        default="Прямые трансляции",
    )
    playlist_title = models.CharField(
        "Заголовок блока «Плейлист»",
        max_length=200,
        default="Плейлист",
    )
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Страница «Видео»"
        verbose_name_plural = "Страница «Видео»"

    def __str__(self) -> str:
        return "Страница «Видео»"

    @classmethod
    def get_singleton(cls) -> "VideoPage":
        """Return the single VideoPage instance, creating if needed."""
        obj = cls.objects.first()
        if obj is None:
            obj = cls.objects.create(
                live_streams_title="Прямые трансляции",
                playlist_title="Плейлист",
            )
        return cast("VideoPage", obj)


class VideoPlatform(models.TextChoices):
    """Платформы для видео."""

    YOUTUBE = "youtube", "YouTube"
    VK = "vk", "VK"
    RUTUBE = "rutube", "RuTube"


class LiveStream(models.Model):
    """Прямая трансляция на странице «Видео»."""

    video_page = models.ForeignKey(
        VideoPage,
        on_delete=models.CASCADE,
        related_name="live_streams",
        verbose_name="Страница видео",
        null=True,
        blank=True,
    )
    title = models.CharField("Название трансляции", max_length=200)
    url = models.URLField(
        "Ссылка на трансляцию",
        help_text=(
            "YouTube: любая ссылка (youtube.com/watch?v=... или youtu.be/...). "
            "VK: используйте формат vk.com/video-XXXXX_YYYYY. "
            "RuTube: любая ссылка на видео."
        ),
    )
    platform = models.CharField(
        "Платформа",
        max_length=20,
        choices=VideoPlatform.choices,
        default=VideoPlatform.YOUTUBE,
    )
    is_active = models.BooleanField("Активна (показывать)", default=True)
    order = models.PositiveSmallIntegerField("Порядок", default=0)
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        verbose_name = "Прямая трансляция"
        verbose_name_plural = "Прямые трансляции"
        ordering = ["order", "-created_at"]

    def __str__(self) -> str:
        disp = self.get_platform_display()
        return f"{self.title} ({disp[1] if isinstance(disp, tuple) else disp})"

    def save(self, *args, **kwargs):
        """Автоматически определяем платформу по URL при сохранении."""
        if self.url:
            self.platform = self._detect_platform(self.url)
        super().save(*args, **kwargs)

    @staticmethod
    def _detect_platform(url: str) -> str:
        """Определяет платформу по URL."""
        url_lower = url.lower()
        if "youtube.com" in url_lower or "youtu.be" in url_lower:
            return str(VideoPlatform.YOUTUBE)
        if "vk.com" in url_lower or "vk.ru" in url_lower:
            return str(VideoPlatform.VK)
        if "rutube.ru" in url_lower:
            return str(VideoPlatform.RUTUBE)
        return str(VideoPlatform.YOUTUBE)  # По умолчанию

    def get_embed_url(self) -> str | None:
        """Возвращает URL для встраивания трансляции."""
        from apps.content.utils import get_video_embed_url

        return cast(str | None, get_video_embed_url(self.url, self.platform))


class Video(models.Model):
    """Видео в плейлисте на странице «Видео»."""

    video_page = models.ForeignKey(
        VideoPage,
        on_delete=models.CASCADE,
        related_name="videos",
        verbose_name="Страница видео",
        null=True,
        blank=True,
    )
    title = models.CharField("Название видео", max_length=200)
    description = models.TextField("Описание", blank=True)
    url = models.URLField(
        "Ссылка на видео",
        help_text=(
            "YouTube: любая ссылка (youtube.com/watch?v=... или youtu.be/...). "
            "VK: используйте формат vk.com/video-XXXXX_YYYYY. "
            "RuTube: любая ссылка на видео."
        ),
    )
    platform = models.CharField(
        "Платформа",
        max_length=20,
        choices=VideoPlatform.choices,
        default=VideoPlatform.YOUTUBE,
    )
    thumbnail_url = models.URLField(
        "URL превью (опционально)",
        blank=True,
        help_text="Ссылка на изображение превью. Если не указано, будет использовано превью с платформы.",
    )
    is_published = models.BooleanField("Опубликовано", default=True)
    order = models.PositiveSmallIntegerField("Порядок", default=0)
    views_count = models.PositiveIntegerField("Просмотры", default=0)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Видео"
        verbose_name_plural = "Видео"
        ordering = ["order", "-created_at"]

    def __str__(self) -> str:
        disp = self.get_platform_display()
        return f"{self.title} ({disp[1] if isinstance(disp, tuple) else disp})"

    def save(self, *args, **kwargs):
        """Автоматически определяем платформу по URL при сохранении."""
        if self.url:
            self.platform = self._detect_platform(self.url)
        super().save(*args, **kwargs)

    @staticmethod
    def _detect_platform(url: str) -> str:
        """Определяет платформу по URL."""
        url_lower = url.lower()
        if "youtube.com" in url_lower or "youtu.be" in url_lower:
            return str(VideoPlatform.YOUTUBE)
        if "vk.com" in url_lower or "vk.ru" in url_lower:
            return str(VideoPlatform.VK)
        if "rutube.ru" in url_lower:
            return str(VideoPlatform.RUTUBE)
        return str(VideoPlatform.YOUTUBE)  # По умолчанию

    def get_embed_url(self) -> str | None:
        """Возвращает URL для встраивания видео."""
        from apps.content.utils import get_video_embed_url

        return cast(str | None, get_video_embed_url(self.url, self.platform))


# ---------------------------------------------------------------------------
# Стрингеры (компании по натяжке струн на ракетки)
# ---------------------------------------------------------------------------


class StringerPage(models.Model):
    """
    Singleton модель для страницы «Стрингеры».
    Хранит настройки страницы: заголовок, описание.
    """

    title = models.CharField("Заголовок страницы", max_length=200, default="Стрингеры")
    description = models.TextField(
        "Описание страницы",
        blank=True,
        help_text="Текст, который отображается вверху страницы перед списком компаний.",
    )
    is_enabled = models.BooleanField("Включить страницу", default=True)

    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Страница «Стрингеры»"
        verbose_name_plural = "Страница «Стрингеры»"

    def __str__(self) -> str:
        return str(self.title)

    def save(self, *args, **kwargs):
        """Обеспечиваем singleton: всегда только одна запись."""
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_singleton(cls) -> "StringerPage":
        """Получить или создать единственный экземпляр."""
        obj, _ = cls.objects.get_or_create(pk=1, defaults={"title": "Стрингеры"})
        return cast("StringerPage", obj)


class StringerCompanyContactType(models.TextChoices):
    """Тип контакта компании стрингеров."""

    PHONE = "phone", "Телефон"
    TELEGRAM = "telegram", "Telegram"
    WHATSAPP = "whatsapp", "WhatsApp"
    MAX = "max", "MAX"


class StringerCompany(CompressImageFieldsMixin, models.Model):
    """Компания по натяжке струн на ракетки."""

    stringer_page = models.ForeignKey(
        StringerPage,
        on_delete=models.CASCADE,
        related_name="companies",
        verbose_name="Страница стрингеров",
        null=True,
        blank=True,
    )
    name = models.CharField("Наименование", max_length=200)
    address = models.CharField("Адрес", max_length=300)
    price = models.CharField(
        "Стоимость",
        max_length=200,
        blank=True,
        help_text="Например: 'от 500 руб.', '500-1000 руб.' и т.д.",
    )
    description = models.TextField("Описание", blank=True)
    is_active = models.BooleanField("Активна (показывать)", default=True)
    order = models.PositiveSmallIntegerField("Порядок", default=0)

    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Компания стрингеров"
        verbose_name_plural = "Компании стрингеров"
        ordering = ["order", "name"]

    def __str__(self) -> str:
        return str(self.name)

    def get_average_rating(self) -> float | None:
        """Возвращает средний рейтинг компании."""
        from django.db.models import Avg

        avg = self.ratings.aggregate(Avg("score"))["score__avg"]
        return float(round(avg, 1)) if avg is not None else None

    def get_rating_count(self) -> int:
        """Возвращает количество оценок."""
        return int(self.ratings.count())


class StringerCompanyContact(models.Model):
    """Контакт компании стрингеров: телефон, Telegram, WhatsApp, MAX."""

    company = models.ForeignKey(
        StringerCompany,
        on_delete=models.CASCADE,
        related_name="contact_items",
        verbose_name="Компания",
    )
    contact_type = models.CharField(
        "Тип контакта",
        max_length=20,
        choices=StringerCompanyContactType.choices,
    )
    value = models.CharField(
        "Значение",
        max_length=300,
        help_text=(
            "Телефон: номер (например 79991234567). "
            "Telegram: @username или username. "
            "WhatsApp: номер. MAX: ссылка на профиль или номер.",
        ),
    )
    order = models.PositiveSmallIntegerField("Порядок", default=0)

    class Meta:
        verbose_name = "Контакт компании"
        verbose_name_plural = "Контакты компании"
        ordering = ["order", "id"]

    def __str__(self) -> str:
        return f"{self.get_contact_type_display()}: {self.value}"

    def get_link_url(self) -> str | None:
        """Возвращает URL для перехода по контакту (tel:, t.me, wa.me, ссылка MAX)."""
        if not self.value or not self.value.strip():
            return None
        v = self.value.strip()
        if self.contact_type == StringerCompanyContactType.PHONE:
            phone = normalize_russian_phone(v)
            return f"tel:{phone}" if phone else None
        if self.contact_type == StringerCompanyContactType.TELEGRAM:
            return build_telegram_url(v)
        if self.contact_type == StringerCompanyContactType.WHATSAPP:
            return build_whatsapp_url(v)
        if self.contact_type == StringerCompanyContactType.MAX:
            return build_max_url(v)
        return None

    def get_icon_path(self) -> str:
        """Путь к иконке мессенджера в static (для шаблона)."""
        icons = {
            StringerCompanyContactType.PHONE: "images/chat_icon.png",
            StringerCompanyContactType.TELEGRAM: "images/Telegram_logo.svg",
            StringerCompanyContactType.WHATSAPP: "images/WhatsApp.svg",
            StringerCompanyContactType.MAX: "images/Max_logo_2025.png",
        }
        return str(icons.get(self.contact_type, "images/chat_icon.png"))


class StringerCompanyPhoto(CompressImageFieldsMixin, models.Model):
    """Фото компании стрингеров."""

    company = models.ForeignKey(
        StringerCompany,
        on_delete=models.CASCADE,
        related_name="photos",
        verbose_name="Компания",
    )
    image = models.ImageField(
        "Фото",
        upload_to="stringers/photos/",
        validators=[validate_image_max_2mb],
    )
    caption = models.CharField("Подпись", max_length=200, blank=True)
    order = models.PositiveSmallIntegerField("Порядок", default=0)

    created_at = models.DateTimeField("Загружено", auto_now_add=True)

    class Meta:
        verbose_name = "Фото компании"
        verbose_name_plural = "Фото компаний"
        ordering = ["order", "-created_at"]

    def __str__(self) -> str:
        return f"Фото {self.id} для {self.company.name}"


class StringerCompanyRating(models.Model):
    """Оценка компании стрингеров от пользователя."""

    company = models.ForeignKey(
        StringerCompany,
        on_delete=models.CASCADE,
        related_name="ratings",
        verbose_name="Компания",
    )
    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="stringer_ratings",
        verbose_name="Пользователь",
    )
    score = models.PositiveSmallIntegerField(
        "Оценка",
        choices=[(i, str(i)) for i in range(1, 6)],
        help_text="Оценка от 1 до 5",
    )
    comment = models.TextField("Комментарий", blank=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Оценка компании"
        verbose_name_plural = "Оценки компаний"
        unique_together = [["company", "user"]]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.user} → {self.company}: {self.score}/5"
