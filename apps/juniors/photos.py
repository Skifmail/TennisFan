"""Закрытое хранение и подготовка фотографий для семейного кабинета."""

import uuid
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile, File
from django.core.files.storage import FileSystemStorage
from PIL import Image, ImageOps, UnidentifiedImageError

if TYPE_CHECKING:
    from .models import ChildProfile


def private_photo_storage() -> FileSystemStorage:
    """Хранит фотографии вне публичных MEDIA_ROOT и STATIC_ROOT."""
    location = getattr(
        settings,
        "JUNIOR_PRIVATE_MEDIA_ROOT",
        Path(settings.BASE_DIR) / "private_media" / "juniors",
    )
    return FileSystemStorage(location=location)


def photo_upload_path(instance: "ChildProfile", filename: str) -> str:
    """Создаёт случайное имя без ФИО и исходного имени файла."""
    return f"photos/{uuid.uuid4().hex}.jpg"


def prepare_photo(upload: File) -> ContentFile:
    """Проверяет фото, уменьшает его и удаляет метаданные EXIF."""
    if upload.size is None or upload.size > 5 * 1024 * 1024:
        raise ValidationError("Фото должно быть не больше 5 МБ.")
    try:
        upload.seek(0)
        with Image.open(upload) as image:
            if image.format not in {"JPEG", "PNG", "WEBP"}:
                raise ValidationError("Выберите фото в формате JPG, PNG или WebP.")
            if image.width * image.height > 20_000_000:
                raise ValidationError(
                    "Слишком большое разрешение фото. Уменьшите его до 20 мегапикселей."
                )
            normalized = ImageOps.exif_transpose(image).convert("RGB")
            normalized.thumbnail((1024, 1024))
            buffer = BytesIO()
            normalized.save(buffer, format="JPEG", quality=85)
    except (
        OSError,
        ValueError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
    ) as exc:
        raise ValidationError(
            "Не удалось прочитать фото. Выберите другой файл JPG, PNG или WebP."
        ) from exc
    finally:
        upload.seek(0)
    return ContentFile(buffer.getvalue(), name="photo.jpg")
