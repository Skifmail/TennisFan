"""Юнит-тесты детального логирования Django admin."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock

from django.contrib.admin.models import ADDITION, CHANGE, DELETION
from django.test import SimpleTestCase
from django.utils import timezone
from zoneinfo import ZoneInfo

from apps.core.admin_logging import (
    build_change_details,
    enrich_change_message,
    format_change_message,
    serialize_admin_value,
)


class SerializeAdminValueTestCase(SimpleTestCase):
    """Сериализация значений полей для журнала."""

    def test_bool_and_empty(self) -> None:
        self.assertEqual(serialize_admin_value(None), "—")
        self.assertEqual(serialize_admin_value(True), "Да")
        self.assertEqual(serialize_admin_value(False), "Нет")

    def test_datetime_is_localized(self) -> None:
        value = datetime(2026, 6, 5, 18, 30, tzinfo=ZoneInfo("UTC"))
        with timezone.override(ZoneInfo("Europe/Moscow")):
            rendered = serialize_admin_value(value)
        self.assertIn("2026", rendered)
        self.assertIn(":", rendered)


class FormatChangeMessageTestCase(SimpleTestCase):
    """Форматирование change_message для виджета."""

    def test_legacy_fields_only(self) -> None:
        payload = json.dumps([{"changed": {"fields": ["Город", "Рейтинг"]}}])
        lines = format_change_message(payload, CHANGE)
        self.assertEqual(lines, ["Изменены поля: Город, Рейтинг"])

    def test_detailed_old_new_values(self) -> None:
        payload = json.dumps(
            [
                {
                    "changed": {
                        "fields": ["Дедлайн"],
                        "details": [
                            {
                                "field": "deadline",
                                "label": "Дедлайн",
                                "old": "05.06.2026 00:00",
                                "new": "10.06.2026 00:00",
                            }
                        ],
                    }
                }
            ]
        )
        lines = format_change_message(payload, CHANGE)
        self.assertEqual(
            lines,
            ["Дедлайн: 05.06.2026 00:00 → 10.06.2026 00:00"],
        )

    def test_addition_without_details(self) -> None:
        self.assertEqual(format_change_message("", ADDITION), ["Создан новый объект"])

    def test_deletion_inline(self) -> None:
        payload = json.dumps(
            [{"deleted": {"name": "Матч", "object": "Игрок 1 vs Игрок 2"}}]
        )
        lines = format_change_message(payload, DELETION)
        self.assertEqual(lines, ["Удалено: Матч «Игрок 1 vs Игрок 2»"])


class BuildChangeDetailsTestCase(SimpleTestCase):
    """Сбор old/new из формы админки."""

    def test_builds_details_from_changed_data(self) -> None:
        form = MagicMock()
        form.changed_data = ["city", "total_points"]
        form.initial = {"city": "Москва", "total_points": Decimal("2800.0")}
        form.cleaned_data = {"city": "Сочи", "total_points": Decimal("2950.5")}
        form.fields = {
            "city": MagicMock(label="Город"),
            "total_points": MagicMock(label="Рейтинг"),
        }

        details = build_change_details(form)

        self.assertEqual(len(details), 2)
        self.assertEqual(details[0]["label"], "Город")
        self.assertEqual(details[0]["old"], "Москва")
        self.assertEqual(details[0]["new"], "Сочи")
        self.assertEqual(details[1]["label"], "Рейтинг")

    def test_enrich_change_message_adds_details(self) -> None:
        form = MagicMock()
        form.changed_data = ["city"]
        form.initial = {"city": "Москва"}
        form.cleaned_data = {"city": "Сочи"}
        form.fields = {"city": MagicMock(label="Город")}

        message = enrich_change_message([{"changed": {"fields": ["Город"]}}], form)

        self.assertIn("details", message[0]["changed"])
        self.assertEqual(message[0]["changed"]["details"][0]["new"], "Сочи")
