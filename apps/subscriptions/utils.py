"""Вспомогательные функции для подписок и тарифов."""


def normalize_city_for_pricing(city_str: str) -> str:
    """
    Нормализует город для определения тарифа (Москва vs регионы).
    Возвращает "moscow" для Москвы, иначе — приведённую к нижнему регистру строку.
    """
    city = (city_str or "").lower().strip()
    if city in ("moscow", "moskva", "москва"):
        return "moscow"
    return city or "moscow"
