"""Справочник игроков платформы для страницы приглашения на спарринг.

Игроки группируются по населённому пункту: сначала город зрителя,
затем остальные города по алфавиту.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass, field

from apps.users.models import Player

PLAYERS_PER_CITY = 24
UNKNOWN_CITY_LABEL = "Город не указан"


@dataclass
class PlayerCityGroup:
    """Игроки одного населённого пункта."""

    city: str
    is_local: bool
    players: list[Player] = field(default_factory=list)
    total: int = 0

    @property
    def hidden_count(self) -> int:
        """Сколько игроков города не поместилось в список."""
        return max(self.total - len(self.players), 0)


def _city_tokens(city: str) -> set[str]:
    """Нормализованные части населённого пункта, разделённые запятой.

    Args:
        city: Строка города из профиля игрока.

    Returns:
        set[str]: Части города в нижнем регистре без лишних пробелов.
    """
    return {part.strip().casefold() for part in (city or "").split(",") if part.strip()}


def _city_label(city: str) -> str:
    """Подпись города для заголовка списка.

    Args:
        city: Строка города из профиля игрока.

    Returns:
        str: Город без лишних пробелов или подпись для пустого значения.
    """
    cleaned = " ".join((city or "").split())
    return cleaned or UNKNOWN_CITY_LABEL


def sparring_player_directory(
    viewer: Player,
    *,
    exclude_ids: Iterable[int] = (),
    limit_per_city: int = PLAYERS_PER_CITY,
) -> tuple[PlayerCityGroup | None, list[PlayerCityGroup]]:
    """Собрать игроков платформы по городам для страницы приглашения.

    Args:
        viewer: Игрок, открывший страницу; исключается из списка.
        exclude_ids: Дополнительные игроки, которых не показывать.
        limit_per_city: Сколько игроков города показывать без поиска.

    Returns:
        tuple: группа «Рядом с вами» (или None) и остальные города по алфавиту.
    """
    excluded = {viewer.pk, *exclude_ids}
    players = (
        Player.objects.filter(is_bye=False, is_verified=True, user__is_active=True)
        .exclude(pk__in=excluded)
        .select_related("user")
        .order_by("-total_points", "user__last_name", "user__first_name")
    )

    grouped: OrderedDict[str, PlayerCityGroup] = OrderedDict()
    for player in players:
        label = _city_label(player.city)
        key = label.casefold()
        group = grouped.get(key)
        if group is None:
            group = PlayerCityGroup(city=label, is_local=False)
            grouped[key] = group
        group.players.append(player)
        group.total += 1

    viewer_tokens = _city_tokens(viewer.city)
    local_groups = [
        group
        for group in grouped.values()
        if viewer_tokens and _city_tokens(group.city) & viewer_tokens
    ]
    local_keys = {id(group) for group in local_groups}
    others = [group for group in grouped.values() if id(group) not in local_keys]

    local: PlayerCityGroup | None = None
    if local_groups:
        local = PlayerCityGroup(city=_city_label(viewer.city), is_local=True)
        for group in local_groups:
            local.players.extend(group.players)
            local.total += group.total
        local.players.sort(
            key=lambda player: (
                -player.total_points,
                (player.user.last_name or "").casefold(),
                (player.user.first_name or "").casefold(),
            )
        )
        local.players = local.players[:limit_per_city]

    for group in others:
        group.players = group.players[:limit_per_city]

    others.sort(
        key=lambda group: (
            group.city == UNKNOWN_CITY_LABEL,
            group.city.casefold(),
        )
    )
    return local, others
