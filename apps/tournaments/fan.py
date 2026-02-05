"""
Система FAN: генерация сетки, продвижение победителей, начисление очков.
"""

import logging
import math
from datetime import datetime, timedelta
from typing import Optional

from django.db import models
from django.urls import reverse
from django.utils import timezone

from apps.users.models import Notification, Player

from .models import Match, Tournament, TournamentPlayerResult, TournamentTeam, TournamentStatus

logger = logging.getLogger(__name__)

FAN_FORMAT = "single_elimination"
BYE_EMAIL = "bye@tennisfan.local"


def _is_fan(t: Tournament) -> bool:
    return getattr(t, "format", None) == FAN_FORMAT


def _get_bye_player() -> Optional[Player]:
    """Служебный игрок «Свободный круг» для матчей при нечётном числе участников."""
    return Player.objects.filter(user__email=BYE_EMAIL, is_bye=True).select_related("user").first()


def _get_or_create_bye_team(tournament: Tournament, bye_player: Player) -> TournamentTeam:
    """Команда «Свободный круг» для парного турнира при нечётном числе команд."""
    team, _ = TournamentTeam.objects.get_or_create(
        tournament=tournament,
        player1=bye_player,
        player2=bye_player,
        defaults={},
    )
    return team


def _round_name(round_index: int) -> str:
    names = {1: "1 круг", 2: "2 круг", 3: "Полуфинал", 4: "Финал"}
    return names.get(round_index, f"Раунд {round_index}")


def _fan_points_for_round(t: Tournament, round_index: int) -> int:
    m = {
        1: t.fan_points_r1,
        2: t.fan_points_r2,
        3: t.fan_points_sf,
        4: t.fan_points_final,
    }
    return m.get(round_index, 0)


def _round_eliminated(round_index: int) -> str:
    m = {1: TournamentPlayerResult.RoundEliminated.R1, 2: TournamentPlayerResult.RoundEliminated.R2,
         3: TournamentPlayerResult.RoundEliminated.SF, 4: TournamentPlayerResult.RoundEliminated.FINAL}
    return m.get(round_index, TournamentPlayerResult.RoundEliminated.R1)


def _tournament_start_dt(tournament: Tournament):
    """Дата/время старта турнира для дедлайнов."""
    start = timezone.now()
    if tournament.start_date:
        d = tournament.start_date
        if isinstance(d, str):
            d = datetime.strptime(d, "%Y-%m-%d").date()
        start = timezone.make_aware(datetime.combine(d, datetime.min.time()))
    return start


def check_and_generate_past_deadline_brackets() -> int:
    """
    Найти турниры (FAN и круговые) с истёкшим дедлайном регистрации и сформировать сетку.
    Вызывать при загрузке страниц турниров (или по cron).
    Возвращает количество сформированных сеток.
    """
    from django.core.cache import cache

    from .round_robin import generate_bracket as generate_round_robin_bracket

    cache_key = "tournament_generate_brackets_last_run"
    if cache.get(cache_key):
        return 0
    cache.set(cache_key, True, 60)  # не чаще раза в минуту

    from .olympic_consolation import _is_olympic, generate_bracket as generate_olympic_bracket

    now = timezone.now()
    qs = list(
        Tournament.objects.filter(
            format__in=["single_elimination", "olympic_consolation", "round_robin"],
            bracket_generated=False,
            registration_deadline__lte=now,
            registration_deadline__isnull=False,
        ).exclude(status=TournamentStatus.CANCELLED)
    )
    total = 0
    for t in qs:
        # Проверка минимального количества участников/команд
        min_required = t.min_teams if t.is_doubles() else t.min_participants
        if min_required is not None:
            count = t.full_teams_count() if t.is_doubles() else t.participants.count()
            if count < min_required:
                notified_at = t.insufficient_participants_notified_at
                if notified_at is None:
                    from apps.core.telegram_notify import notify_tournament_insufficient_participants
                    notify_tournament_insufficient_participants(t)
                    t.insufficient_participants_notified_at = now
                    t.save(update_fields=["insufficient_participants_notified_at"])
                    logger.info(
                        "Insufficient participants for %s: %s/%s, notified admin",
                        t.slug, count, min_required,
                    )
                elif (notified_at + timedelta(hours=3)) <= now:
                    from .cancel import cancel_tournament
                    cancel_tournament(t)
                    logger.info(
                        "Cancelled tournament %s: still insufficient after 3h (%s/%s)",
                        t.slug, count, min_required,
                    )
                continue

        if t.format == "single_elimination":
            ok, msg = generate_bracket(t)
        elif _is_olympic(t):
            ok, msg = generate_olympic_bracket(t)
        else:
            ok, msg = generate_round_robin_bracket(t)
        if ok:
            total += 1
            logger.info("Auto-generated bracket for %s: %s", t.slug, msg)
    return total


