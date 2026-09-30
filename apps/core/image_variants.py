"""Уменьшенные копии изображений для карточек и аватаров.

Оригинал остаётся для полноразмерного просмотра. В списках используется
JPEG с ограничением длинной стороны, чтобы не скачивать фото на сотни килобайт.
"""

from __future__ import annotations

import hashlib
import io
from typing import BinaryIO

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import Storage, default_storage
from django.utils.encoding import uri_to_iri
from loguru import logger

CACHE_CONTROL = "public, max-age=2592000"

# Длинная сторона и качество JPEG. Карточка турнира на экране 72px, аватар меньше;
# 2–3x запас покрывает плотность экрана, магазин показывает фото крупнее.
VARIANT_SPECS: dict[str, tuple[int, int]] = {
    "avatar": (160, 78),
    "card": (640, 78),
}


def variant_key(original_name: str, kind: str) -> str:
    """Стабильный ключ копии в хранилище.

    Args:
        original_name: Путь исходного файла в storage.
        kind: Вид копии, ключ из ``VARIANT_SPECS``.

    Returns:
        str: Ключ вида ``variants/<kind>/<hash>.jpg``.
    """
    digest = hashlib.sha1(original_name.encode("utf-8")).hexdigest()[:20]
    return f"variants/{kind}/{digest}.jpg"


def name_from_media_url(url: str) -> str | None:
    """Достаёт ключ storage из публичного URL медиа.

    Args:
        url: Абсолютный или относительный URL файла.

    Returns:
        str | None: Ключ в хранилище либо None, если это не медиа проекта.
    """
    if not url:
        return None
    media_url = str(getattr(settings, "MEDIA_URL", "") or "")
    if media_url and url.startswith(media_url):
        return str(uri_to_iri(url[len(media_url) :].split("?", 1)[0]))
    marker = "/media/"
    if marker in url:
        return str(uri_to_iri(url.split(marker, 1)[1].split("?", 1)[0]))
    return None


def render_variant(data: bytes, kind: str) -> bytes:
    """Уменьшает изображение до JPEG заданного вида.

    Args:
        data: Байты исходного изображения.
        kind: Вид копии из ``VARIANT_SPECS``.

    Returns:
        bytes: JPEG-копия.

    Raises:
        ValueError: Неизвестный вид или файл не открывается как изображение.
    """
    spec = VARIANT_SPECS.get(kind)
    if spec is None:
        raise ValueError(f"Неизвестный вид копии: {kind}")
    max_edge, quality = spec
    try:
        from PIL import Image, ImageOps
    except ImportError as err:
        raise ValueError("Pillow не установлен") from err

    opened = Image.open(io.BytesIO(data))
    transposed = ImageOps.exif_transpose(opened)
    prepared = transposed if transposed.mode == "RGB" else transposed.convert("RGB")
    prepared.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    prepared.save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


def variant_url(value: object, kind: str = "card") -> str:
    """URL уменьшенной копии либо исходный адрес, если ключ неизвестен.

    Args:
        value: FieldFile или строковый URL.
        kind: Вид копии.

    Returns:
        str: Адрес копии или исходный URL.
    """
    if kind not in VARIANT_SPECS:
        kind = "card"
    name, storage = _source(value)
    if not name or storage is None:
        if isinstance(value, str):
            return value
        url_getter = getattr(value, "url", None)
        return str(url_getter) if isinstance(url_getter, str) else ""
    return str(storage.url(variant_key(name, kind)))


def ensure_display_variants(name: str, storage: Storage | None = None) -> list[str]:
    """Создаёт копии для карточки и аватара и обновляет кэш исходника.

    Args:
        name: Ключ исходного файла.
        storage: Хранилище. По умолчанию ``default_storage``.

    Returns:
        list[str]: Ключи записанных копий.
    """
    storage = storage or default_storage
    if not name or not storage.exists(name):
        return []
    with storage.open(name, "rb") as source:
        data = _read_all(source)
    written: list[str] = []
    for kind in VARIANT_SPECS:
        key = variant_key(name, kind)
        payload = render_variant(data, kind)
        _put_bytes(storage, key, payload)
        written.append(key)
    try:
        _set_cache_header(storage, name)
    except Exception as exc:
        logger.warning("Не удалось обновить кэш {}: {}", name, exc)
    logger.info("Копии изображения {} записаны: {}", name, ", ".join(written))
    return written


def _source(value: object) -> tuple[str | None, Storage | None]:
    """Выделяет ключ и storage из FieldFile или URL.

    Args:
        value: FieldFile или строка.

    Returns:
        tuple[str | None, Storage | None]: Ключ и хранилище.
    """
    name = getattr(value, "name", None)
    storage = getattr(value, "storage", None)
    if isinstance(name, str) and name and storage is not None:
        return name, storage
    if isinstance(value, str):
        parsed = name_from_media_url(value)
        if parsed:
            return parsed, default_storage
    return None, None


def _read_all(source: BinaryIO) -> bytes:
    """Читает файл хранилища целиком.

    Args:
        source: Открытый бинарный поток.

    Returns:
        bytes: Содержимое.
    """
    return source.read()


def _put_bytes(storage: Storage, key: str, payload: bytes) -> None:
    """Записывает JPEG по точному ключу, без случайного суффикса.

    Args:
        storage: Хранилище Django.
        key: Ключ объекта.
        payload: Байты JPEG.
    """
    bucket = getattr(storage, "bucket", None)
    if bucket is not None:
        extra: dict[str, str] = {}
        acl = getattr(settings, "AWS_DEFAULT_ACL", None)
        if acl:
            extra["ACL"] = acl
        bucket.put_object(
            Key=key,
            Body=payload,
            ContentType="image/jpeg",
            CacheControl=CACHE_CONTROL,
            **extra,
        )
        return
    if storage.exists(key):
        storage.delete(key)
    storage.save(key, ContentFile(payload, name=key))


def _set_cache_header(storage: Storage, key: str) -> None:
    """Ставит Cache-Control существующему объекту S3, не меняя байты.

    Args:
        storage: Хранилище. Для локального диска вызов ничего не делает.
        key: Ключ объекта.
    """
    bucket = getattr(storage, "bucket", None)
    if bucket is None:
        return
    head = bucket.meta.client.head_object(Bucket=bucket.name, Key=key)
    content_type = head.get("ContentType") or "application/octet-stream"
    bucket.copy(
        {"Bucket": bucket.name, "Key": key},
        key,
        ExtraArgs={
            "MetadataDirective": "REPLACE",
            "ContentType": content_type,
            "CacheControl": CACHE_CONTROL,
            "ACL": getattr(settings, "AWS_DEFAULT_ACL", None) or "public-read",
        },
    )
