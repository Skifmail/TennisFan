"""Оформление текстов согласий из управляемого реестра юридических версий."""

from html import escape

import markdown  # type: ignore[import-untyped]
from django import template
from django.utils.safestring import SafeString, mark_safe

register = template.Library()


@register.filter
def legal_markdown(value: str | None) -> SafeString:
    """Оформляет Markdown согласия, экранируя исходные HTML-теги."""
    return mark_safe(
        markdown.markdown(escape(value or "", quote=False), extensions=["extra"])
    )