def generate_bracket(tournament: Tournament) -> tuple[bool, str]:
    """
    Сформировать сетку FAN: сортировка по рейтингу, пары 1–N, 2–(N-1), …;
    при нечётном N — у первого по рейтингу «свободный круг» (bye).
    Турнир проводится при любом числе участников от 2 до max (включительно).
    Участники фиксируются, регистрация закрывается.
    Поддерживает одиночные и парные турниры.
    """
    if not _is_fan(tournament):
        return False, "Турнир не в формате FAN."
    if tournament.bracket_generated:
        return False, "Сетка уже сформирована."

    from .models import TournamentTeam
    from .solo_teams import remove_solo_teams_from_doubles_tournament

    if tournament.is_doubles():
        removed = remove_solo_teams_from_doubles_tournament(tournament)
        if removed:
            logger.info("Removed %d solo teams from FAN doubles tournament %s", removed, tournament.slug)

    if tournament.is_doubles():
        entities = list(
            tournament.teams.filter(player2__isnull=False)
            .select_related("player1__user", "player2__user")
            .order_by("-player1__total_points")
        )
        max_n = tournament.max_teams
        entity_name = "команд"
    else:
        entities = list(tournament.participants.order_by("-total_points"))
        max_n = tournament.max_participants
        entity_name = "участников"

    n = len(entities)
    if n < 2:
        return False, f"Нужно минимум 2 {entity_name[:-1]} для формирования сетки."
    if max_n is not None and n > max_n:
        return False, f"Зарегистрировано {n}, максимум {max_n}."

    start = _tournament_start_dt(tournament)
    days = getattr(tournament, "match_days_per_round", 7) or 7
    delta = timedelta(days=days)
    bye_player = _get_bye_player()
    odd = n % 2 == 1
    if odd and not bye_player:
        return False, "Не найден служебный игрок «Свободный круг» (bye). Выполните миграции users."

    num_real = n // 2
    created = 0
    round_order = 1
    is_doubles = tournament.is_doubles()

    def _create_match(a, b, walkover=False):
        nonlocal created, round_order
        if is_doubles:
            team_a, team_b = a, b
            p1, p2 = team_a.player1, team_b.player1
            kw = {"team1": team_a, "team2": team_b, "player1": p1, "player2": p2}
            if walkover:
                kw["winner"] = p1
                kw["winner_team"] = team_a
        else:
            p1, p2 = a, b
            kw = {"player1": p1, "player2": p2}
            if walkover:
                kw["winner"] = p1
        Match.objects.create(
            tournament=tournament,
            round_name="1 круг",
            round_index=1,
            round_order=round_order,
            is_consolation=False,
            status=Match.MatchStatus.WALKOVER if walkover else Match.MatchStatus.SCHEDULED,
            deadline=start + delta,
            completed_datetime=timezone.now() if walkover else None,
            **kw,
        )
        created += 1
        round_order += 1

    if odd:
        _create_match(entities[0], bye_player if not is_doubles else _get_or_create_bye_team(tournament, bye_player), walkover=True)

    for i in range(num_real):
        lo, hi = (i + 1, n - 1 - i) if odd else (i, n - 1 - i)
        a, b = entities[lo], entities[hi]
        if not is_doubles and (getattr(b, "is_bye", False) or b == bye_player):
            continue
        _create_match(a, b)

    tournament.bracket_generated = True
    tournament.save(update_fields=["bracket_generated"])
    logger.info("FAN bracket R1 created for %s: %d matches (n=%d, odd=%s)", tournament.name, created, n, odd)
    return True, f"Сетка сформирована: {created} матчей 1-го круга, {entity_name} {n}."


