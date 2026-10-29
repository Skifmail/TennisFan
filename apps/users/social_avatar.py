"""Безопасная загрузка аватаров из доверенных хранилищ соцсетей."""

from __future__ import annotations

import io
from urllib.parse import urlsplit

import requests
from django.core.files.base import ContentFile
from loguru import logger
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024
AVATAR_HOSTS = {
    "yandex": ("avatars.yandex.net",),
    "vk": ("userapi.com", "vkuserphoto.ru", "vk.me"),
}


def safe_avatar_url(provider: str, value: object) -> str:
    """Разрешить HTTPS-фото только с доменов провайдера без нестандартных портов."""
    if not isinstance(value, str) or len(value) > 4096:
        return ""
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        if (
            parts.scheme != "https"
            or parts.username
            or parts.password
            or parts.port not in (None, 443)
        ):
            return ""
    except ValueError:
        return ""
    if any(
        host == allowed or host.endswith("." + allowed)
        for allowed in AVATAR_HOSTS.get(provider, ())
    ):
        return value
    return ""


def download_avatar(provider: str, url: str) -> ContentFile | None:
    """Скачать ограниченное по размеру фото и преобразовать его в безопасный JPEG."""
    if not safe_avatar_url(provider, url):
        return None
    try:
        with requests.get(
            url, stream=True, timeout=(3, 8), allow_redirects=False
        ) as response:
            if response.status_code != 200:
                return None
            if int(response.headers.get("Content-Length", "0")) > MAX_DOWNLOAD_BYTES:
                return None
            data = bytearray()
            for chunk in response.iter_content(chunk_size=65536):
                data.extend(chunk)
                if len(data) > MAX_DOWNLOAD_BYTES:
                    return None
        with Image.open(io.BytesIO(data)) as image:
            if (
                image.format not in ("JPEG", "PNG", "WEBP")
                or image.width * image.height > 12_000_000
            ):
                return None
            image.load()
            cleaned = ImageOps.exif_transpose(image).convert("RGB")
            cleaned.thumbnail((800, 800))
            output = io.BytesIO()
            cleaned.save(output, format="JPEG", quality=85, optimize=True)
        return ContentFile(output.getvalue(), name="social-avatar.jpg")
    except (
        requests.RequestException,
        ValueError,
        OSError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
    ):
        return None


def import_social_avatar(player_id: int, provider: str, url: str) -> bool:
    """Добавить фото новому игроку после фиксации регистрации, не перезаписывая его аватар."""
    from django.db import transaction

    from .models import Player

    photo = download_avatar(provider, url)
    if photo is None:
        return False
    try:
        with transaction.atomic():
            player = Player.objects.select_for_update().get(pk=player_id)
            if not player.avatar:
                player.avatar.save(
                    f"social-{provider}-{player_id}.jpg", photo, save=True
                )
        return True
    except Exception:
        logger.warning(
            "Не удалось сохранить социальный аватар: player={} provider={}",
            player_id,
            provider,
        )
        return False
