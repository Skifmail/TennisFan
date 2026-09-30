"""Рейтинг и категория игрока в разрезе вида спорта.

Теннис по-прежнему пишется в поля ``Player``: так не ломается текущий UI.
Падел читается и пишется только в ``PlayerSportProfile``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import cast

from django.db.models import FloatField, OuterRef, QuerySet, Subquery, Value
from django.db.models.functions import Coalesce

from apps.core.sports import Sport, SportArg, sport_code
from apps.users.models import Player, PlayerSportProfile
from apps.users.rating_utils import (
    get_starting_points,
    rating_to_ntrp_level,
    rating_to_skill_level,
)

PADEL_START_LEVEL = Decimal("1.5")


def ensure_sport_profile(player: Player, sport: SportArg) -> PlayerSportProfile:
    """Вернуть профиль вида спорта, создав его при отсутствии.

    Для тенниса начальные числа копируются с игрока.
    Для падела без онбординга старт — сила 1.5 / 1500 FAN.

    Args:
        player: Игрок.
        sport: Код вида спорта.

    Returns:
        PlayerSportProfile: Профиль этого вида спорта.
    """
    code = sport_code(sport)
    if code not in Sport.values:
        code = sport_code(Sport.TENNIS)
    if code == Sport.TENNIS:
        defaults = {
            "total_points": float(player.total_points or 0),
            "hidden_rating": float(player.hidden_rating or 0),
            "ntrp_level": player.ntrp_level,
            "skill_level": player.skill_level,
            "matches_played": player.matches_played,
            "matches_won": player.matches_won,
        }
    else:
        starting = float(get_starting_points(PADEL_START_LEVEL))
        defaults = {
            "total_points": starting,
            "hidden_rating": starting,
            "ntrp_level": PADEL_START_LEVEL,
            "skill_level": rating_to_skill_level(starting),
            "matches_played": 0,
            "matches_won": 0,
        }
    profile, _created = PlayerSportProfile.objects.get_or_create(
        player=player,
        sport=code,
        defaults=defaults,
    )
    return cast(PlayerSportProfile, profile)


def get_sport_profile(player: Player, sport: SportArg) -> PlayerSportProfile | None:
    """Найти профиль, не создавая его.

    Args:
        player: Игрок.
        sport: Код вида спорта.

    Returns:
        PlayerSportProfile | None: Профиль или None.
    """
    return cast(
        PlayerSportProfile | None,
        PlayerSportProfile.objects.filter(
            player=player, sport=sport_code(sport)
        ).first(),
    )


def rating_points(player: Player | None, sport: SportArg) -> float:
    """Текущий FAN игрока в виде спорта.

    Args:
        player: Игрок или None.
        sport: Код вида спорта.

    Returns:
        float: Очки. Для тенниса без профиля — поле игрока.
    """
    if player is None or getattr(player, "is_bye", False):
        return 0.0
    sport = sport_code(sport)
    if sport != Sport.PADEL:
        return float(player.total_points or 0)
    profile = get_sport_profile(player, Sport.PADEL)
    if profile is None:
        return float(get_starting_points(PADEL_START_LEVEL))
    return float(profile.total_points or 0)


def hidden_rating_points(player: Player, sport: SportArg) -> float:
    """Скрытый рейтинг вида спорта.

    Args:
        player: Игрок.
        sport: Код вида спорта.

    Returns:
        float: Скрытый рейтинг.
    """
    sport = sport_code(sport)
    if sport != Sport.PADEL:
        return float(player.hidden_rating or 0)
    profile = get_sport_profile(player, Sport.PADEL)
    if profile is None:
        return float(get_starting_points(PADEL_START_LEVEL))
    return float(profile.hidden_rating or 0)


def matches_played_for(player: Player, sport: SportArg) -> int:
    """Число матчей игрока в виде спорта.

    Args:
        player: Игрок.
        sport: Код вида спорта.

    Returns:
        int: Сыгранные матчи.
    """
    sport = sport_code(sport)
    if sport != Sport.PADEL:
        return int(player.matches_played or 0)
    profile = get_sport_profile(player, Sport.PADEL)
    return int(profile.matches_played or 0) if profile else 0


def skill_level_for(player: Player, sport: SportArg) -> str | None:
    """Категория силы для допуска в турнир.

    Args:
        player: Игрок.
        sport: Код вида спорта турнира.

    Returns:
        str | None: Код категории. None, если падел ещё не задан.
    """
    sport = sport_code(sport)
    if sport != Sport.PADEL:
        return str(player.skill_level)
    profile = get_sport_profile(player, Sport.PADEL)
    if profile is None:
        return None
    return str(profile.skill_level)


def commit_rating(player: Player, new_rating: float, sport: SportArg) -> None:
    """Записать новый FAN и уровень силы.

    Теннис обновляет игрока, сигнал синхронизирует теннисный профиль.
    Падел обновляет только профиль падела.

    Args:
        player: Игрок.
        new_rating: Новые очки, не ниже нуля.
        sport: Код вида спорта матча.
    """
    if player is None or getattr(player, "is_bye", False):
        return
    sport = sport_code(sport)
    rating = max(0.0, float(new_rating))
    skill = rating_to_skill_level(rating)
    level = rating_to_ntrp_level(rating)
    if sport != Sport.PADEL:
        player.hidden_rating = rating
        player.total_points = rating
        player.skill_level = skill
        player.ntrp_level = level
        player.save(
            update_fields=["hidden_rating", "total_points", "skill_level", "ntrp_level"]
        )
        return
    profile = ensure_sport_profile(player, Sport.PADEL)
    profile.hidden_rating = rating
    profile.total_points = rating
    profile.skill_level = skill
    profile.ntrp_level = level
    profile.save(
        update_fields=[
            "hidden_rating",
            "total_points",
            "skill_level",
            "ntrp_level",
            "updated_at",
        ]
    )


def record_match_played(player: Player | None, sport: SportArg, *, won: bool) -> None:
    """Увеличить счётчик матчей вида спорта.

    Args:
        player: Игрок.
        sport: Код вида спорта.
        won: Была ли это победа.
    """
    if player is None or getattr(player, "is_bye", False):
        return
    sport = sport_code(sport)
    if sport != Sport.PADEL:
        player.matches_played += 1
        if won:
            player.matches_won += 1
        player.save(update_fields=["matches_played", "matches_won"])
        return
    profile = ensure_sport_profile(player, Sport.PADEL)
    profile.matches_played += 1
    if won:
        profile.matches_won += 1
    profile.save(update_fields=["matches_played", "matches_won", "updated_at"])


def adjust_wins(player: Player | None, sport: SportArg, delta: int) -> None:
    """Сдвинуть число побед при пересчёте результата.

    Args:
        player: Игрок.
        sport: Код вида спорта.
        delta: +1 или -1.
    """
    if player is None or getattr(player, "is_bye", False) or not delta:
        return
    sport = sport_code(sport)
    if sport != Sport.PADEL:
        player.matches_won = max(0, int(player.matches_won) + delta)
        player.save(update_fields=["matches_won"])
        return
    profile = get_sport_profile(player, Sport.PADEL)
    if profile is None:
        return
    profile.matches_won = max(0, int(profile.matches_won) + delta)
    profile.save(update_fields=["matches_won", "updated_at"])


def set_padel_strength(player: Player, level: Decimal) -> PlayerSportProfile:
    """Задать стартовую силу падела и синхронный FAN.

    Args:
        player: Игрок.
        level: Сила в диапазоне 1.5–7.0.

    Returns:
        PlayerSportProfile: Обновлённый профиль падела.

    Raises:
        ValueError: Если уровень вне диапазона. Её бросает ``get_starting_points``.
    """
    points = float(get_starting_points(level))
    profile = ensure_sport_profile(player, Sport.PADEL)
    profile.total_points = points
    profile.hidden_rating = points
    profile.ntrp_level = rating_to_ntrp_level(points)
    profile.skill_level = rating_to_skill_level(points)
    profile.save(
        update_fields=[
            "total_points",
            "hidden_rating",
            "ntrp_level",
            "skill_level",
            "updated_at",
        ]
    )
    return profile


def apply_profile_to_player(player: Player, profile: PlayerSportProfile) -> None:
    """Подменить отображаемые поля игрока числами профиля, не сохраняя.

    Args:
        player: Экземпляр для шаблона.
        profile: Профиль вида спорта.
    """
    player.total_points = profile.total_points
    player.hidden_rating = profile.hidden_rating
    player.ntrp_level = profile.ntrp_level
    player.skill_level = profile.skill_level
    player.matches_played = profile.matches_played
    player.matches_won = profile.matches_won


def order_by_sport_rating(queryset: QuerySet, sport: SportArg) -> QuerySet:
    """Отсортировать игроков по FAN выбранного вида спорта.

    Для тенниса порядок прежний: ``-total_points``.

    Args:
        queryset: QuerySet игроков.
        sport: Код вида спорта.

    Returns:
        QuerySet: Отсортированный queryset.
    """
    sport = sport_code(sport)
    if sport != Sport.PADEL:
        return queryset.order_by("-total_points")
    padel_points = PlayerSportProfile.objects.filter(
        player=OuterRef("pk"),
        sport=Sport.PADEL,
    ).values("total_points")[:1]
    return queryset.annotate(
        sport_points=Coalesce(
            Subquery(padel_points, output_field=FloatField()),
            Value(0.0),
        )
    ).order_by("-sport_points", "pk")


def sync_tennis_profile(player: Player) -> None:
    """Скопировать теннисные поля игрока в профиль тенниса.

    Args:
        player: Игрок после сохранения.
    """
    if getattr(player, "is_bye", False):
        return
    PlayerSportProfile.objects.update_or_create(
        player=player,
        sport=Sport.TENNIS,
        defaults={
            "total_points": float(player.total_points or 0),
            "hidden_rating": float(player.hidden_rating or 0),
            "ntrp_level": player.ntrp_level,
            "skill_level": player.skill_level,
            "matches_played": player.matches_played,
            "matches_won": player.matches_won,
        },
    )
