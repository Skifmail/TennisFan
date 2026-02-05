"""
Олимпийская система с матчами за все места (утешительная сетка).
Основная сетка — как FAN; проигравшие каждого раунда образуют утешительную сетку за диапазон мест.
"""

import logging
import math
from datetime import datetime, timedelta
from typing import List, Optional, Tuple, Union

from django.db import models
from django.urls import reverse
from django.utils import timezone

from apps.users.models import Notification, Player

from .fan import (
    BYE_EMAIL,
    _expected_final_round,
    _get_bye_player,
    _get_or_create_bye_team,
    _round_name,
    _tournament_start_dt,
)
from .models import Match, Tournament, TournamentPlayerResult, TournamentTeam

logger = logging.getLogger(__name__)

OLYMPIC_FORMAT = "olympic_consolation"


def _is_olympic(t: Tournament) -> bool:
    return getattr(t, "format", None) == OLYMPIC_FORMAT


def _olympic_points_for_place(t: Tournament, place: int) -> int:
    """Очки за занятое место (используем те же поля, что и FAN)."""
    if place == 1:
        return t.fan_points_winner
    if place == 2:
        return t.fan_points_final
    if 3 <= place <= 4:
        return t.fan_points_sf
    if 5 <= place <= 8:
        return t.fan_points_r2
    return t.fan_points_r1  # 9+


def _placement_range_for_round(round_index: int, n: int) -> Tuple[int, int]:
    """Для раунда основной сетки: (placement_min, placement_max) для проигравших."""
    k = max(1, math.ceil(math.log2(n)))
    if round_index > k:
        return (1, 2)
    size = 2 ** (k - round_index)  # число проигравших в этом раунде
    placement_max = 2 ** (k - round_index + 1)
    placement_min = placement_max - size + 1
    return (placement_min, placement_max)


def generate_bracket(tournament: Tournament) -> Tuple[bool, str]:
    """
    Сформировать основную сетку (как FAN): 1 круг, посев по рейтингу, BYE при нечётном N.
    Утешительные сетки создаются по мере завершения раундов.
    """
    if not _is_olympic(tournament):
        return False, "Турнир не в формате «Олимпийская система»."
    if tournament.bracket_generated:
        return False, "Сетка уже сформирована."

    from .fan import generate_bracket as fan_generate

    # Временно переключаем формат на FAN, генерируем, возвращаем
    old_format = tournament.format
    tournament.format = "single_elimination"
    tournament._olympic_override = True
    ok, msg = fan_generate(tournament)
    tournament.format = old_format
    delattr(tournament, "_olympic_override")
    if not ok:
        return False, msg
    logger.info("Olympic main bracket created for %s: %s", tournament.name, msg)
    return True, f"Основная сетка сформирована. {msg}"


def _get_losers_of_main_round(tournament: Tournament, round_index: int) -> List[Union[Player, TournamentTeam]]:
    """Список проигравших в раунде основной сетки (без BYE)."""
    is_doubles = tournament.is_doubles()
    matches = tournament.matches.filter(
        round_index=round_index, is_consolation=False
    ).order_by("round_order")
    losers = []
    for m in matches:
        if m.status not in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER):
            return []
        if is_doubles:
            w = m.winner_team
            if not w:
                return []
            los = m.team2 if w == m.team1 else m.team1
            if getattr(los.player1, "is_bye", False):
                continue
            losers.append(los)
        else:
            w = m.winner
            if not w:
                return []
            los = m.player2 if w == m.player1 else m.player1
            if getattr(los, "is_bye", False):
                continue
            losers.append(los)
    return losers


