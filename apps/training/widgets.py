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
    Значение — JSON-словарь {type_code_или_произвольное_название: price}.
    """

    MAX_EXTRA_TYPES = 10

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

        attrs = attrs or {}
        container_id = attrs.get("id", f"id_{name}_wrapper")

        rows: list[str] = []
        standard_codes = {code for code, _label in self.choices}

        # Стандартные типы из перечисления TrainingType
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

        # Произвольные типы (те, что не входят в стандартные коды)
        extra_items = [(k, v) for k, v in value.items() if k not in standard_codes]
        extra_count = min(len(extra_items), self.MAX_EXTRA_TYPES)

        for idx in range(extra_count):
            label, price = extra_items[idx]
            checked = "checked"
            safe_idx = f"extra_{idx}"
            row = (
                "<tr>"
                f'<td style="padding: 4px 8px;">'
                f'<input type="checkbox" name="{name}_extra_enabled_{safe_idx}" '
                f'id="id_{name}_extra_enabled_{safe_idx}" {checked}></td>'
                f'<td style="padding: 4px 8px;">'
                f'<input type="text" name="{name}_extra_label_{safe_idx}" '
                f'id="id_{name}_extra_label_{safe_idx}" value="{label}" '
                'style="width: 220px;"></td>'
                f'<td style="padding: 4px 8px;">'
                f'<input type="number" name="{name}_extra_price_{safe_idx}" '
                f'id="id_{name}_extra_price_{safe_idx}" value="{price}" '
                'step="1" min="0" style="width: 120px;"></td>'
                "</tr>"
            )
            rows.append(row)

        html = (
            f'<div id="{container_id}" '
            f'data-next-extra-index="{extra_count}" '
            f'data-max-extra="{self.MAX_EXTRA_TYPES}">'
            '<table style="border-collapse: collapse; width: 100%; max-width: 500px;">'
            "<thead>"
            "<tr>"
            '<th style="text-align: left; padding: 4px 8px; border-bottom: 1px solid #ccc;"></th>'
            '<th style="text-align: left; padding: 4px 8px; border-bottom: 1px solid #ccc;">Тип тренировки</th>'
            '<th style="text-align: left; padding: 4px 8px; border-bottom: 1px solid #ccc;">Цена (₽)</th>'
            "</tr>"
            "</thead>"
            "<tbody>" + "".join(rows) + "</tbody></table>"
            '<button type="button" class="button" '
            'style="margin-top: 8px;" '
            f'data-type-prices-add="{name}">+ Добавить тип</button>'
            "</div>"
            "<script>"
            "(function(){"
            f'var container=document.getElementById("{container_id}");'
            "if(!container){return;}"
            "var btn=container.querySelector('button[data-type-prices-add]');"
            "if(!btn){return;}"
            "var tbody=container.querySelector('tbody');"
            "if(!tbody){return;}"
            "var baseName=btn.getAttribute('data-type-prices-add');"
            "var maxExtra=parseInt(container.getAttribute('data-max-extra')||'10',10);"
            "btn.addEventListener('click',function(){"
            "var idx=parseInt(container.getAttribute('data-next-extra-index')||'0',10);"
            "if(idx>=maxExtra){return;}"
            "var safeIdx='extra_'+idx;"
            "var row=document.createElement('tr');"
            "row.innerHTML="
            "'<td style=\"padding:4px 8px;\">'"
            "+'<input type=\"checkbox\" name=\"'+baseName+'_extra_enabled_'+safeIdx+'\" '"
            "+'id=\"id_'+baseName+'_extra_enabled_'+safeIdx+'\"></td>'"
            "+'<td style=\"padding:4px 8px;\">'"
            "+'<input type=\"text\" name=\"'+baseName+'_extra_label_'+safeIdx+'\" '"
            "+'id=\"id_'+baseName+'_extra_label_'+safeIdx+'\" value=\"\" '"
            '+\'placeholder="Свой тип тренировки" style="width: 220px;"></td>\''
            "+'<td style=\"padding:4px 8px;\">'"
            "+'<input type=\"number\" name=\"'+baseName+'_extra_price_'+safeIdx+'\" '"
            "+'id=\"id_'+baseName+'_extra_price_'+safeIdx+'\" value=\"\" '"
            '+\'step="1" min="0" style="width: 120px;"></td>\''
            "+'</tr>';"
            "tbody.appendChild(row);"
            "container.setAttribute('data-next-extra-index',String(idx+1));"
            "});"
            "})();"
            "</script>"
        )
        return mark_safe(html)

    def value_from_datadict(self, data, files, name):
        """Собираем словарь {type: price} из POST-данных."""
        result = {}
        # Стандартные типы
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

        # Произвольные типы
        for idx in range(self.MAX_EXTRA_TYPES):
            safe_idx = f"extra_{idx}"
            enabled_key = f"{name}_extra_enabled_{safe_idx}"
            label_key = f"{name}_extra_label_{safe_idx}"
            price_key = f"{name}_extra_price_{safe_idx}"

            label = (data.get(label_key) or "").strip()
            price_str = (data.get(price_key) or "").strip()
            enabled = enabled_key in data

            # Пустая строка без чекбокса и без цены — пропускаем
            if not label and not price_str and not enabled:
                continue

            # Без названия не сохраняем
            if not label:
                continue

            if price_str:
                try:
                    result[label] = float(price_str)
                except ValueError:
                    result[label] = None
            else:
                result[label] = None
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
