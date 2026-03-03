"""
Кастомные виджеты для приложения training.
"""

from django import forms
from django.utils.safestring import mark_safe

from .models import SKILL_LEVEL_NTRP, TrainingType


class TypePricesWidget(forms.Widget):
    """
    Виджет для выбора типов тренировки с ценами.
    Отображает таблицу: чекбокс | название типа | поле цены.
    Значение — JSON-словарь {type_code: price}.
    """

    def __init__(self, attrs=None):
        super().__init__(attrs)
        self.choices = TrainingType.choices

    def render(self, name, value, attrs=None, renderer=None):
        """Рендер таблицы чекбокс + цена без отдельного шаблона."""
        if isinstance(value, str):
            import json

            try:
                value = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                value = {}
        value = value or {}

        rows: list[str] = []
        for code, label in self.choices:
            checked = "checked" if code in value else ""
            price = value.get(code, "")
            row = (
                "<tr>"
                f'<td style="padding: 4px 8px;">'
                f'<input type="checkbox" name="{name}_checked_{code}" '
                f'id="id_{name}_checked_{code}" {checked}></td>'
                f'<td style="padding: 4px 8px;">'
                f'<label for="id_{name}_checked_{code}">{label}</label></td>'
                f'<td style="padding: 4px 8px;">'
                f'<input type="number" name="{name}_price_{code}" '
                f'id="id_{name}_price_{code}" value="{price}" '
                'step="1" min="0" style="width: 120px;"></td>'
                "</tr>"
            )
            rows.append(row)

        html = (
            '<table style="border-collapse: collapse; width: 100%; max-width: 500px;">'
            "<thead>"
            "<tr>"
            '<th style="text-align: left; padding: 4px 8px; border-bottom: 1px solid #ccc;"></th>'
            '<th style="text-align: left; padding: 4px 8px; border-bottom: 1px solid #ccc;">Тип тренировки</th>'
            '<th style="text-align: left; padding: 4px 8px; border-bottom: 1px solid #ccc;">Цена (₽)</th>'
            "</tr>"
            "</thead>"
            "<tbody>" + "".join(rows) + "</tbody></table>"
        )
        return mark_safe(html)

    def value_from_datadict(self, data, files, name):
        """Собираем словарь {type: price} из POST-данных."""
        result = {}
        for code, _label in self.choices:
            checked_key = f"{name}_checked_{code}"
            price_key = f"{name}_price_{code}"
            if checked_key in data:
                price_str = data.get(price_key, "").strip()
                if price_str:
                    try:
                        result[code] = float(price_str)
                    except ValueError:
                        result[code] = None
                else:
                    result[code] = None
        return result

    def format_value(self, value):
        return value


class MultiCheckboxWidget(forms.CheckboxSelectMultiple):
    """Чекбоксы с подписями NTRP-диапазонов для уровней."""

    def __init__(self, choices=None, show_ntrp: bool = False, attrs=None):
        self.show_ntrp = show_ntrp
        super().__init__(attrs=attrs, choices=choices or [])

    def create_option(
        self, name, value, label, selected, index, subindex=None, attrs=None
    ):
        option = super().create_option(
            name, value, label, selected, index, subindex, attrs
        )
        if self.show_ntrp:
            ntrp = SKILL_LEVEL_NTRP.get(value, "")
            if ntrp:
                option["label"] = f"{label} ({ntrp})"
        return option
