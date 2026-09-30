"""Уменьшенные копии изображений и gzip HTML."""

import io
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.http import HttpResponse
from django.middleware.gzip import GZipMiddleware
from django.test import RequestFactory, SimpleTestCase, override_settings
from PIL import Image

from apps.core.image_variants import (
    ensure_display_variants,
    name_from_media_url,
    render_variant,
    variant_key,
    variant_url,
)
from apps.core.templatetags.media_tags import media_variant


class GzipHtmlTests(SimpleTestCase):
    """Динамический HTML сжимается, если клиент просит gzip."""

    def test_middleware_is_after_whitenoise(self) -> None:
        middleware = settings.MIDDLEWARE
        self.assertLess(
            middleware.index("whitenoise.middleware.WhiteNoiseMiddleware"),
            middleware.index("django.middleware.gzip.GZipMiddleware"),
        )

    def test_long_html_is_gzipped(self) -> None:
        middleware = GZipMiddleware(
            lambda request: HttpResponse("x" * 800, content_type="text/html")
        )
        request = RequestFactory().get("/", HTTP_ACCEPT_ENCODING="gzip")
        response = middleware(request)
        self.assertEqual(response["Content-Encoding"], "gzip")
        self.assertLess(len(response.content), 800)


class ImageVariantTests(SimpleTestCase):
    """Копия карточки меньше исходника и не шире заданной стороны."""

    def test_card_variant_shrinks_large_jpeg(self) -> None:
        image = Image.new("RGB", (1800, 1200), (30, 60, 90))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=95)
        original = buffer.getvalue()
        card = render_variant(original, "card")
        avatar = render_variant(original, "avatar")
        card_image = Image.open(io.BytesIO(card))
        avatar_image = Image.open(io.BytesIO(avatar))
        self.assertLess(len(card), len(original))
        self.assertLessEqual(max(card_image.size), 640)
        self.assertLessEqual(max(avatar_image.size), 160)

    def test_variant_key_is_stable(self) -> None:
        self.assertEqual(
            variant_key("tournaments/example.jpg", "card"),
            variant_key("tournaments/example.jpg", "card"),
        )
        self.assertTrue(
            variant_key("tournaments/example.jpg", "card").startswith("variants/card/")
        )

    @override_settings(MEDIA_URL="https://cdn.example/bucket/")
    def test_media_url_maps_to_variant(self) -> None:
        source = "https://cdn.example/bucket/avatars/player.jpg"
        self.assertEqual(name_from_media_url(source), "avatars/player.jpg")
        result = media_variant(source, "avatar")
        self.assertIn("variants/avatar/", result)
        self.assertTrue(result.endswith(".jpg"))

    def test_unknown_url_stays_unchanged(self) -> None:
        self.assertEqual(
            variant_url("https://example.com/logo.png", "card"),
            "https://example.com/logo.png",
        )

    def test_storage_write_creates_both_kinds(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="img-variants-"))
        storage = FileSystemStorage(location=str(root))
        image = Image.new("RGB", (900, 700), (10, 20, 30))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=90)
        storage.save("photos/big.jpg", ContentFile(buffer.getvalue()))
        written = ensure_display_variants("photos/big.jpg", storage=storage)
        self.assertEqual(
            written,
            [
                variant_key("photos/big.jpg", "avatar"),
                variant_key("photos/big.jpg", "card"),
            ],
        )
        for key in written:
            self.assertTrue(storage.exists(key))
