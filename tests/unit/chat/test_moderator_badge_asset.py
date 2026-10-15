"""Проверка сохранности присланного пользователем бейджа модератора."""

from hashlib import sha256
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[3]
BADGE_SHA256: Final = "46e74bb220b2f4289e2a1045afcde36597d221914d56652bae621ae0954cb1e8"


def test_badge_when_static_built_should_preserve_requested_image() -> None:
    """В статику попадает именно присланный бейдж, без перекрашивания."""
    badge = ROOT / "static/images/chat/moderator-badge.png"
    assert sha256(badge.read_bytes()).hexdigest() == BADGE_SHA256
