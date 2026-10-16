"""Контракт компактного бейджа администратора городского чата."""

from pathlib import Path
from typing import Final

from PIL import Image

ROOT: Final = Path(__file__).resolve().parents[3]
MAX_BADGE_BYTES: Final = 4096
MAX_BADGE_SIDE: Final = 64


def test_administrator_badge_when_built_should_be_small_and_transparent() -> None:
    """Для маленькой иконки не загружается мегабайтный исходник."""
    badge = ROOT / "static/images/chat/administrator-badge.webp"
    assert badge.stat().st_size <= MAX_BADGE_BYTES
    with Image.open(badge) as image:
        assert image.format == "WEBP"
        assert max(image.size) <= MAX_BADGE_SIDE
        assert image.mode == "RGBA"
        assert image.getchannel("A").getextrema() == (0, 255)