def _create_consolation_match(
    tournament: Tournament,
    round_name: str,
    round_index: int,
    round_order: int,
    place_min: int,
    place_max: int,
    entity_a: Optional[Union[Player, TournamentTeam]],
    entity_b: Optional[Union[Player, TournamentTeam]],
    deadline,
    walkover: bool = False,
) -> Match:
    """Создать один матч утешительной сетки. entity_a/b могут быть None (заполнятся позже)."""
    is_doubles = tournament.is_doubles()
    kw = {}
    if is_doubles and entity_a and entity_b:
        kw = {
            "team1": entity_a,
            "team2": entity_b,
            "player1": entity_a.player1,
            "player2": entity_b.player1,
        }
        if walkover:
            kw["winner_team"] = entity_a
            kw["winner"] = entity_a.player1
    elif is_doubles:
        if entity_a:
            kw["team1"] = entity_a
            kw["player1"] = entity_a.player1
        if entity_b:
            kw["team2"] = entity_b
            kw["player2"] = entity_b.player1
    elif entity_a is not None and entity_b is not None:
        kw = {"player1": entity_a, "player2": entity_b}
        if walkover:
            kw["winner"] = entity_a
    else:
        if entity_a is not None:
            kw["player1"] = entity_a
        if entity_b is not None:
            kw["player2"] = entity_b
    return Match.objects.create(
        tournament=tournament,
        round_name=round_name,
        round_index=round_index,
        round_order=round_order,
        is_consolation=True,
        placement_min=place_min,
        placement_max=place_max,
        status=Match.MatchStatus.WALKOVER if walkover else Match.MatchStatus.SCHEDULED,
        deadline=deadline,
        completed_datetime=timezone.now() if walkover else None,
        **kw,
    )


def create_consolation_bracket_for_round(
    tournament: Tournament, round_index: int, losers: List[Union[Player, TournamentTeam]]
) -> Tuple[bool, str]:
    """
    Создать утешительную сетку для проигравших раунда.
    losers — список из 2 или 4 (степень двойки); place_min, place_max — диапазон мест.
    """
    if not _is_olympic(tournament):
        return False, "Не олимпийский турнир."
    n = len(losers)
    if n == 0:
        return True, "Нет проигравших для утешительной сетки."
    if n == 1:
        # Один проигравший — присваиваем место без матча
        place_min, place_max = _placement_range_for_round(round_index, _count_main_participants(tournament))
        _set_place(tournament, losers[0], place_min, is_doubles=tournament.is_doubles())
        return True, "Место присвоено."
    k = max(1, math.ceil(math.log2(n)))
    if 2**k != n:
        return False, f"Число проигравших должно быть степенью двойки, получено {n}."
    place_min, place_max = _placement_range_for_round(round_index, _count_main_participants(tournament))

    start = _tournament_start_dt(tournament)
    days = getattr(tournament, "match_days_per_round", 7) or 7
    delta = timedelta(days=days)
    base = start + delta * (round_index + 1)
    round_label = f"Места {place_min}–{place_max}"
    existing = tournament.matches.filter(
        is_consolation=True, placement_min=place_min, placement_max=place_max
    ).exists()
    if existing:
        return True, f"Утешительная сетка за места {place_min}–{place_max} уже создана."

    is_doubles = tournament.is_doubles()
    bye_player = _get_bye_player()

    if n == 2:
        m = _create_consolation_match(
            tournament, round_label, round_index, 200 + round_index * 10, place_min, place_max,
            losers[0], losers[1], base + delta,
        )
        return True, f"Создан матч за места {place_min}–{place_max}."

    # n == 4: два полуфинала (L0–L3, L1–L2), финал за place_min/place_min+1, матч за place_min+2/place_max
    # Сначала создаём пустые матчи 203 и 204 (заполнятся победителями/проигравшими полуфиналов)
    m203 = _create_consolation_match(
        tournament, f"{round_label} (финал)", round_index, 203, place_min, place_min + 1,
        None, None, base + delta,
    )
    m204 = _create_consolation_match(
        tournament, f"{round_label} (за {place_min+2}-е и {place_max}-е)", round_index, 204,
        place_min + 2, place_max, None, None, base + delta,
    )
    m1 = _create_consolation_match(
        tournament, f"{round_label} (1/2)", round_index, 201, place_min, place_max,
        losers[0], losers[3], base + delta,
    )
    m2 = _create_consolation_match(
        tournament, f"{round_label} (1/2)", round_index, 202, place_min, place_max,
        losers[1], losers[2], base + delta,
    )
    m1.next_match = m203
    m2.next_match = m203
    m1.loser_next_match = m204
    m2.loser_next_match = m204
    m1.save(update_fields=["next_match", "loser_next_match"])
    m2.save(update_fields=["next_match", "loser_next_match"])
    return True, f"Создана утешительная сетка за места {place_min}–{place_max} (4 матча)."


