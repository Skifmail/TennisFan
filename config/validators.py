"""
Общие валидаторы и утилиты для изображений.
"""

import io
import logging

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import models

logger = logging.getLogger(__name__)

# Лимит размера файла: 500 КБ. Все загружаемые фото сжимаются до этого размера при сохранении.
MAX_IMAGE_SIZE_KB = 500
MAX_IMAGE_SIZE_BYTES = MAX_IMAGE_SIZE_KB * 1024

# Макс. размер загружаемого файла (отклоняем с понятной ошибкой, иначе платформа обрывает запрос).
MAX_IMAGE_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 МБ


def validate_image_max_2mb(value):
    """
    Валидатор размера изображения (имя сохранено для совместимости).
    Файлы > 10 МБ отклоняются (платформа может обрывать большие загрузки через 5–10 с).
    Файлы до 10 МБ принимаются и при сохранении сжимаются до 500 КБ (CompressImageFieldsMixin).
    """
    if not value:
        return
    if value.size > MAX_IMAGE_UPLOAD_BYTES:
        raise ValidationError(
            f"Изображение не должно превышать 10 МБ (сейчас {value.size // (1024 * 1024)} МБ). "
            "Сожмите фото на устройстве или загрузите файл меньшего размера."
        )


def compress_image_to_max_bytes(file_like, max_bytes=MAX_IMAGE_SIZE_BYTES):
    """
    Сжимает изображение до размера не более max_bytes с минимальной потерей качества.
    Сначала плавно снижает качество JPEG (начиная с 92), при необходимости уменьшает разрешение
    (LANCZOS). Исходники меньше лимита не пережимаются.

    :param file_like: файл или bytes
    :param max_bytes: максимальный размер в байтах
    :return: (bytes, format_ext) — сжатые данные и расширение ('jpg' или 'png')
    :raises ValueError: если изображение не удалось открыть
    """
    try:
        from PIL import Image
    except ImportError:
        raise ValueError("Pillow не установлен. Установите: pip install Pillow")

    if hasattr(file_like, "read"):
        file_like.seek(0)
        data = file_like.read()
    else:
        data = file_like

    img = Image.open(io.BytesIO(data)).copy()
    try:
        from PIL import ImageOps
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass

    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
        out_ext = "jpg"
        out_format = "JPEG"
    elif img.mode != "RGB":
        img = img.convert("RGB")
        out_ext = "jpg"
        out_format = "JPEG"
    else:
        out_ext = "jpg"
        out_format = "JPEG"

    buf = io.BytesIO()
    quality = 92
    scale = 1.0

    while True:
        buf.seek(0)
        buf.truncate(0)
        if scale < 1.0:
            w, h = img.size
            new_w = max(1, int(w * scale))
            new_h = max(1, int(h * scale))
            resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            resized.save(buf, format=out_format, quality=quality, optimize=True)
        else:
            img.save(buf, format=out_format, quality=quality, optimize=True)

        if buf.tell() <= max_bytes:
            return buf.getvalue(), out_ext
        if quality > 45:
            quality -= 8
            continue
        if scale > 0.35:
            scale *= 0.88
            quality = 88
            continue
        buf.seek(0)
        buf.truncate(0)
        w, h = img.size
        new_w = max(1, int(w * 0.5))
        new_h = max(1, int(h * 0.5))
        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        img.save(buf, format=out_format, quality=70, optimize=True)
        return buf.getvalue(), out_ext


class CompressImageFieldsMixin:
    """
    Миксин для моделей с ImageField. При сохранении все загруженные изображения
    размером больше MAX_IMAGE_SIZE_BYTES (500 КБ) автоматически сжимаются до этого лимита.
    """

    def save(self, *args, **kwargs):
        for field in self._meta.get_fields():
            if not isinstance(field, models.ImageField):
                continue
            file_obj = getattr(self, field.name, None)
            if not file_obj or not getattr(file_obj, "size", None):
                continue
            if file_obj.size <= MAX_IMAGE_SIZE_BYTES:
                continue
            try:
                compressed_bytes, ext = compress_image_to_max_bytes(file_obj, MAX_IMAGE_SIZE_BYTES)
                name = getattr(file_obj, "name", None) or f"image.{ext}"
                if not name.lower().endswith((".jpg", ".jpeg", ".png")):
                    name = f"{name.rsplit('.', 1)[0] if '.' in name else name}.{ext}"
                new_file = ContentFile(compressed_bytes, name=name)
                setattr(self, field.name, new_file)
                logger.info(
                    "Сжато изображение %s.%s: было %s байт, стало %s байт",
                    self.__class__.__name__,
                    field.name,
                    file_obj.size,
                    len(compressed_bytes),
                )
            except Exception as e:
                logger.warning("Не удалось сжать изображение %s.%s: %s", self.__class__.__name__, field.name, e)
        super().save(*args, **kwargs)
