"""
Бизнес-логика оценки соперников: сохранение, пересчёт агрегатов, байесовская формула.
"""

import logging
from datetime import timedelta
from typing import Any, cast

from django.db import transaction
from django.utils import timezone

from apps.tournaments.models import Match
from apps.tournaments.utils import get_match_participants
from apps.users.models import Player

from .constants import (
    BAYESIAN_M_PARAM,
    MIN_VOTES_TO_DISPLAY,
    RATING_EDIT_WINDOW_HOURS,
    RATING_MAX,
    RATING_MIN,
)
from .enums import SkillMetric
from .models import PlayerSkillAggregate, PlayerSkillRating

logger = logging.getLogger(__name__)


# --- Валидация и права --------------------------------------------------------


def can_rate_match(match: Match, player: Player) -> tuple[bool, str]:
    """
    Оценивать можно только: матч завершён, пользователь участвовал, не оценивал ранее.
    Возвращает (ok, reason).
    """
    if match.status not in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER):
        return False, "Матч не завершён"
    participants = get_match_participants(match)
    if player not in participants:
        return False, "Вы не участвовали в матче"
    if PlayerSkillRating.objects.filter(match=match, from_player=player).exists():
        return False, "Вы уже оценили соперника в этом матче"
    return True, ""


def get_opponent_for_rating(match: Match, from_player: Player) -> Player | None:
    """
    Соперник, которого оценивает from_player.
    Одиночный: второй игрок. Парный: один из соперников (первый в списке противоположной стороны).
    """
    participants = get_match_participants(match)
    if from_player not in participants:
        return None
    # Одиночный
    if match.player1_id and match.player2_id and not (match.team1_id or match.team2_id):
        opponent_single: Player | None
        if match.player1_id == from_player.id:
            opponent_single = match.player2
        else:
            opponent_single = match.player1
        return opponent_single
    # Парный: противоположная команда
    if match.team1_id and match.team2_id:
        t1_players = {match.team1.player1_id, match.team1.player2_id} - {None}
        t2_players = {match.team2.player1_id, match.team2.player2_id} - {None}
        if from_player.id in t1_players:
            opp = match.team2.player1 or match.team2.player2
            return cast(Player | None, opp)
        if from_player.id in t2_players:
            opp = match.team1.player1 or match.team1.player2
            return cast(Player | None, opp)
    # Спарринг 2×2
    if match.partner1_id and match.partner2_id:
        side1 = {match.player1_id, match.partner1_id} - {None}
        side2 = {match.player2_id, match.partner2_id} - {None}
        if from_player.id in side1:
            opp = match.player2 or match.partner2
            return cast(Player | None, opp)
        if from_player.id in side2:
            opp = match.player1 or match.partner1
            return cast(Player | None, opp)
    return None


def can_edit_rating(rating: PlayerSkillRating) -> bool:
    """Редактирование доступно только 24 часа."""
    if not rating.updated_at:
        return False
    deadline = rating.created_at + timedelta(hours=RATING_EDIT_WINDOW_HOURS)
    return bool(timezone.now() <= deadline)


def validate_metric_values(payload: dict[str, Any]) -> dict[str, int | None]:
    """
    Из payload оставить только ключи из SkillMetric, значения 1–10 или None.
    Невалидные ключи/значения игнорируются.
    """
    result: dict[str, int | None] = {}
    for name in SkillMetric.all_metric_names():
        val = payload.get(name)
        if val is None or val == "":
            result[name] = None
            continue
        try:
            n = int(val)
            if RATING_MIN <= n <= RATING_MAX:
                result[name] = n
            else:
                result[name] = None
        except (TypeError, ValueError):
            result[name] = None
    return result


# --- Сохранение и пересчёт ----------------------------------------------------


def _system_average_raw(metric_name: str) -> float:
    """Среднее по системе по одной метрике (C в формуле)."""
    from django.db.models import Avg, Count

    if metric_name not in SkillMetric.all_metric_names():
        return 5.0
    sub = PlayerSkillRating.objects.exclude(**{metric_name: None}).aggregate(
        avg=Avg(metric_name),
        cnt=Count("id"),
    )
    if not sub["cnt"]:
        return 5.0
    return float(sub["avg"])


def _weighted_average(r: float, v: int, c: float) -> float:
    """weighted = (v/(v+m))*R + (m/(v+m))*C."""
    if v <= 0:
        return c
    m = BAYESIAN_M_PARAM
    w1 = v / (v + m)
    w2 = m / (v + m)
    return round(w1 * r + w2 * c, 4)


@transaction.atomic
def submit_rating(
    match_id: int,
    from_player: Player,
    payload: dict[str, int | None],
) -> tuple[bool, str, PlayerSkillRating | None]:
    """
    Сохранить или обновить оценку. Проверяет права, валидирует, пересчитывает агрегаты.
    Возвращает (success, message, rating_or_none).
    """
    try:
        match = Match.objects.get(pk=match_id)
    except Match.DoesNotExist:
        return False, "Матч не найден", None

    ok, reason = can_rate_match(match, from_player)
    if not ok:
        # Проверка на редактирование существующей оценки
        existing = PlayerSkillRating.objects.filter(
            match=match, from_player=from_player
        ).first()
        if existing and can_edit_rating(existing):
            rating = existing
        else:
            return False, reason, None
    else:
        opponent = get_opponent_for_rating(match, from_player)
        if not opponent:
            return False, "Не удалось определить соперника", None
        rating, _ = PlayerSkillRating.objects.get_or_create(
            match=match,
            from_player=from_player,
            defaults={"to_player": opponent},
        )
        if rating.to_player_id != opponent.id:
            rating.to_player = opponent
            rating.save(update_fields=["to_player"])

    values = validate_metric_values(payload)
    changed_metrics = []
    for name, val in values.items():
        if rating.get_metric_value(name) != val:
            rating.set_metric_value(name, val)
            changed_metrics.append(name)
    rating.save(update_fields=SkillMetric.all_metric_names() + ["updated_at"])

    if changed_metrics:
        recalc_aggregates_for_player(rating.to_player, changed_metrics)

    return True, "Оценка сохранена", rating


