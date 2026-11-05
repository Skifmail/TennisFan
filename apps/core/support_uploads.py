"""Проверка и приватное хранение скриншотов обращений поддержки."""

from io import BytesIO
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile, File
from django.core.files.storage import FileSystemStorage
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_ATTACHMENTS = 3
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def private_support_storage() -> FileSystemStorage:
    """Возвращает хранилище вне публичного MEDIA_URL, в постоянном закрытом томе."""
    return FileSystemStorage(
        location=getattr(
            settings,
            "SUPPORT_PRIVATE_MEDIA_ROOT",
            Path(settings.BASE_DIR) / "private_media" / "support",
        )
    )


def support_image_path(instance: object, filename: str) -> str:
    """Создаёт имя без исходного названия файла и персональных сведений."""
    return f"screenshots/{uuid4().hex}.jpg"


def prepare_support_image(upload: File) -> ContentFile:
    """Проверяет содержимое картинки, удаляет метаданные и нормализует формат."""
    if not upload.size or upload.size > MAX_IMAGE_BYTES:
        raise ValidationError("Каждое изображение должно быть не больше 5 МБ.")
    try:
        upload.seek(0)
        with Image.open(upload) as image:
            if image.format not in {"JPEG", "PNG", "WEBP"}:
                raise ValidationError("Прикрепите JPG, PNG или WebP.")
            if image.width * image.height > 20_000_000:
                raise ValidationError("Уменьшите изображение до 20 мегапикселей.")
            normalized = ImageOps.exif_transpose(image).convert("RGB")
            normalized.thumbnail((1920, 1920))
            result = BytesIO()
            normalized.save(result, "JPEG", quality=90)
    except (
        OSError,
        ValueError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
    ) as exc:
        raise ValidationError(
            "Не удалось прочитать изображение. Выберите другой JPG, PNG или WebP."
        ) from exc
    finally:
        upload.seek(0)
    return ContentFile(result.getvalue(), name="screenshot.jpg")
