"""Отложенное заполнение профиля и однократный стартовый уровень тенниса."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from django.db import transaction

if TYPE_CHECKING:
    from .models import Player

STRENGTH_REQUIRED = "Сначала укажите уровень силы в профиле. Без него нельзя участвовать в турнирах и спаррингах."


class StrengthAlreadySet(ValueError):
    """Начальный уровень уже задан и не может быть изменён пользователем."""


def tennis_strength_set(player: Player | None) -> bool:
    """Учесть флаг однократного выбора и историю матчей существующего игрока."""
    return bool(player and (player.tennis_entry_level_set or player.matches_played > 0))


@transaction.atomic
def set_initial_strength(player_id: int, level: Decimal) -> None:
    """Однократно зафиксировать уровень и стартовые очки под блокировкой строки."""
    from .models import Player
    from .rating_utils import (
        get_starting_points,
        map_ntrp_to_skill_level,
        ntrp_category_band,
    )

    if not level.is_finite() or not Decimal("1.50") <= level <= Decimal("7.00"):
        raise ValueError("Уровень силы должен быть от 1.50 до 7.00.")
    player = Player.objects.select_for_update().get(pk=player_id)
    if tennis_strength_set(player):
        raise StrengthAlreadySet(
            "Уровень силы уже указан. Далее рейтинг меняется по результатам матчей."
        )
    level = level.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    points = float(get_starting_points(level))
    player.ntrp_level = level
    player.skill_level = map_ntrp_to_skill_level(ntrp_category_band(level))
    player.total_points = points
    player.hidden_rating = points
    player.tennis_entry_level_set = True
    player.save(
        update_fields=[
            "ntrp_level",
            "skill_level",
            "total_points",
            "hidden_rating",
            "tennis_entry_level_set",
        ]
    )


def ensure_tennis_participants(
    players: tuple[Player, ...], sport: str = "tennis"
) -> None:
    """Проверить уровень каждого участника теннисного матча, в том числе приглашённого партнёра."""
    if sport == "tennis" and any(not tennis_strength_set(player) for player in players):
        raise ValueError(STRENGTH_REQUIRED)
