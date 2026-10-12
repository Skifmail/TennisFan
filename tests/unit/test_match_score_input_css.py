"""Сетка ввода счёта не должна схлопывать столбцы сетов на узком экране."""

from pathlib import Path

from django.test import SimpleTestCase

FORMS_CSS = (
    Path(__file__).resolve().parents[2] / "static" / "css" / "components" / "forms.css"
)


class MatchScoreInputCssTests(SimpleTestCase):
    """Столбцы сетов имеют фиксированную ширину, а не потолок без минимума."""

    def test_set_columns_do_not_collapse(self) -> None:
        """Дорожка сета не задана как minmax(0, …) рядом с 1fr."""
        css = FORMS_CSS.read_text(encoding="utf-8")

        self.assertIn("repeat(3, 3.5rem)", css)
        self.assertNotIn("minmax(0, 80px)", css)
        self.assertIn("appearance: textfield", css)
