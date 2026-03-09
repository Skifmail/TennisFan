"""
Пользовательские фильтры для отображения уровней SkillLevel с диапазоном NTRP.
"""

from django import template

from apps.users.skill_levels import skill_with_ntrp

register = template.Library()


def _get_years_suffix(value: int) -> str:
    remainder_100 = value % 100
    remainder_10 = value % 10
    if 11 <= remainder_100 <= 14:
        return "лет"
    if remainder_10 == 1:
        return "год"
    if 2 <= remainder_10 <= 4:
        return "года"
    return "лет"


@register.filter(name="skill_with_ntrp")
def skill_with_ntrp_filter(code: str) -> str:
    """Возвращает название уровня силы с числовым диапазоном NTRP по коду.

    Args:
        code (str): Код уровня силы (значение из ``SkillLevel``).

    Returns:
        str: Строка вида ``\"Новичок (1.5–2.4)\"`` или исходное название уровня.
    """
    return skill_with_ntrp(code)


@register.filter(name="years_suffix")
def years_suffix(value: int | str) -> str:
    """Возвращает правильное склонение слова для количества лет.

    Args:
        value (int | str): Число лет, для которого нужно подобрать форму слова.

    Returns:
        str: Одна из форм ``"год"``, ``"года"`` или ``"лет"``.

    Raises:
        ValueError: Если значение нельзя преобразовать в целое число.
    """
    numeric_value = abs(int(value))
    return _get_years_suffix(numeric_value)