def _count_main_participants(tournament: Tournament) -> int:
    if tournament.is_doubles():
        return tournament.teams.filter(player2__isnull=False).count()
    return tournament.participants.count()


def _set_place(
    tournament: Tournament,
    entity: Union[Player, TournamentTeam],
    place: int,
    is_doubles: bool = False,
) -> None:
    points = _olympic_points_for_place(tournament, place)
    if is_doubles:
        for p in (entity.player1, entity.player2):
            if p and not getattr(p, "is_bye", False):
                TournamentPlayerResult.objects.update_or_create(
                    tournament=tournament,
                    player=p,
                    defaults={"place": place, "fan_points": points, "is_consolation": True},
                )
    else:
        if entity and not getattr(entity, "is_bye", False):
            TournamentPlayerResult.objects.update_or_create(
                tournament=tournament,
                player=entity,
                defaults={"place": place, "fan_points": points, "is_consolation": True},
            )


def ensure_consolation_created_for_round(tournament: Tournament, round_index: int) -> None:
    """После завершения матча основной сетки: если весь раунд сыгран — создать утешительную сетку."""
    if not _is_olympic(tournament):
        return
    losers = _get_losers_of_main_round(tournament, round_index)
    if not losers:
        return
    ok, msg = create_consolation_bracket_for_round(tournament, round_index, losers)
    if ok and "Создан" in msg or "Создана" in msg:
        logger.info("Olympic consolation for round %s created: %s", round_index, msg)


def advance_winner_olympic(match: Match) -> Optional[Match]:
    """
    Олимпийская система: продвижение победителя и проигравшего (в утешительную),
    присвоение мест при завершении матчей за два места.
    """
    t = match.tournament
    if not _is_olympic(t):
        return None
    is_doubles = t.is_doubles() and match.team1_id and match.team2_id
    winner_team = getattr(match, "winner_team", None)
    winner = match.winner
    if not winner and not winner_team:
        return None

    if is_doubles:
        loser_entity = match.team2 if winner_team == match.team1 else match.team1
        winner_entity = match.team1 if winner_team == match.team1 else match.team2
    else:
        loser_entity = match.player2 if winner == match.player1 else match.player1
        winner_entity = match.player1 if winner == match.player1 else match.player2

    ri, ro = match.round_index, match.round_order
    is_cons = match.is_consolation
    place_min = match.placement_min
    place_max = match.placement_max

    if is_cons:
        # Утешительная сетка: матч за два места (place_max - place_min == 1) — присвоить оба места
        if place_min is not None and place_max is not None and place_max - place_min == 1:
            _set_place(t, winner_entity, place_min, is_doubles=t.is_doubles())
            _set_place(t, loser_entity, place_max, is_doubles=t.is_doubles())
        # Победитель идёт в next_match, проигравший — в loser_next_match (если есть)
        next_winner = match.next_match
        next_loser = match.loser_next_match
        if next_winner:
            _fill_match_side(next_winner, winner_entity, is_doubles)
        if next_loser:
            _fill_match_side(next_loser, loser_entity, is_doubles)
        _try_finalize_olympic(t)
        return None

    # Основная сетка: продвижение победителя — делегируем в fan (включая логику
    # заглушек bye: игрок с bye не должен получать bye в следующем раунде).
    t.format = "single_elimination"
    try:
        from .fan import advance_winner_and_award_loser
        next_m = advance_winner_and_award_loser(match)
    finally:
        t.format = OLYMPIC_FORMAT
    ensure_consolation_created_for_round(t, ri)
    _try_finalize_olympic(t)
    return next_m


def _fill_match_side(
    m: Match, entity: Union[Player, TournamentTeam], is_doubles: bool
) -> None:
    """Заполнить свободную сторону матча (player1/team1 или player2/team2)."""
    if is_doubles:
        if m.team1_id is None:
            m.team1 = entity
            m.player1 = entity.player1
        else:
            m.team2 = entity
            m.player2 = entity.player1
    else:
        if m.player1_id is None:
            m.player1 = entity
        else:
            m.player2 = entity
    m.save(
        update_fields=["team1", "team2", "player1", "player2"]
        if is_doubles
        else ["player1", "player2"]
    )


