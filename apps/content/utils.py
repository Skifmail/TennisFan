"""
Утилиты контента: slug и парсинг видео.
"""

from __future__ import annotations

import re
from typing import TypeVar, cast

from django.db import models
from django.utils.text import slugify

SLUG_MAX_LENGTH = 50
SUFFIX_RESERVED = 4

TModel = TypeVar("TModel", bound=models.Model)


def generate_unique_slug(
    *,
    model: type[TModel],
    name: str,
    slug: str | None = None,
    instance: TModel | None = None,
    exclude_pk: int | None = None,
    fallback: str = "item",
    suffix_start: int = 2,
) -> str:
    """Генерирует уникальный slug для модели с полем slug.

    Если slug уже занят другим объектом или пустой — создаётся уникальный
    вариант путём добавления суффикса -2, -3 и т.д. Базовый slug обрезается,
    чтобы оставить место для суффикса (лимит SlugField — 50 символов).

    Args:
        model: Класс модели с уникальным полем ``slug``.
        name: Название (используется, если slug пустой).
        slug: Текущее значение slug (может быть из prepopulated или ввода).
        instance: Редактируемый экземпляр (исключается из проверки уникальности).
        exclude_pk: PK исключаемого объекта, если нет самого экземпляра.
        fallback: Значение по умолчанию, если name и slug пустые.
        suffix_start: С какого числа начинать суффикс (по умолчанию -2).

    Returns:
        Уникальный slug, подходящий под ограничение max_length.
    """
    raw_slug = (slug or "").strip()
    base = raw_slug or cast(str, slugify(name, allow_unicode=True))
    if not base:
        base = fallback
    base = base[: SLUG_MAX_LENGTH - SUFFIX_RESERVED].rstrip("-")
    candidate = base
    n = max(1, suffix_start)
    queryset = model.objects.all()
    if instance is not None and instance.pk is not None:
        queryset = queryset.exclude(pk=instance.pk)
    elif exclude_pk is not None:
        queryset = queryset.exclude(pk=exclude_pk)
    while queryset.filter(slug=candidate).exists():
        suffix = f"-{n}"
        if len(base) + len(suffix) > SLUG_MAX_LENGTH:
            base = base[: SLUG_MAX_LENGTH - len(suffix)].rstrip("-") or fallback
        candidate = base + suffix
        n += 1
    return candidate


def extract_youtube_video_id(url: str) -> str | None:
    """Извлекает ID видео из URL YouTube."""
    patterns = [
        r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([a-zA-Z0-9_-]{11})",
        r"youtube\.com/watch\?.*v=([a-zA-Z0-9_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def extract_vk_video_id(url: str) -> tuple[str, str] | None:
    """
    Извлекает owner_id и video_id из URL VK.

    Поддерживаемые форматы:
    - https://vk.com/video-123456_789012
    - https://vk.com/video123456_789012
    - https://vk.com/vkvideo?z=video-123456_789012%2F...
    - https://vk.com/clip-123456_789012

    Returns:
        Кортеж (owner_id, video_id) или None
    """
    from urllib.parse import unquote

    # Декодируем URL если он закодирован
    url = unquote(url)

    patterns = [
        # Прямая ссылка: video-123456_789012 или video123456_789012
        r"video(-?\d+)_(\d+)",
        # Клипы: clip-123456_789012
        r"clip(-?\d+)_(\d+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return (match.group(1), match.group(2))

    return None


def extract_rutube_video_id(url: str) -> str | None:
    """Извлекает ID видео из URL RuTube."""
    # RuTube: https://rutube.ru/video/VIDEO_ID/ или https://rutube.ru/play/embed/VIDEO_ID/
    patterns = [
        r"rutube\.ru/video/([a-zA-Z0-9_-]+)",
        r"rutube\.ru/play/embed/([a-zA-Z0-9_-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def get_video_embed_url(url: str, platform: str) -> str | None:
    """
    Преобразует URL видео в URL для встраивания (embed).

    Args:
        url: Исходный URL видео
        platform: Платформа (youtube, vk, rutube)

    Returns:
        URL для встраивания или None если не удалось распарсить
    """
    if platform == "youtube":
        video_id = extract_youtube_video_id(url)
        if video_id:
            # Стандартный YouTube embed URL
            return f"https://www.youtube.com/embed/{video_id}"

    elif platform == "vk":
        result = extract_vk_video_id(url)
        if result:
            owner_id, video_id = result
            # VK Video embed URL
            # Формат: https://vk.com/video_ext.php?oid=OWNER_ID&id=VIDEO_ID
            return f"https://vk.com/video_ext.php?oid={owner_id}&id={video_id}"
        return None

    elif platform == "rutube":
        video_id = extract_rutube_video_id(url)
        if video_id:
            return f"https://rutube.ru/play/embed/{video_id}"

    return None