def create_consolation_matches(tournament: Tournament) -> tuple[bool, str]:
    """
    После завершения всех матчей R1: создать матчи подвала для проигравших.
    Пары: L1–L8, L2–L7, L3–L6, L4–L5 (по месту в R1).
    """
    if not _is_fan(tournament):
        return False, "Не FAN."
    if tournament.matches.filter(is_consolation=True).exists():
        return True, "Подвал уже создан."
    r1 = tournament.matches.filter(round_index=1, is_consolation=False)
    if r1.count() == 0:
        return False, "Нет матчей 1-го круга."
    unfinished = [m for m in r1 if m.status not in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER)]
    if unfinished:
        return False, "Не все матчи 1-го круга завершены."

    is_doubles = tournament.is_doubles()
    losers = []
    for m in r1.order_by("round_order"):
        if is_doubles:
            w = m.winner_team
            if not w:
                return False, "Не у всех матчей R1 есть победитель."
            los = m.team2 if w == m.team1 else m.team1
            if getattr(los.player1, "is_bye", False):
                continue
        else:
            w = m.winner
            if not w:
                return False, "Не у всех матчей R1 есть победитель."
            los = m.player2 if w == m.player1 else m.player1
            if getattr(los, "is_bye", False):
                continue
        losers.append(los)
    n = len(losers)
    if n < 2:
        return True, "Подвал не создаётся: меньше двух проигравших в R1."

    start = _tournament_start_dt(tournament)
    days = getattr(tournament, "match_days_per_round", 7) or 7
    delta = timedelta(days=days)
    base = start + delta
    bye_player = _get_bye_player()
    half = n // 2
    created = 0

    for i in range(half):
        a, b = losers[i], losers[n - 1 - i]
        if is_doubles:
            Match.objects.create(
                tournament=tournament,
                round_name="Подвал, 1 круг",
                round_index=1,
                round_order=100 + i,
                is_consolation=True,
                team1=a,
                team2=b,
                player1=a.player1,
                player2=b.player1,
                status=Match.MatchStatus.SCHEDULED,
                deadline=base + delta,
            )
        else:
            Match.objects.create(
                tournament=tournament,
                round_name="Подвал, 1 круг",
                round_index=1,
                round_order=100 + i,
                is_consolation=True,
                player1=a,
                player2=b,
                status=Match.MatchStatus.SCHEDULED,
                deadline=base + delta,
            )
        created += 1

    if n % 2 == 1 and bye_player:
        odd_loser = losers[half]
        if is_doubles:
            bye_entity = _get_or_create_bye_team(tournament, bye_player)
            Match.objects.create(
                tournament=tournament,
                round_name="Подвал, 1 круг",
                round_index=1,
                round_order=100 + half,
                is_consolation=True,
                team1=odd_loser,
                team2=bye_entity,
                player1=odd_loser.player1,
                player2=bye_entity.player1,
                winner_team=odd_loser,
                winner=odd_loser.player1,
                status=Match.MatchStatus.WALKOVER,
                deadline=base + delta,
                completed_datetime=timezone.now(),
            )
        else:
            Match.objects.create(
                tournament=tournament,
                round_name="Подвал, 1 круг",
                round_index=1,
                round_order=100 + half,
                is_consolation=True,
                player1=odd_loser,
                player2=bye_player,
                winner=odd_loser,
                status=Match.MatchStatus.WALKOVER,
                deadline=base + delta,
                completed_datetime=timezone.now(),
            )
        created += 1

    return True, f"Создано {created} матчей подвала."


def _next_match_pair(next_round_index: int, next_round_order: int) -> tuple[int, int]:
    """round_order двух матчей предыдущего раунда, чьи победители идут в (next_round_index, next_round_order)."""
    p1 = (next_round_order - 1) * 2 + 1
    p2 = (next_round_order - 1) * 2 + 2
    return (p1, p2)