def recalc_aggregates_for_player(player: Player, metric_names: list[str] | None = None):
    """
    Пересчитать PlayerSkillAggregate для player по указанным метрикам
    (или по всем, если metric_names is None).
    Вызывать внутри transaction.atomic.
    """
    metrics = metric_names or SkillMetric.all_metric_names()
    for metric_name in metrics:
        if metric_name not in SkillMetric.all_metric_names():
            continue
        _recalc_one_aggregate(player, metric_name)


def _recalc_one_aggregate(player: Player, metric_name: str) -> None:
    """Один агрегат: сырые оценки по to_player и метрике, затем байесовское среднее."""
    from django.db.models import Avg, Count

    field_name = metric_name
    qs = (
        PlayerSkillRating.objects.filter(to_player=player)
        .exclude(**{field_name: None})
        .aggregate(avg=Avg(field_name), cnt=Count("id"))
    )
    votes_count = qs["cnt"] or 0
    average_raw = float(qs["avg"] or 0.0)
    c = _system_average_raw(metric_name)
    average_weighted = _weighted_average(average_raw, votes_count, c)

    PlayerSkillAggregate.objects.update_or_create(
        player=player,
        metric_name=metric_name,
        defaults={
            "average_raw": average_raw,
            "average_weighted": average_weighted,
            "votes_count": votes_count,
        },
    )


# --- Чтение для API -----------------------------------------------------------


def get_player_skills(
    player: Player,
    request_user: Any,
    *,
    include_lowest_three: bool = False,
) -> dict[str, Any]:
    """
    Агрегированные навыки игрока для API.
    Для чужого профиля — только публичные (все 12 метрик, звёзды, кол-во оценок).
    Для своего — дополнительно 3 самых низких weighted (рекомендовано улучшить).
    """
    is_owner = bool(
        request_user.is_authenticated
        and getattr(request_user, "player", None)
        and request_user.player.pk == player.pk
    )

    aggregates = {
        a.metric_name: {
            "average_raw": round(a.average_raw, 2),
            "average_weighted": round(a.average_weighted, 2),
            "votes_count": a.votes_count,
            "display_value": (
                round(a.average_weighted, 1)
                if a.votes_count >= MIN_VOTES_TO_DISPLAY
                else None
            ),
            "insufficient_data": a.votes_count < MIN_VOTES_TO_DISPLAY,
        }
        for a in PlayerSkillAggregate.objects.filter(player=player)
    }

    # Все 12 метрик в едином порядке
    result: dict[str, Any] = {
        "metrics": [],
        "recommend_to_improve": [],
    }
    for name in SkillMetric.all_metric_names():
        data = aggregates.get(name)
        if not data:
            data = {
                "average_raw": None,
                "average_weighted": None,
                "votes_count": 0,
                "display_value": None,
                "insufficient_data": True,
            }
        # Звёзды: 1–10 -> 1–10 (округление для отображения)
        display_val = data.get("display_value")
        stars_filled = (
            min(10, max(0, round(display_val or 0)))
            if display_val is not None and not data.get("insufficient_data")
            else 0
        )
        result["metrics"].append(
            {
                "name": name,
                "label": dict(SkillMetric.choices).get(name, name),
                "stars_filled": stars_filled,
                **data,
            }
        )

    if is_owner and include_lowest_three:
        with_votes: list[tuple[str, dict[str, Any]]] = []
        for name in SkillMetric.all_metric_names():
            agg = aggregates.get(name)
            if agg and agg.get("votes_count", 0) >= MIN_VOTES_TO_DISPLAY:
                with_votes.append((name, agg))

        with_votes.sort(key=lambda x: (x[1]["average_weighted"] or 999))
        result["recommend_to_improve"] = [
            {
                "name": name,
                "label": dict(SkillMetric.choices).get(name, name),
                "average_weighted": data["average_weighted"],
                "votes_count": data["votes_count"],
            }
            for name, data in with_votes[:3]
        ]

    return result


def get_match_rating_status(match: Match, player: Player) -> dict[str, Any]:
    """Статус оценки матча для текущего игрока: можно ли оценить, есть ли уже оценка, ссылка."""
    from django.urls import reverse

    can, reason = can_rate_match(match, player)
    existing = PlayerSkillRating.objects.filter(match=match, from_player=player).first()
    can_edit = existing and can_edit_rating(existing) if existing else False
    rate_url = reverse("player_ratings:rate_match", kwargs={"match_id": match.pk})
    return {
        "can_rate": can or can_edit,
        "already_rated": existing is not None,
        "can_edit": can_edit,
        "reason": reason,
        "rate_url": rate_url,
    }
