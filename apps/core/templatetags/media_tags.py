"""Фильтр URL уменьшенной копии изображения."""

from django import template

from apps.core.image_variants import variant_url

register = template.Library()


@register.filter(name="media_variant")
def media_variant(value: object, kind: str = "card") -> str:
    """Возвращает URL уменьшенной копии.

    Args:
        value: FieldFile или публичный URL медиа.
        kind: ``avatar`` или ``card``.

    Returns:
        str: Адрес копии. Если это не медиа проекта, возвращается исходная строка.
    """
    return variant_url(value, str(kind or "card"))