def advance_winner_and_award_loser(match: Match) -> Optional[Match]:
    """
    После подтверждения результата матча: выдать очки проигравшему,
    при необходимости создать матч следующего раунда и «перевести» победителя.
    Поддерживает одиночные и парные турниры.
    Возвращает созданный next_match или None.
    """
    t = match.tournament
    if not _is_fan(t):
        return None
    is_doubles = t.is_doubles() and match.team1_id and match.team2_id
    winner_team = getattr(match, "winner_team", None)
    winner = match.winner
    if not winner and not winner_team:
        return None

    if is_doubles:
        loser_team = match.team2 if winner_team == match.team1 else match.team1
        losers = [loser_team.player1, loser_team.player2]
    else:
        loser = match.player2 if winner == match.player1 else match.player1
        losers = [loser]

    ri, ro = match.round_index, match.round_order
    is_cons = match.is_consolation

    if is_cons:
        points = t.fan_points_r1
        round_elim = TournamentPlayerResult.RoundEliminated.R1
    else:
        points = _fan_points_for_round(t, ri)
        round_elim = _round_eliminated(ri)

    for loser in losers:
        if not loser or getattr(loser, "is_bye", False):
            continue
        TournamentPlayerResult.objects.update_or_create(
            tournament=t,
            player=loser,
            defaults={"round_eliminated": round_elim, "fan_points": points, "is_consolation": is_cons},
        )
        loser.total_points += points
        loser.save(update_fields=["total_points"])

    # Подвал: один круг. Проигравшие подвала уже вылетели в R1 и имеют запись. Обновляем очки? Нет — они уже 10.
    if is_cons:
        return None

    # Основная сетка: следующий раунд
    next_ri = ri + 1
    next_ro = (ro + 1) // 2
    next_name = _round_name(next_ri)
    prev1, prev2 = _next_match_pair(next_ri, next_ro)
    if next_ri > 4:
        return None

    prev_matches = list(
        t.matches.filter(round_index=ri, round_order__in=(prev1, prev2), is_consolation=False)
    )
    existing = t.matches.filter(
        round_index=next_ri, round_order=next_ro, is_consolation=False
    ).first()
    start = _tournament_start_dt(t)
    delta = timedelta(days=getattr(t, "match_days_per_round", 7) or 7)
    deadline = start + delta * next_ri
    bye_player = _get_bye_player()

    def _is_bye_placeholder(m: Match) -> bool:
        if m.status != Match.MatchStatus.SCHEDULED:
            return False
        if is_doubles:
            if m.winner_team_id is not None:
                return False
            t1_bye = m.team1_id and getattr(m.team1.player1, "is_bye", False)
            t2_bye = m.team2_id and getattr(m.team2.player1, "is_bye", False)
            return bool(t1_bye or t2_bye)
        return m.winner_id is None and (
            (bye_player and (m.player1_id == bye_player.pk or m.player2_id == bye_player.pk))
        )

    # Слот следующего раунда уже занят заглушкой (игрок vs Bye). Оба матча текущего раунда
    # завершены — подставляем победителя «не-bye» матча в заглушку.
    if existing and len(prev_matches) == 2 and _is_bye_placeholder(existing):
        bye_prev = next(
            (m for m in prev_matches if getattr(m.player1, "is_bye", False) or getattr(m.player2, "is_bye", False)),
            None,
        )
        if bye_prev is not None:
            other_prev = next(m for m in prev_matches if m.pk != bye_prev.pk)
            if other_prev.winner_id or other_prev.winner_team_id:
                if is_doubles:
                    other_winner_team = other_prev.winner_team
                    if getattr(existing.team2.player1, "is_bye", False) if existing.team2_id else False:
                        existing.team2 = other_winner_team
                        existing.player2 = other_winner_team.player1
                    else:
                        existing.team1 = other_winner_team
                        existing.player1 = other_winner_team.player1
                    existing.save(update_fields=["team1", "team2", "player1", "player2"])
                else:
                    other_winner = other_prev.winner
                    if existing.player2_id == bye_player.pk:
                        existing.player2 = other_winner
                    else:
                        existing.player1 = other_winner
                    existing.save(update_fields=["player1", "player2"])
                other_prev.next_match = existing
                other_prev.save(update_fields=["next_match"])
                return existing

    if existing:
        return None

    # Один матч в паре: это «игрок vs Bye», второй слот пуст. Создаём только заглушку (SCHEDULED),
    # без победителя — соперник подставится, когда сыграет другая пара. Иначе игрок получал бы bye
    # в каждом раунде и выходил в финал без игр.
    if len(prev_matches) == 1:
        if next_ri >= _expected_final_round(t):
            return None
        if not bye_player:
            return None
        if is_doubles:
            bye_entity = _get_or_create_bye_team(t, bye_player)
            orphan_team = prev_matches[0].winner_team
            next_m = Match.objects.create(
                tournament=t,
                round_name=next_name,
                round_index=next_ri,
                round_order=next_ro,
                is_consolation=False,
                team1=orphan_team,
                team2=bye_entity,
                player1=orphan_team.player1,
                player2=bye_entity.player1,
                status=Match.MatchStatus.SCHEDULED,
                deadline=deadline,
            )
        else:
            orphan = prev_matches[0].winner
            next_m = Match.objects.create(
                tournament=t,
                round_name=next_name,
                round_index=next_ri,
                round_order=next_ro,
                is_consolation=False,
                player1=orphan,
                player2=bye_player,
                status=Match.MatchStatus.SCHEDULED,
                deadline=deadline,
            )
        prev_matches[0].next_match = next_m
        prev_matches[0].save(update_fields=["next_match"])
        return next_m

    if len(prev_matches) != 2:
        return None

    # Оба матча есть. Один может быть заглушкой (игрок vs Bye, без победителя) — тогда
    # не создаём новый матч, а подставляем в заглушку победителя второго матча.
    bye_placeholder = next((m for m in prev_matches if _is_bye_placeholder(m)), None)
    if bye_placeholder is not None:
        other_match = next(m for m in prev_matches if m.pk != bye_placeholder.pk)
        if not other_match.winner_id and not other_match.winner_team_id:
            return None
        if is_doubles:
            other_winner_team = other_match.winner_team
            if bye_placeholder.team2_id and getattr(bye_placeholder.team2.player1, "is_bye", False):
                bye_placeholder.team2 = other_winner_team
                bye_placeholder.player2 = other_winner_team.player1
            else:
                bye_placeholder.team1 = other_winner_team
                bye_placeholder.player1 = other_winner_team.player1
            bye_placeholder.save(update_fields=["team1", "team2", "player1", "player2"])
        else:
            other_winner = other_match.winner
            if bye_placeholder.player2_id == bye_player.pk:
                bye_placeholder.player2 = other_winner
            else:
                bye_placeholder.player1 = other_winner
            bye_placeholder.save(update_fields=["player1", "player2"])
        other_match.next_match = bye_placeholder
        other_match.save(update_fields=["next_match"])
        return bye_placeholder

    both_done = all(
        m.status in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER)
        and (m.winner_team_id if is_doubles else m.winner_id)
        for m in prev_matches
    )
    if not both_done:
        return None

    prev_matches.sort(key=lambda m: m.round_order)
    if is_doubles:
        w1, w2 = prev_matches[0].winner_team, prev_matches[1].winner_team
        next_m = Match.objects.create(
            tournament=t,
            round_name=next_name,
            round_index=next_ri,
            round_order=next_ro,
            is_consolation=False,
            team1=w1,
            team2=w2,
            player1=w1.player1,
            player2=w2.player1,
            status=Match.MatchStatus.SCHEDULED,
            deadline=deadline,
        )
    else:
        w1, w2 = prev_matches[0].winner, prev_matches[1].winner
        next_m = Match.objects.create(
            tournament=t,
            round_name=next_name,
            round_index=next_ri,
            round_order=next_ro,
            is_consolation=False,
            player1=w1,
            player2=w2,
            status=Match.MatchStatus.SCHEDULED,
            deadline=deadline,
        )
    for pm in prev_matches:
        pm.next_match = next_m
        pm.save(update_fields=["next_match"])
    return next_m


