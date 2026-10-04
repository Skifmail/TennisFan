"""Рендер HTML-шаблона в PDF через WeasyPrint."""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.template.loader import render_to_string
from weasyprint import HTML


def render_pdf(template_name: str, context: dict[str, object]) -> bytes:
    """Собрать PDF из Django-шаблона.

    Шрифты и картинки резолвятся относительно каталога ``static/``.

    Args:
        template_name: Путь шаблона от корня templates.
        context: Контекст шаблона.

    Returns:
        bytes: Содержимое PDF.
    """
    html = render_to_string(template_name, context)
    static_dir = Path(settings.BASE_DIR) / "static"
    document = HTML(string=html, base_url=static_dir.as_uri() + "/")
    return bytes(document.write_pdf())
