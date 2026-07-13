"""
Дефолтный HTML разделов правил из шаблонов-фолбэков.

Используется для заполнения пустых RulesSection.body в админке и миграциях,
чтобы редактор видел актуальный текст, а не пустое поле.
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings

# slug раздела → относительный путь к HTML-шаблону с дефолтным текстом
RULES_SECTION_TEMPLATES: dict[str, str] = {
    "tennis_rules": "core/rules_tennis_editable.html",
    "rules_fan": "core/rules_fan.html",
    "rules_round_robin": "core/rules_round_robin.html",
    "rules_olympic": "core/rules_olympic.html",
    "rules_doubles": "core/rules_doubles.html",
    "rules_seeding": "core/rules_seeding.html",
    "rules_tvd": "core/rules_tvd.html",
    "rating_rules": "core/rules_rating.html",
    "rules_rating_algorithm": "core/rules_rating_algorithm.html",
    "site_usage_rules": "core/rules_site_usage.html",
}

# Заголовки для разделов, которые могут отсутствовать в БД
RULES_SECTION_TITLES: dict[str, str] = {
    "tennis_rules": "Правила тенниса",
    "rules_fan": "Одноэтапная сетка",
    "rules_round_robin": "Круговой турнир",
    "rules_olympic": "Олимпийская система (утешительная сетка)",
    "rules_doubles": "Парные турниры",
    "rules_seeding": "Правила посева",
    "rules_tvd": "Турнир выходного дня (ТВД)",
    "rating_rules": "Рейтинг и очки",
    "rules_rating_algorithm": "Алгоритм расчета рейтинга",
    "site_usage_rules": "Правила пользования сайтом",
}


def get_default_rules_body(slug: str) -> str:
    """
    Вернуть HTML-текст правил из шаблона-фолбэка для указанного slug.

    Args:
        slug (str): Код раздела правил (например, ``rules_round_robin``).

    Returns:
        str: Содержимое шаблона без ведущих/замыкающих пробелов.
            Пустая строка, если для slug нет шаблона или файл не найден.
    """
    relative = RULES_SECTION_TEMPLATES.get(slug)
    if not relative:
        return ""
    path = Path(settings.BASE_DIR) / "templates" / relative
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()