def _expected_final_round(tournament: Tournament) -> int:
    """Ожидаемый индекс раунда финала для single elimination (ceil(log2(N)))."""
    if tournament.is_doubles():
        n = tournament.teams.filter(player2__isnull=False).count()
    else:
        n = tournament.participants.count()
    if n < 2:
        return 1
    return max(1, math.ceil(math.log2(n)))


def finalize_tournament(tournament: Tournament) -> tuple[bool, str]:
    """
    Турнир завершён (финал сыгран). Начислить очки финалисту и победителю,
    пересчитать рейтинг: total_points += fan_points для всех участников.
    Вызывать ТОЛЬКО когда сыгран матч финала (не 1-й круг!).
    """
    if not _is_fan(tournament):
        return False, "Не FAN."
    if tournament.status == "completed":
        return False, "Турнир уже завершён."

    from django.db.models import Max

    expected_final_ri = _expected_final_round(tournament)
    agg = tournament.matches.filter(is_consolation=False).aggregate(Max("round_index"))
    max_ri = agg.get("round_index__max")
    if max_ri is None:
        return False, "Нет матчей основной сетки."
    # Финал — только матч в ожидаемом финальном раунде (не 1-й круг!)
    if max_ri < expected_final_ri:
        return False, "Финал ещё не сыгран."
    final = tournament.matches.filter(is_consolation=False, round_index=max_ri).first()
    if not final or final.status not in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER) or not final.winner:
        return False, "Финал не завершён."

    is_doubles = tournament.is_doubles() and final.team1_id and final.team2_id
    if is_doubles:
        winner_team = final.winner_team
        loser_team = final.team2 if winner_team == final.team1 else final.team1
        if getattr(winner_team.player1, "is_bye", False) or getattr(loser_team.player1, "is_bye", False):
            return False, "Финал не может быть с участием служебного игрока."
        finalists = [
            (loser_team.player1, TournamentPlayerResult.RoundEliminated.FINAL, tournament.fan_points_final),
            (loser_team.player2, TournamentPlayerResult.RoundEliminated.FINAL, tournament.fan_points_final),
            (winner_team.player1, TournamentPlayerResult.RoundEliminated.WINNER, tournament.fan_points_winner),
            (winner_team.player2, TournamentPlayerResult.RoundEliminated.WINNER, tournament.fan_points_winner),
        ]
    else:
        winner = final.winner
        loser = final.player2 if winner == final.player1 else final.player1
        if getattr(winner, "is_bye", False) or getattr(loser, "is_bye", False):
            return False, "Финал не может быть с участием служебного игрока."
        finalists = [
            (loser, TournamentPlayerResult.RoundEliminated.FINAL, tournament.fan_points_final),
            (winner, TournamentPlayerResult.RoundEliminated.WINNER, tournament.fan_points_winner),
        ]

    for player, round_elim, points in finalists:
        if not player or getattr(player, "is_bye", False):
            continue
        TournamentPlayerResult.objects.update_or_create(
            tournament=tournament,
            player=player,
            defaults={"round_eliminated": round_elim, "fan_points": points, "is_consolation": False},
        )
        player.total_points += points
        player.save(update_fields=["total_points"])

    tournament.status = "completed"
    tournament.save(update_fields=["status"])
    logger.info("FAN tournament %s completed, ratings updated.", tournament.name)
    return True, "Турнир завершён, рейтинг обновлён."


