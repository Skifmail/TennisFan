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


def _russian_plural_form_index(value: int) -> int:
    """Возвращает индекс формы слова для русского склонения по числу.

    Используется схема из трёх форм: 1, 2–4, 5+.

    Args:
        value (int): Число, для которого подбирается форма.

    Returns:
        int: Индекс формы (0, 1 или 2).
    """
    numeric_value = abs(value)
    remainder_100 = numeric_value % 100
    remainder_10 = numeric_value % 10
    if 11 <= remainder_100 <= 14:
        return 2
    if remainder_10 == 1:
        return 0
    if 2 <= remainder_10 <= 4:
        return 1
    return 2


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


@register.filter(name="ru_pluralize")
def ru_pluralize(value: int | str, forms: str) -> str:
    """Возвращает правильную форму слова для русского языка.

    Фильтр принимает три формы через запятую:
    ``{{ count|ru_pluralize:"игрок,игрока,игроков" }}``.

    Args:
        value (int | str): Число для выбора формы.
        forms (str): Строка из трёх форм слова через запятую.

    Returns:
        str: Одна из трёх форм слова.

    Raises:
        ValueError: Если передано меньше трёх форм.
    """
    parts = [part.strip() for part in forms.split(",") if part.strip()]
    if len(parts) != 3:
        msg = (
            "Фильтр ru_pluralize ожидает три формы слова, "
            'например: "игрок,игрока,игроков".'
        )
        raise ValueError(msg)

    index = _russian_plural_form_index(int(value))
    return parts[index]
