"""Шаблонные фильтры для единообразного отображения имён."""

from django import template

from apps.users.display import format_user_display_name

register = template.Library()


@register.filter(name="display_name")
def display_name(value: object) -> str:
    """Возвращает отображаемое имя объекта (User, Player, команда с get_display_name).

    Args:
        value: User, Player или объект с методом ``get_display_name``.

    Returns:
        str: Имя в формате «Имя Фамилия».
    """
    if value is None:
        return ""
    getter = getattr(value, "get_display_name", None)
    if callable(getter):
        return str(getter())
    user = getattr(value, "user", None)
    if user is not None:
        return format_user_display_name(user)
    return str(value)
