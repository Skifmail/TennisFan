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

from .models import Match, Tournament, TournamentPlayerResult

logger = logging.getLogger(__name__)

FAN_FORMAT = "single_elimination"
BYE_EMAIL = "bye@tennisfan.local"


def _is_fan(t: Tournament) -> bool:
    return getattr(t, "format", None) == FAN_FORMAT


def _get_bye_player() -> Optional[Player]:
    """Служебный игрок «Свободный круг» для матчей при нечётном числе участников."""
    return Player.objects.filter(user__email=BYE_EMAIL, is_bye=True).select_related("user").first()


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
    Найти FAN-турниры с истёкшим дедлайном регистрации и сформировать сетку.
    Вызывать при загрузке страниц турниров (или по cron).
    Возвращает количество сформированных сеток.
    """
    from django.core.cache import cache

    cache_key = "fan_generate_brackets_last_run"
    if cache.get(cache_key):
        return 0
    cache.set(cache_key, True, 60)  # не чаще раза в минуту

    now = timezone.now()
    qs = list(
        Tournament.objects.filter(
            format="single_elimination",
            bracket_generated=False,
            registration_deadline__lte=now,
            registration_deadline__isnull=False,
        )
    )
    total = 0
    for t in qs:
        ok, msg = generate_bracket(t)
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
    """
    if not _is_fan(tournament):
        return False, "Турнир не в формате FAN."
    if tournament.bracket_generated:
        return False, "Сетка уже сформирована."

    participants = list(tournament.participants.order_by("-total_points"))
    n = len(participants)
    max_n = tournament.max_participants

    if n < 2:
        return False, "Нужно минимум 2 участника для формирования сетки."
    if max_n is not None and n > max_n:
        return False, f"Зарегистрировано {n}, максимум {max_n}."

    start = _tournament_start_dt(tournament)
    days = getattr(tournament, "match_days_per_round", 7) or 7
    delta = timedelta(days=days)
    bye_player = _get_bye_player()
    odd = n % 2 == 1
    if odd and not bye_player:
        return False, "Не найден служебный игрок «Свободный круг» (bye). Выполните миграции users."

    # R1: 1 vs N, 2 vs N-1, …; при нечётном N — сеяный 1 получает bye (матч «игрок — свободный круг»).
    num_real = n // 2
    created = 0
    round_order = 1

    if odd:
        Match.objects.create(
            tournament=tournament,
            round_name="1 круг",
            round_index=1,
            round_order=round_order,
            is_consolation=False,
            player1=participants[0],
            player2=bye_player,
            winner=participants[0],
            status=Match.MatchStatus.WALKOVER,
            deadline=start + delta,
            completed_datetime=timezone.now(),
        )
        created += 1
        round_order += 1

    for i in range(num_real):
        lo, hi = (i + 1, n - 1 - i) if odd else (i, n - 1 - i)
        a, b = participants[lo], participants[hi]
        Match.objects.create(
            tournament=tournament,
            round_name="1 круг",
            round_index=1,
            round_order=round_order,
            is_consolation=False,
            player1=a,
            player2=b,
            status=Match.MatchStatus.SCHEDULED,
            deadline=start + delta,
        )
        created += 1
        round_order += 1

    tournament.bracket_generated = True
    tournament.save(update_fields=["bracket_generated"])
    logger.info("FAN bracket R1 created for %s: %d matches (n=%d, odd=%s)", tournament.name, created, n, odd)
    return True, f"Сетка сформирована: {created} матчей 1-го круга, участников {n}."


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

    losers = []
    for m in r1.order_by("round_order"):
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

    # При нечётном числе проигравших (7): один остаётся без пары — даём ему матч vs Свободный круг (walkover)
    if n % 2 == 1 and bye_player:
        odd_loser = losers[half]
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
    Возвращает созданный next_match или None.
    """
    t = match.tournament
    if not _is_fan(t):
        return None
    winner = match.winner
    if not winner:
        return None
    loser = match.player2 if winner == match.player1 else match.player1
    ri, ro = match.round_index, match.round_order
    is_cons = match.is_consolation

    skip_award = getattr(loser, "is_bye", False)
    if not skip_award:
        if is_cons:
            points = t.fan_points_r1
            round_elim = TournamentPlayerResult.RoundEliminated.R1
        else:
            points = _fan_points_for_round(t, ri)
            round_elim = _round_eliminated(ri)
        TournamentPlayerResult.objects.update_or_create(
            tournament=t,
            player=loser,
            defaults={"round_eliminated": round_elim, "fan_points": points, "is_consolation": is_cons},
        )

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
    both_done = all(
        m.status in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER) and m.winner_id
        for m in prev_matches
    )
    if not both_done:
        return None

    existing = t.matches.filter(
        round_index=next_ri, round_order=next_ro, is_consolation=False
    ).first()
    if existing:
        return None

    start = _tournament_start_dt(t)
    delta = timedelta(days=getattr(t, "match_days_per_round", 7) or 7)
    deadline = start + delta * next_ri
    bye_player = _get_bye_player()

    if len(prev_matches) == 1:
        # Финал не может содержать Bye — нужны оба полуфиналиста.
        # Если next_ri — раунд финала, ждём второй матч-фидер (вернётся None).
        if next_ri >= _expected_final_round(t):
            return None
        # Один матч-фидер (напр. при 14 участниках: R2.4 от матча 7) — bye в следующий раунд.
        # Справедливо: bye получает сильнейший по рейтингу среди победителей раунда.
        orphan = prev_matches[0].winner
        if not bye_player:
            return None
        # Все победители текущего раунда (исключая Bye)
        all_round_matches = list(
            t.matches.filter(round_index=ri, is_consolation=False)
            .exclude(winner__isnull=True)
            .exclude(winner__is_bye=True)
        )
        winners = [m.winner for m in all_round_matches if m.winner_id]
        if not winners:
            return None
        bye_recipient = max(winners, key=lambda p: (p.total_points, -p.pk))
        if bye_recipient != orphan:
            # Своп: orphan занимает место bye_recipient в уже созданном матче следующего раунда
            swap_match = t.matches.filter(
                round_index=next_ri, is_consolation=False
            ).filter(
                models.Q(player1=bye_recipient) | models.Q(player2=bye_recipient)
            ).first()
            if swap_match:
                if swap_match.player1_id == bye_recipient.pk:
                    swap_match.player1 = orphan
                else:
                    swap_match.player2 = orphan
                swap_match.save(update_fields=["player1", "player2"])
                # orphan (победитель match 7) идёт в swap_match
                prev_matches[0].next_match = swap_match
                prev_matches[0].save(update_fields=["next_match"])
                # bye_recipient идёт в next_m — найдём его матч-фидер
                feeder_for_bye = next(
                    (m for m in all_round_matches if m.winner_id == bye_recipient.pk),
                    None,
                )
        next_m = Match.objects.create(
            tournament=t,
            round_name=next_name,
            round_index=next_ri,
            round_order=next_ro,
            is_consolation=False,
            player1=bye_recipient,
            player2=bye_player,
            winner=bye_recipient,
            status=Match.MatchStatus.WALKOVER,
            deadline=deadline,
            completed_datetime=timezone.now(),
        )
        if bye_recipient == orphan:
            prev_matches[0].next_match = next_m
            prev_matches[0].save(update_fields=["next_match"])
        else:
            feeder_for_bye = next(
                (m for m in all_round_matches if m.winner_id == bye_recipient.pk),
                None,
            )
            if feeder_for_bye:
                feeder_for_bye.next_match = next_m
                feeder_for_bye.save(update_fields=["next_match"])
        return next_m

    if len(prev_matches) != 2:
        return None

    prev_matches.sort(key=lambda m: m.round_order)
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

    winner = final.winner
    loser = final.player2 if winner == final.player1 else final.player1
    if getattr(winner, "is_bye", False) or getattr(loser, "is_bye", False):
        return False, "Финал не может быть с участием служебного игрока."

    for player, round_elim, points in [
        (loser, TournamentPlayerResult.RoundEliminated.FINAL, tournament.fan_points_final),
        (winner, TournamentPlayerResult.RoundEliminated.WINNER, tournament.fan_points_winner),
    ]:
        TournamentPlayerResult.objects.update_or_create(
            tournament=tournament,
            player=player,
            defaults={"round_eliminated": round_elim, "fan_points": points, "is_consolation": False},
        )

    for r in TournamentPlayerResult.objects.filter(tournament=tournament):
        if getattr(r.player, "is_bye", False):
            continue
        r.player.total_points += r.fan_points
        r.player.save(update_fields=["total_points"])

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
