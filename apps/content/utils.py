"""
Утилиты для работы с видео.
"""

import re


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
