"""
Пользовательские фильтры для отображения уровней SkillLevel с диапазоном NTRP.
"""

from django import template

from apps.users.skill_levels import skill_with_ntrp

register = template.Library()


@register.filter(name="skill_with_ntrp")
def skill_with_ntrp_filter(code: str) -> str:
    """Возвращает название уровня силы с числовым диапазоном NTRP по коду.

    Args:
        code (str): Код уровня силы (значение из ``SkillLevel``).

    Returns:
        str: Строка вида ``\"Новичок (1.5–2.4)\"`` или исходное название уровня.
    """
    return skill_with_ntrp(code)