def ensure_consolation_created(tournament: Tournament) -> None:
    """Вызвать после каждого завершённого матча R1: если все R1 сыграны — создать подвал."""
    if not _is_fan(tournament):
        return
    ok, _ = create_consolation_matches(tournament)
    if ok:
        logger.info("Consolation bracket created for %s", tournament.name)


def _overdue_winner(match: Match) -> Optional[Player]:
    """
    При просрочке дедлайна победа присуждается игроку с более высоким рейтингом.
    При равенстве — с меньшим id. Bye не может быть «победителем» в таком матче.
    """
    a, b = match.player1, match.player2
    if getattr(a, "is_bye", False):
        return b
    if getattr(b, "is_bye", False):
        return a
    if a.total_points != b.total_points:
        return a if a.total_points > b.total_points else b
    return a if a.pk < b.pk else b


def apply_overdue_walkover(match: Match, winner: Player) -> None:
    """
    Оформить тех. победу (дедлайн истёк): обновить матч, отклонить заявки, уведомить игроков.
    Не вызывает advance_winner / finalize — это делает вызывающий код.
    """
    loser = match.player2 if winner == match.player1 else match.player1
    match.winner = winner
    match.status = Match.MatchStatus.WALKOVER
    match.completed_datetime = timezone.now()
    match.save(update_fields=["winner", "status", "completed_datetime"])

    match.result_proposals.filter(status=Match.ProposalStatus.PENDING).update(
        status=Match.ProposalStatus.REJECTED
    )
    url = reverse("match_detail", args=[match.pk])
    Notification.objects.create(user=winner.user, message="Дедлайн матча истёк. Вам присуждена тех. победа.", url=url)
    Notification.objects.create(user=loser.user, message="Дедлайн матча истёк. Вам засчитано тех. поражение.", url=url)
    logger.info("Overdue walkover: match %s → winner %s", match.pk, winner)


def process_overdue_match(match: Match) -> tuple[bool, str]:
    """
    Обработать просроченный FAN-матч: тех. победа сильнейшему по рейтингу, продвижение, подвал, финализация.
    Возвращает (успех, сообщение).
    """
    if not _is_fan(match.tournament):
        return False, "Не FAN."
    if match.status in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER):
        return False, "Матч уже завершён."
    if not match.deadline or match.deadline > timezone.now():
        return False, "Дедлайн не истёк."
    if getattr(match.player1, "is_bye", False) and getattr(match.player2, "is_bye", False):
        return False, "Служебный матч."

    winner = _overdue_winner(match)
    apply_overdue_walkover(match, winner)

    advance_winner_and_award_loser(match)
    if match.round_index == 1 and not match.is_consolation:
        ensure_consolation_created(match.tournament)
    finalize_tournament(match.tournament)

    return True, f"Матч {match.pk} ({match.player1} vs {match.player2}): тех. победа {winner} (дедлайн истёк)."
