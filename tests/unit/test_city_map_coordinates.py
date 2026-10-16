"""Проверки координат одноимённых населённых пунктов."""

from typing import Final

import pytest

from apps.core.city_map_coordinates import (
    AmbiguousLocalityError,
    LocalityCoordinates,
    resolve_locality,
)

RAMENSKOE: Final = (55.5495132, 38.2728914)
VILLAGE: Final = (56.5, 34.5)


def test_resolve_when_city_and_village_share_name_should_select_city() -> None:
    """Название без префикса относится к городу, не деревне."""
    records = [
        LocalityCoordinates("Раменское", "Московская область", "city", RAMENSKOE),
        LocalityCoordinates("Раменское", "Тверская область", "derevnya", VILLAGE),
    ]
    assert resolve_locality("Раменское", records, []).coordinates == RAMENSKOE


def test_resolve_when_legacy_coordinates_wrong_should_use_unique_csv_city() -> None:
    """Проверенный справочник исправляет старую запись без региона."""
    legacy = [LocalityCoordinates("Раменское", "", "city", VILLAGE)]
    reference = [
        LocalityCoordinates("Раменское", "Московская область", "city", RAMENSKOE)
    ]
    assert resolve_locality("Раменское", legacy, reference).coordinates == RAMENSKOE


def test_resolve_when_region_and_type_given_should_keep_village() -> None:
    """Явно выбранная деревня не превращается в одноимённый город."""
    records = [
        LocalityCoordinates("Раменское", "Московская область", "city", RAMENSKOE),
        LocalityCoordinates("Раменское", "Тверская область", "derevnya", VILLAGE),
    ]
    selected = resolve_locality("деревня Раменское, Тверская область", records, [])
    assert selected.coordinates == VILLAGE


def test_resolve_when_same_city_in_two_regions_should_raise_ambiguity() -> None:
    """Без региона нельзя произвольно выбрать один из двух городов."""
    records = [
        LocalityCoordinates("Мирный", "Архангельская область", "city", RAMENSKOE),
        LocalityCoordinates("Мирный", "Республика Саха", "city", VILLAGE),
    ]
    with pytest.raises(AmbiguousLocalityError):
        resolve_locality("Мирный", records, [])
    assert (
        resolve_locality("Мирный, Республика Саха", records, []).coordinates == VILLAGE
    )


@pytest.mark.parametrize("name", ["", "  ", "Несуществующий город"])
def test_resolve_when_unknown_should_return_empty_coordinates(name: str) -> None:
    """Пустые и неизвестные пункты не получают случайную метку."""
    assert resolve_locality(name, [], []).coordinates is None


@pytest.mark.parametrize("name", ["г. Раменское", "раменское", "Раменское, Россия"])
def test_resolve_when_city_alias_should_use_reference(name: str) -> None:
    """Префикс, регистр и страна не мешают найти город."""
    reference = [
        LocalityCoordinates("Раменское", "Московская область", "city", RAMENSKOE)
    ]
    assert resolve_locality(name, [], reference).coordinates == RAMENSKOE


def test_resolve_when_unknown_city_should_preserve_display_case() -> None:
    """Отсутствие справочника не меняет написание названия в профиле."""
    assert resolve_locality("г. Новый город", [], []).name == "Новый город"


def test_resolve_when_region_does_not_match_should_not_use_other_region() -> None:
    """Явный регион запрещает подстановку одноимённого города из другого."""
    records = [LocalityCoordinates("Мирный", "Область А", "city", RAMENSKOE)]
    assert resolve_locality("Мирный, Область Б", records, []).coordinates is None


@pytest.mark.parametrize("name", ["Городец", "Селогоры", "Хуторской"])
def test_resolve_when_name_starts_with_type_word_should_preserve_city(
    name: str,
) -> None:
    """Начало названия не считается отдельным префиксом типа поселения."""
    records = [LocalityCoordinates(name, "Область А", "city", RAMENSKOE)]
    assert resolve_locality(name, records, []).coordinates == RAMENSKOE