def _try_finalize_olympic(tournament: Tournament) -> None:
    """Проверить, все ли места определены и финал сыгран — завершить турнир."""
    if not _is_olympic(tournament) or tournament.status == "completed":
        return
    expected_final_ri = _expected_final_round(tournament)
    final = tournament.matches.filter(
        is_consolation=False, round_index=expected_final_ri
    ).first()
    if not final or final.status not in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER) or not final.winner:
        return
    # Финал основной сетки сыгран — присвоить 1 и 2 места
    is_doubles = tournament.is_doubles() and final.team1_id and final.team2_id
    if is_doubles:
        winner_entity = final.winner_team
        loser_entity = final.team2 if winner_entity == final.team1 else final.team1
    else:
        winner_entity = final.winner
        loser_entity = final.player2 if final.winner == final.player1 else final.player1
    if getattr(winner_entity, "player1", winner_entity) and getattr(getattr(winner_entity, "player1", winner_entity), "is_bye", False):
        return
    _set_place(tournament, winner_entity, 1, is_doubles=tournament.is_doubles())
    _set_place(tournament, loser_entity, 2, is_doubles=tournament.is_doubles())

    # Проверяем, все ли утешительные матчи сыграны (все места присвоены)
    n = _count_main_participants(tournament)
    results_with_place = tournament.fan_results.filter(place__isnull=False)
    if results_with_place.count() < n:
        return
    finalize_olympic(tournament)


def finalize_olympic(tournament: Tournament) -> Tuple[bool, str]:
    """Завершить турнир: начислить очки по местам, обновить рейтинг."""
    if not _is_olympic(tournament):
        return False, "Не олимпийский турнир."
    if tournament.status == "completed":
        return False, "Турнир уже завершён."

    for r in tournament.fan_results.select_related("player").all():
        if getattr(r.player, "is_bye", False):
            continue
        if r.place is not None and r.fan_points:
            r.player.total_points += r.fan_points
            r.player.save(update_fields=["total_points"])

    tournament.status = "completed"
    tournament.save(update_fields=["status"])
    logger.info("Olympic tournament %s completed, ratings updated.", tournament.name)
    return True, "Турнир завершён, рейтинг обновлён."


def process_overdue_match(match: Match) -> Tuple[bool, str]:
    """Обработать просроченный матч олимпийской системы: тех. победа, продвижение, утешительные, финализация."""
    if not _is_olympic(match.tournament):
        return False, "Не олимпийский турнир."
    from .fan import _overdue_winner, apply_overdue_walkover

    if match.status in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER):
        return False, "Матч уже завершён."
    if not match.deadline or match.deadline > timezone.now():
        return False, "Дедлайн не истёк."
    if match.player1 and match.player2 and getattr(match.player1, "is_bye", False) and getattr(match.player2, "is_bye", False):
        return False, "Служебный матч."

    if match.is_consolation:
        if match.team1_id and match.team2_id:
            a, b = match.team1, match.team2
            bye_team = getattr(a.player1, "is_bye", False) or getattr(b.player1, "is_bye", False)
        else:
            a, b = match.player1, match.player2
            bye_team = (getattr(a, "is_bye", False) if a else False) or (getattr(b, "is_bye", False) if b else False)
        if bye_team:
            return False, "В утешительном матче участвует BYE."
        if match.team1_id and match.team2_id:
            t1_pts = match.team1.player1.total_points + (match.team1.player2.total_points if match.team1.player2_id else 0)
            t2_pts = match.team2.player1.total_points + (match.team2.player2.total_points if match.team2.player2_id else 0)
            winner_entity = match.team1 if t1_pts >= t2_pts else match.team2
            winner = winner_entity.player1
        else:
            winner = _overdue_winner(match)
            winner_entity = winner
        apply_overdue_walkover(match, winner)
        if match.team1_id and match.team2_id:
            match.winner_team = winner_entity
            match.save(update_fields=["winner_team"])
        advance_winner_olympic(match)
        return True, f"Матч {match.pk}: тех. победа (олимпийская система)."
    else:
        winner = _overdue_winner(match)
        apply_overdue_walkover(match, winner)
        advance_winner_olympic(match)
        return True, f"Матч {match.pk}: тех. победа (олимпийская система)."