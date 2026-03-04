"""
Утилиты и константы для работы с уровнями SkillLevel.
"""

from apps.users.models import SkillLevel

# Соответствие уровней NTRP (строковые ключи совпадают с SkillLevel.value)
SKILL_LEVEL_NTRP: dict[str, str] = {
    "novice": "1.5–2.5",
    "amateur": "2.6–3.5",
    "experienced": "3.6–4.5",
    "advanced": "4.6–5.5",
    "professional": "5.6–7.0",
}


def skill_with_ntrp(code: str) -> str:
    """Возвращает человекочитаемое название уровня с числовым диапазоном NTRP.

    Args:
        code (str): Код уровня силы из перечисления SkillLevel (например, ``\"novice\"``).

    Returns:
        str: Строка вида ``\"Новичок (1.5–2.5)\"`` либо просто название уровня, если диапазон не найден.
    """
    mapping = dict(SkillLevel.choices)
    label = mapping.get(code, code)
    ntrp = SKILL_LEVEL_NTRP.get(code, "")
    return f"{label} ({ntrp})" if ntrp else str(label)
