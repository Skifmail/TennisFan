"""Сохранение изображений общим миксином после уточнения его типов и MRO."""

from __future__ import annotations

from collections.abc import Iterator
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.files.base import ContentFile
from django.test import override_settings
from PIL import Image

from apps.core.image_variants import VARIANT_SPECS, variant_key
from apps.telegram_bot.models import TelegramBroadcast
from config.validators import MAX_IMAGE_SIZE_BYTES, compress_image_to_max_bytes

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def image_storage(tmp_path: Path) -> Iterator[None]:
    """Изолировать оригиналы и копии от рабочего хранилища изображений."""
    with override_settings(
        MEDIA_ROOT=str(tmp_path),
        STORAGES={
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {
                "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
            },
        },
    ):
        yield


def _image_bytes(*, image_format: str = "PNG", color: str = "navy") -> bytes:
    """Создать настоящее изображение без сети и случайных исходных данных."""
    image = Image.new("RGB", (900, 700), color)
    with BytesIO() as buffer:
        image.save(buffer, format=image_format)
        return buffer.getvalue()


def _assert_variants(broadcast: TelegramBroadcast) -> None:
    """Проверить копии в том же хранилище, что и сохранённый оригинал."""
    name = broadcast.image.name
    assert name is not None
    for kind, (max_edge, _) in VARIANT_SPECS.items():
        with broadcast.image.storage.open(variant_key(name, kind), "rb") as source:
            with Image.open(source) as variant:
                assert variant.format == "JPEG"
                assert max(variant.size) <= max_edge


def test_large_upload_is_compressed_before_model_save() -> None:
    """Миксин передаёт сохранение модели дальше по MRO и создаёт доступные копии."""
    original = _image_bytes(image_format="BMP")
    assert len(original) > MAX_IMAGE_SIZE_BYTES
    broadcast = TelegramBroadcast.objects.create(
        text="Фото для рассылки", image=ContentFile(original, name="large.bmp")
    )

    broadcast.refresh_from_db()
    assert broadcast.text == "Фото для рассылки"
    assert broadcast.image.name is not None
    assert broadcast.image.name.endswith(".jpg")
    assert broadcast.image.size <= MAX_IMAGE_SIZE_BYTES
    with broadcast.image.open("rb") as stored:
        with Image.open(stored) as image:
            assert image.format == "JPEG"
    _assert_variants(broadcast)


def test_small_upload_keeps_original_bytes_and_has_variants() -> None:
    """Изображение меньше лимита сохраняется без повторного JPEG-сжатия."""
    original = _image_bytes()
    broadcast = TelegramBroadcast.objects.create(
        text="Небольшое фото", image=ContentFile(original, name="small.png")
    )

    broadcast.refresh_from_db()
    with broadcast.image.open("rb") as stored:
        assert stored.read() == original
    _assert_variants(broadcast)


def test_scalar_update_does_not_regenerate_image_variants() -> None:
    """Частичное сохранение служебного статуса не переписывает уже созданные копии."""
    broadcast = TelegramBroadcast.objects.create(
        text="Фото", image=ContentFile(_image_bytes(), name="unchanged.png")
    )
    original_name = broadcast.image.name
    broadcast.status = TelegramBroadcast.Status.QUEUED

    with patch("apps.core.image_variants.ensure_display_variants") as variants:
        broadcast.save(update_fields=["status"])

    variants.assert_not_called()
    broadcast.refresh_from_db()
    assert broadcast.status == TelegramBroadcast.Status.QUEUED
    assert broadcast.image.name == original_name
    _assert_variants(broadcast)


def test_replacement_image_is_saved_with_update_fields() -> None:
    """Новая загрузка сохраняется вместе с копиями и при явно заданных полях."""
    broadcast = TelegramBroadcast.objects.create(
        text="Фото", image=ContentFile(_image_bytes(), name="before.png")
    )
    replacement = _image_bytes(color="green")
    broadcast.image = ContentFile(replacement, name="after.png")
    broadcast.save(update_fields=["image"])

    broadcast.refresh_from_db()
    with broadcast.image.open("rb") as stored:
        assert stored.read() == replacement
    _assert_variants(broadcast)


def test_model_without_image_can_be_created_and_updated() -> None:
    """Пустое ImageField не мешает обычному сохранению модели."""
    broadcast = TelegramBroadcast.objects.create(text="Без фото")
    broadcast.text = "Изменённый текст"
    broadcast.save(update_fields=["text"])

    broadcast.refresh_from_db()
    assert broadcast.text == "Изменённый текст"
    assert not broadcast.image


def test_variant_failure_does_not_lose_saved_original() -> None:
    """Сбой необязательных копий не теряет модель и исходный файл."""
    original = _image_bytes()
    with patch(
        "apps.core.image_variants.ensure_display_variants",
        side_effect=ValueError("Ошибка создания копии"),
    ):
        broadcast = TelegramBroadcast.objects.create(
            text="Сохранённый оригинал",
            image=ContentFile(original, name="variant-failure.png"),
        )

    broadcast.refresh_from_db()
    assert broadcast.text == "Сохранённый оригинал"
    with broadcast.image.open("rb") as stored:
        assert stored.read() == original


@pytest.mark.parametrize("source_kind", ["bytes", "stream", "django_file"])
def test_compressor_accepts_supported_binary_sources(source_kind: str) -> None:
    """Байты, бинарный поток и Django-файл остаются совместимыми после типизации."""
    original = _image_bytes(image_format="BMP")
    source = (
        original
        if source_kind == "bytes"
        else (
            BytesIO(original)
            if source_kind == "stream"
            else ContentFile(original, name="source.bmp")
        )
    )
    compressed, extension = compress_image_to_max_bytes(source)

    assert extension == "jpg"
    assert len(compressed) <= MAX_IMAGE_SIZE_BYTES
    with Image.open(BytesIO(compressed)) as image:
        assert image.format == "JPEG"
