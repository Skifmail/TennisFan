"""Template tags для виджета «Последние действия» в админке."""

from __future__ import annotations

from django import template
from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import AbstractBaseUser

from apps.core.admin_logging import format_log_entry

register = template.Library()


@register.filter
def admin_log_detail_lines(entry: LogEntry) -> list[str]:
    """Вернуть человекочитаемые строки изменений для LogEntry.

    Args:
        entry: Запись журнала Django admin.

    Returns:
        Список строк с описанием изменений.
    """
    return format_log_entry(entry)


@register.simple_tag
def admin_log_count_for_user(user: AbstractBaseUser) -> int:
    """Количество записей журнала для пользователя.

    Args:
        user: Текущий пользователь админки.

    Returns:
        Число записей LogEntry пользователя.
    """
    if not user.is_authenticated:
        return 0
    return int(LogEntry.objects.filter(user_id=user.pk).count())


@register.simple_tag
def admin_log_total_count() -> int:
    """Общее количество записей журнала действий в базе.

    Returns:
        Число всех записей LogEntry.
    """
    return int(LogEntry.objects.count())
