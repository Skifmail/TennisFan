"""Аватар в карточке спарринга остаётся кругом на узком экране."""

from pathlib import Path

from django.test import SimpleTestCase

SPARRING_CSS = (
    Path(__file__).resolve().parents[2] / "static" / "css" / "pages" / "sparring.css"
)


class SparringAvatarCssTests(SimpleTestCase):
    """Мобильное правило img не растягивает аватар в овал."""

    def test_card_avatar_is_forced_square(self) -> None:
        """Ширина и высота аватара карточки зафиксированы и не уступают max-width: 100%."""
        css = SPARRING_CSS.read_text(encoding="utf-8")

        self.assertIn("img.player-avatar.sparring-card__avatar", css)
        self.assertIn("width: 48px;", css)
        self.assertIn("height: 48px;", css)
        self.assertIn("max-width: 48px;", css)
        self.assertIn("max-height: 48px;", css)
        self.assertIn("aspect-ratio: 1 / 1;", css)
        self.assertIn("border-radius: 50%;", css)
        self.assertIn("grid-template-columns: 48px minmax(0, 1fr);", css)
        self.assertNotIn("grid-template-columns: 40px minmax(0, 1fr);", css)
