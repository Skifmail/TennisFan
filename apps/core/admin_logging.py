"""
Детальное логирование изменений в Django admin и форматирование для виджета «Последние действия».
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, cast

from django.contrib.admin.models import ADDITION, CHANGE, LogEntry
from django.forms import Field
from django.utils import timezone


def serialize_admin_value(value: Any, field: Field | None = None) -> str:
    """Сериализовать значение поля формы для отображения в журнале.

    Args:
        value: Значение поля (initial или cleaned).
        field: Поле формы Django (опционально).

    Returns:
        Человекочитаемая строка.
    """
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "Да" if value else "Нет"
    if isinstance(value, datetime):
        dt = timezone.localtime(value) if timezone.is_aware(value) else value
        return str(dt.strftime("%d.%m.%Y %H:%M"))
    if isinstance(value, date):
        return str(value.strftime("%d.%m.%Y"))
    if isinstance(value, Decimal):
        normalized = value.normalize()
        text = format(normalized, "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    if field is not None and hasattr(field, "queryset"):
        if hasattr(value, "pk"):
            return str(value)
        if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
            try:
                return str(field.queryset.get(pk=value))
            except Exception:
                pass
    return str(value)


def build_change_details(form: Any) -> list[dict[str, str]]:
    """Собрать old/new значения изменённых полей формы админки.

    Args:
        form: Экземпляр ModelForm после успешной валидации.

    Returns:
        Список словарей с ключами field, label, old, new.
    """
    details: list[dict[str, str]] = []
    for field_name in form.changed_data:
        if field_name in {"password", "password1", "password2"}:
            details.append(
                {
                    "field": field_name,
                    "label": "Пароль",
                    "old": "••••••",
                    "new": "••••••",
                }
            )
            continue

        field = form.fields.get(field_name)
        label = str(field.label) if field is not None and field.label else field_name
        old_value = form.initial.get(field_name)
        new_value = form.cleaned_data.get(field_name)
        details.append(
            {
                "field": field_name,
                "label": label,
                "old": serialize_admin_value(old_value, field),
                "new": serialize_admin_value(new_value, field),
            }
        )
    return details


def enrich_change_message(
    message: list[dict[str, Any]], form: Any
) -> list[dict[str, Any]]:
    """Добавить old/new значения в JSON change_message Django admin.

    Args:
        message: Стандартное сообщение ``construct_change_message``.
        form: Форма изменённого объекта.

    Returns:
        Обогащённое сообщение для сохранения в LogEntry.
    """
    if not form.changed_data:
        return message

    details = build_change_details(form)
    if not details:
        return message

    for item in message:
        changed = item.get("changed")
        if changed is not None and "name" not in changed:
            changed["details"] = details
            break
    else:
        message.append(
            {
                "changed": {
                    "fields": [detail["label"] for detail in details],
                    "details": details,
                }
            }
        )
    return message


def format_change_message(change_message: str, action_flag: int) -> list[str]:
    """Преобразовать change_message LogEntry в список строк для UI.

    Args:
        change_message: JSON или legacy-текст из LogEntry.
        action_flag: Флаг действия LogEntry (ADDITION/CHANGE/DELETION).

    Returns:
        Список строк с описанием изменений на русском.
    """
    if not change_message:
        if action_flag == ADDITION:
            return ["Создан новый объект"]
        return []

    try:
        payload = json.loads(change_message)
    except json.JSONDecodeError:
        return [change_message]

    if not isinstance(payload, list):
        return [str(payload)]

    lines: list[str] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        if "added" in item:
            added = item["added"]
            if isinstance(added, dict) and added.get("name") and added.get("object"):
                lines.append(
                    f"Добавлено: {added['name']} «{added['object']}»",
                )
            else:
                lines.append("Создан новый объект")
            continue
        if "deleted" in item:
            deleted = item["deleted"]
            if (
                isinstance(deleted, dict)
                and deleted.get("name")
                and deleted.get("object")
            ):
                lines.append(
                    f"Удалено: {deleted['name']} «{deleted['object']}»",
                )
            else:
                lines.append("Объект удалён")
            continue
        if "changed" not in item:
            continue

        changed = item["changed"]
        if not isinstance(changed, dict):
            continue

        prefix = ""
        if changed.get("name") and changed.get("object"):
            prefix = f"{changed['name']} «{changed['object']}»: "

        details = changed.get("details")
        if isinstance(details, list) and details:
            for detail in details:
                if not isinstance(detail, dict):
                    continue
                label = detail.get("label") or detail.get("field") or "Поле"
                old_value = detail.get("old", "—")
                new_value = detail.get("new", "—")
                lines.append(f"{prefix}{label}: {old_value} → {new_value}")
            continue

        fields = changed.get("fields") or []
        if fields:
            lines.append(f"{prefix}Изменены поля: {', '.join(map(str, fields))}")

    if not lines and action_flag == CHANGE:
        return ["Изменения без детализации"]
    return lines


def patch_admin_detailed_logging() -> None:
    """Подключить детальное логирование ко всем ModelAdmin проекта."""
    from django.contrib.admin.options import ModelAdmin
    from django.contrib.admin.utils import construct_change_message as django_construct

    if getattr(ModelAdmin, "_tennison_detailed_logging_patched", False):
        return

    def construct_change_message(
        self: ModelAdmin,
        request: Any,
        form: Any,
        formsets: Any,
        add: bool = False,
    ) -> list[dict[str, Any]]:
        message = cast(list[dict[str, Any]], django_construct(form, formsets, add))
        if add:
            return message
        return enrich_change_message(message, form)

    ModelAdmin.construct_change_message = construct_change_message
    ModelAdmin._tennison_detailed_logging_patched = True


def format_log_entry(entry: LogEntry) -> list[str]:
    """Сформировать строки описания для записи журнала админки.

    Args:
        entry: Запись LogEntry.

    Returns:
        Список строк с деталями действия.
    """
    return format_change_message(entry.change_message, entry.action_flag)
