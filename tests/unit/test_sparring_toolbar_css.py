"""Регрессия вёрстки тулбара спарринга: подписи фильтров не должны обрезаться."""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class SparringToolbarCssTests(SimpleTestCase):
    """Панель фильтров не делит одну узкую строку с кнопками действий."""

    def test_filters_stack_above_actions_with_readable_min_width(self) -> None:
        """Фильтры занимают отдельную строку и не сжимаются ниже ~16rem."""
        css_path = Path(settings.BASE_DIR) / "static" / "css" / "pages" / "sparring.css"
        css = css_path.read_text(encoding="utf-8")
        self.assertIn(":has(.sparring-toolbar__filters)", css)
        self.assertIn("minmax(min(100%, 16rem), 1fr)", css)
        self.assertNotIn("min-width: min(100%, 220px)", css)
