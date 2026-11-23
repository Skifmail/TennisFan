"""Адресное выделение тренера в публичных каталогах без изменения его профиля."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Final

from apps.core.geo import normalize_geo_text

if TYPE_CHECKING:
    from apps.training.models import Coach, Training

# Существующий URL профиля: написание слага отличается от фамилии тренера.
HIGHEST_CATEGORY_COACH_SLUG: Final = "leonid-ermoraev"
PRIORITY_CITIES: Final = frozenset({"раменское", "жуковский"})


def is_highest_category_coach(slug: str) -> bool:
    """Проверить адресно выбранный профиль, не выделяя однофамильцев."""
    return slug == HIGHEST_CATEGORY_COACH_SLUG


def prioritize_catalog_trainings(
    trainings: Iterable[Training], city: str
) -> list[Training]:
    """Поднять тренировки Леонида только в Раменском и Жуковском.

    Стабильная сортировка сохраняет порядок остальных предложений и секции
    близости. Предложение без выбранного города в географии не поднимается.
    """
    from apps.training.geo import training_city_tokens

    entries = list(trainings)
    city_key = normalize_geo_text(city)
    if city_key not in PRIORITY_CITIES:
        return entries

    def is_priority(training: Training) -> bool:
        """Проверить тренера и наличие выбранного города у предложения."""
        return bool(
            training.coach
            and is_highest_category_coach(training.coach.slug)
            and city_key in training_city_tokens(training.city)
        )

    return sorted(entries, key=lambda training: not is_priority(training))


def prioritize_catalog_coaches(coaches: Iterable[Coach], city: str) -> list[Coach]:
    """Поставить Леонида первым в общем списке и двух целевых городах."""
    entries = list(coaches)
    if city and normalize_geo_text(city) not in PRIORITY_CITIES:
        return entries
    return sorted(entries, key=lambda coach: not is_highest_category_coach(coach.slug))
