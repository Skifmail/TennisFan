"""
Система FAN: генерация сетки, продвижение победителей, начисление очков.
"""

import logging
import math
from datetime import datetime, timedelta
from typing import cast

from django.urls import reverse
from django.utils import timezone

from apps.users.models import Notification, Player

from .models import (
    Match,
    SeasonPoints,
    Tournament,
    TournamentPlayerResult,
    TournamentStatus,
    TournamentTeam,
)
from .postpayment import get_pending_postpayment_users, open_postpayment_window
from .season_utils import get_current_season, get_season_display

logger = logging.getLogger(__name__)

FAN_FORMAT = "single_elimination"
BYE_EMAIL = "bye@tennisfan.local"


def _update_season_points(
    player: Player, points: int, match: Match | None = None
) -> None:
    """Обновить сезонные очки игрока.

    Args:
        player: Игрок, которому начисляются очки.
        points: Количество FAN очков для добавления.
        match: Опциональный объект матча для проверки типа (спарринги не дают очки).
    """
    if not player or getattr(player, "is_bye", False):
        return

    if points <= 0:
        return

    # Спарринговые матчи не дают сезонные очки
    if match and match.is_sparring():
        logger.debug("Skipping season points for sparring match %s", match.pk)
        return

    current_season = get_current_season()
    season_points, created = SeasonPoints.objects.get_or_create(
        player=player,
        defaults={
            "current_season_points": 0,
            "season_name": current_season.name,
            "season_year": current_season.year,
        },
    )

    # Если сезон изменился, сбросить очки (должно происходить через команду, но на всякий случай)
    if (
        season_points.season_name != current_season.name
        or season_points.season_year != current_season.year
    ):
        season_points.current_season_points = 0
        season_points.season_name = current_season.name
        season_points.season_year = current_season.year

    season_points.current_season_points += points
    season_points.save(
        update_fields=[
            "current_season_points",
            "season_name",
            "season_year",
            "updated_at",
        ]
    )
    logger.debug(
        "Season points updated: %s +%d = %d (%s)",
        player,
        points,
        season_points.current_season_points,
        get_season_display(current_season),
    )


def _is_fan(t: Tournament | None) -> bool:
    if t is None:
        return False
    return getattr(t, "format", None) == FAN_FORMAT


def _get_bye_player() -> Player | None:
    """Служебный игрок «Свободный круг» для матчей при нечётном числе участников."""
    return cast(
        Player | None,
        Player.objects.filter(user__email=BYE_EMAIL, is_bye=True)
        .select_related("user")
        .first(),
    )


def _get_or_create_bye_team(
    tournament: Tournament, bye_player: Player
) -> TournamentTeam:
    """Команда «Свободный круг» для парного турнира при нечётном числе команд."""
    team, _ = TournamentTeam.objects.get_or_create(
        tournament=tournament,
        player1=bye_player,
        player2=bye_player,
        defaults={},
    )
    return cast(TournamentTeam, team)


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
    return int(m.get(round_index, 0))


def _round_eliminated(round_index: int) -> str:
    m = {
        1: TournamentPlayerResult.RoundEliminated.R1,
        2: TournamentPlayerResult.RoundEliminated.R2,
        3: TournamentPlayerResult.RoundEliminated.SF,
        4: TournamentPlayerResult.RoundEliminated.FINAL,
    }
    val = m.get(round_index, TournamentPlayerResult.RoundEliminated.R1)
    return val if isinstance(val, str) else str(val)


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
    from django.conf import settings
    from django.core.cache import cache

    from .round_robin import generate_bracket as generate_round_robin_bracket

    cache_key = "tournament_generate_brackets_last_run"
    # В режиме разработки уменьшаем время кэширования для более быстрой отладки
    cache_timeout = 10 if settings.DEBUG else 60
    if cache.get(cache_key):
        return 0
    cache.set(
        cache_key, True, cache_timeout
    )  # не чаще раза в минуту (или 10 сек в DEBUG)

    from .olympic_consolation import (
        _is_olympic,
    )
    from .olympic_consolation import (
        generate_bracket as generate_olympic_bracket,
    )

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
        if t.allow_postpayment and t.postpayment_window_started_at is None:
            pending_users = get_pending_postpayment_users(t)
            if pending_users:
                open_postpayment_window(t)
                logger.info(
                    "Opened postpayment window for %s with %s pending users",
                    t.slug,
                    len(pending_users),
                )
                continue
        # Проверка минимального количества участников/команд
        min_required = t.min_teams if t.is_doubles() else t.min_participants
        if min_required is not None:
            count = t.full_teams_count() if t.is_doubles() else t.participants.count()
            if count < min_required:
                notified_at = t.insufficient_participants_notified_at
                if notified_at is None:
                    from apps.core.telegram_notify import (
                        notify_tournament_insufficient_participants,
                    )

                    notify_tournament_insufficient_participants(t)
                    t.insufficient_participants_notified_at = now
                    t.save(update_fields=["insufficient_participants_notified_at"])
                    logger.info(
                        "Insufficient participants for %s: %s/%s, notified admin",
                        t.slug,
                        count,
                        min_required,
                    )
                elif (notified_at + timedelta(hours=3)) <= now:
                    from .cancel import cancel_tournament

                    cancel_tournament(t)
                    logger.info(
                        "Cancelled tournament %s: still insufficient after 3h (%s/%s)",
                        t.slug,
                        count,
                        min_required,
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


def _bracket_params(n: int) -> tuple[int, int]:
    """Размер сетки и число раундов: bracket_size=2^ceil(log2(n)), total_rounds=log2(bracket_size)."""
    if n < 2:
        return 2, 1
    bracket_size = 2 ** int(math.ceil(math.log2(n)))
    total_rounds = int(math.log2(bracket_size))
    return bracket_size, total_rounds


def _bracket_r1_count(n: int) -> int:
    """Количество матчей в первом круге для N участников."""
    bracket_size, _ = _bracket_params(n)
    return bracket_size // 2


def generate_bracket(tournament: Tournament) -> tuple[bool, str]:
    """
    Сформировать сетку FAN: single elimination через фиксированное бинарное дерево.
    Размер сетки = 2^ceil(log2(N)), BYE до степени двойки.
    Все матчи всех раундов создаются сразу, связываются через next_match.
    """
    if not _is_fan(tournament):
        return False, "Турнир не в формате FAN."
    if tournament.bracket_generated:
        return False, "Сетка уже сформирована."

    from .solo_teams import remove_solo_teams_from_doubles_tournament

    if tournament.is_doubles():
        removed = remove_solo_teams_from_doubles_tournament(tournament)
        if removed:
            logger.info(
                "Removed %d solo teams from FAN doubles tournament %s",
                removed,
                tournament.slug,
            )

    if tournament.is_doubles():
        entities = list(
            tournament.teams.filter(player2__isnull=False)
            .exclude(player1__is_bye=True)
            .select_related("player1__user", "player2__user")
            .order_by("-player1__total_points")
        )
        max_n = tournament.max_teams
        entity_name = "команд"
    else:
        entities = list(
            tournament.participants.exclude(is_bye=True).order_by("-total_points")
        )
        max_n = tournament.max_participants
        entity_name = "участников"

    n = len(entities)
    if n < 2:
        return False, f"Нужно минимум 2 {entity_name[:-1]} для формирования сетки."
    if max_n is not None and n > max_n:
        return False, f"Зарегистрировано {n}, максимум {max_n}."

    bye_player = _get_bye_player()
    if not bye_player:
        return (
            False,
            "Не найден служебный игрок «Свободный круг» (bye). Выполните миграции users.",
        )

    bracket_size, total_rounds = _bracket_params(n)
    padded = list(entities)
    bye_entity = (
        bye_player
        if not tournament.is_doubles()
        else _get_or_create_bye_team(tournament, bye_player)
    )
    while len(padded) < bracket_size:
        padded.append(bye_entity)

    start = _tournament_start_dt(tournament)
    days = getattr(tournament, "match_days_per_round", 7) or 7
    delta = timedelta(days=days)
    is_doubles = tournament.is_doubles()

    matches: dict[tuple[int, int], Match] = {}
    for r in range(total_rounds):
        num_matches = bracket_size // (2 ** (r + 1))
        round_index = r + 1
        round_name = (
            "Финал" if round_index == total_rounds else _round_name(round_index)
        )
        deadline = start + delta * round_index
        for o in range(num_matches):
            m = Match.objects.create(
                tournament=tournament,
                round_name=round_name,
                round_index=round_index,
                round_order=o + 1,
                is_consolation=False,
                status=Match.MatchStatus.SCHEDULED,
                deadline=deadline,
            )
            matches[(r, o)] = m

    for r in range(total_rounds - 1):
        num_matches = bracket_size // (2 ** (r + 1))
        for o in range(num_matches):
            child = matches[(r, o)]
            parent = matches[(r + 1, o // 2)]
            child.next_match = parent
            child.save(update_fields=["next_match"])

    num_r1 = bracket_size // 2
    for o in range(num_r1):
        a, b = padded[o], padded[bracket_size - 1 - o]
        m = matches[(0, o)]
        if is_doubles:
            m.team1 = a
            m.team2 = b
            m.player1 = a.player1
            m.player2 = b.player1
        else:
            m.player1 = a
            m.player2 = b

        def _is_bye_entity(e) -> bool:
            if is_doubles:
                return bool(e and getattr(getattr(e, "player1", None), "is_bye", False))
            return bool(getattr(e, "is_bye", False) or e == bye_player)

        is_bye_a, is_bye_b = _is_bye_entity(a), _is_bye_entity(b)
        if is_bye_a or is_bye_b:
            winner_entity = b if is_bye_a else a
            if is_doubles:
                m.winner_team = winner_entity
                m.winner = winner_entity.player1
            else:
                m.winner = winner_entity
            m.status = Match.MatchStatus.WALKOVER
            m.completed_datetime = timezone.now()
        m.save()

    tournament.bracket_generated = True
    tournament.save(update_fields=["bracket_generated"])
    total_matches = len(matches)
    logger.info(
        "FAN bracket created for %s: %d matches (n=%d, bracket_size=%d)",
        tournament.name,
        total_matches,
        n,
        bracket_size,
    )
    try:
        from apps.telegram_bot.notifications import notify_bracket_formed

        notify_bracket_formed(tournament)
    except Exception as e:
        logger.exception("notify_bracket_formed for %s: %s", tournament.slug, e)
    return True, f"Сетка сформирована: {total_matches} матчей, {entity_name} {n}."


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
    unfinished = [
        m
        for m in r1
        if m.status not in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER)
    ]
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


def advance_winner_and_award_loser(
    match: Match, skip_points: bool = False
) -> Match | None:
    """
    После подтверждения результата матча: начислить очки проигравшему,
    подставить победителя в parent (next_match). Родитель уже существует.
    """
    t = match.tournament
    if not _is_fan(t):
        return None

    winner_team = getattr(match, "winner_team", None)
    winner = match.winner
    if not winner and not winner_team:
        return None

    is_doubles = t.is_doubles() and bool(match.team1_id and match.team2_id)
    if is_doubles:
        loser_team = match.team2 if winner_team == match.team1 else match.team1
        losers = [loser_team.player1, loser_team.player2] if loser_team else []
    else:
        loser = match.player2 if winner == match.player1 else match.player1
        losers = [loser] if loser else []

    if not skip_points:
        if match.is_consolation:
            points = t.fan_points_r1
            round_elim: str = TournamentPlayerResult.RoundEliminated.R1[0]
        else:
            points = _fan_points_for_round(t, match.round_index)
            round_elim = _round_eliminated(match.round_index)
        for loser in losers:
            if not loser or getattr(loser, "is_bye", False):
                continue
            TournamentPlayerResult.objects.update_or_create(
                tournament=t,
                player=loser,
                defaults={
                    "round_eliminated": round_elim,
                    "fan_points": points,
                    "is_consolation": match.is_consolation,
                },
            )
            _update_season_points(loser, points, match=match)

    if match.is_consolation:
        return None

    parent = match.next_match
    if parent is None:
        return None

    fill_slot1 = match.round_order % 2 == 1

    if is_doubles:
        if fill_slot1:
            parent.team1 = winner_team
            parent.player1 = winner_team.player1 if winner_team else None
        else:
            parent.team2 = winner_team
            parent.player2 = winner_team.player1 if winner_team else None
        both_filled = parent.team1_id and parent.team2_id
    else:
        if fill_slot1:
            parent.player1 = winner
        else:
            parent.player2 = winner
        both_filled = parent.player1_id and parent.player2_id

    if both_filled:
        parent.status = Match.MatchStatus.SCHEDULED

    update_fields = (
        ["team1", "team2", "player1", "player2"]
        if is_doubles
        else ["player1", "player2"]
    )
    if both_filled:
        update_fields.append("status")
    parent.save(update_fields=update_fields)
    return cast(Match | None, parent)


def _expected_final_round(tournament: Tournament) -> int:
    """Ожидаемый индекс раунда финала для single elimination (ceil(log2(N)))."""
    if tournament.is_doubles():
        n = tournament.teams.filter(player2__isnull=False).count()
    else:
        n = tournament.participants.count()
    if n < 2:
        return 1
    return int(max(1, math.ceil(math.log2(n))))


def finalize_tournament(tournament: Tournament) -> tuple[bool, str]:
    """
    Турнир завершён (финал сыгран). Начислить очки финалисту и победителю.
    Финал — матч без next_match (корень бинарного дерева).
    """
    if not _is_fan(tournament):
        return False, "Не FAN."
    if tournament.status == "completed":
        return False, "Турнир уже завершён."

    final = tournament.matches.filter(
        is_consolation=False, next_match__isnull=True
    ).first()
    if (
        not final
        or final.status not in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER)
        or not final.winner
    ):
        return False, "Финал не завершён."

    is_doubles = tournament.is_doubles() and final.team1_id and final.team2_id
    if is_doubles:
        winner_team = final.winner_team
        loser_team = final.team2 if winner_team == final.team1 else final.team1
        if getattr(winner_team.player1, "is_bye", False) or getattr(
            loser_team.player1, "is_bye", False
        ):
            return False, "Финал не может быть с участием служебного игрока."
        finalists = [
            (
                loser_team.player1,
                TournamentPlayerResult.RoundEliminated.FINAL,
                tournament.fan_points_final,
            ),
            (
                loser_team.player2,
                TournamentPlayerResult.RoundEliminated.FINAL,
                tournament.fan_points_final,
            ),
            (
                winner_team.player1,
                TournamentPlayerResult.RoundEliminated.WINNER,
                tournament.fan_points_winner,
            ),
            (
                winner_team.player2,
                TournamentPlayerResult.RoundEliminated.WINNER,
                tournament.fan_points_winner,
            ),
        ]
    else:
        winner = final.winner
        loser = final.player2 if winner == final.player1 else final.player1
        if getattr(winner, "is_bye", False) or getattr(loser, "is_bye", False):
            return False, "Финал не может быть с участием служебного игрока."
        finalists = [
            (
                loser,
                TournamentPlayerResult.RoundEliminated.FINAL,
                tournament.fan_points_final,
            ),
            (
                winner,
                TournamentPlayerResult.RoundEliminated.WINNER,
                tournament.fan_points_winner,
            ),
        ]

    for player, round_elim, points in finalists:
        if not player or getattr(player, "is_bye", False):
            continue
        TournamentPlayerResult.objects.update_or_create(
            tournament=tournament,
            player=player,
            defaults={
                "round_eliminated": round_elim,
                "fan_points": points,
                "is_consolation": False,
            },
        )
        # Обновляем сезонные очки (только для турнирных матчей)
        _update_season_points(
            player, points, match=None
        )  # В финале match уже завершен, но это турнирный матч

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


def _overdue_winner(match: Match) -> Player | None:
    """
    При просрочке дедлайна победа присуждается игроку с более высоким рейтингом.
    При равенстве — с меньшим id. Bye не может быть «победителем» в таком матче.
    """
    a, b = match.player1, match.player2
    if a is None or b is None:
        return None
    if getattr(a, "is_bye", False):
        return cast(Player | None, b)
    if getattr(b, "is_bye", False):
        return cast(Player | None, a)
    if a.total_points != b.total_points:
        return cast(Player | None, a if a.total_points > b.total_points else b)
    return cast(Player | None, a if a.pk < b.pk else b)


def apply_overdue_walkover(match: Match, winner: Player) -> None:
    """
    Оформить тех. победу (дедлайн истёк): обновить матч, отклонить заявки, уведомить игроков.
    Не вызывает advance_winner / finalize — это делает вызывающий код.
    """
    loser = match.player2 if winner == match.player1 else match.player1
    if loser is None:
        return
    match.winner = winner
    match.status = Match.MatchStatus.WALKOVER
    match.completed_datetime = timezone.now()
    match.save(update_fields=["winner", "status", "completed_datetime"])

    match.result_proposals.filter(status=Match.ProposalStatus.PENDING).update(
        status=Match.ProposalStatus.REJECTED
    )
    url = reverse("match_detail", args=[match.pk])
    Notification.objects.create(
        user=winner.user,
        message="Дедлайн матча истёк. Вам присуждена тех. победа.",
        url=url,
    )
    Notification.objects.create(
        user=loser.user,
        message="Дедлайн матча истёк. Вам засчитано тех. поражение.",
        url=url,
    )
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
    if getattr(match.player1, "is_bye", False) and getattr(
        match.player2, "is_bye", False
    ):
        return False, "Служебный матч."

    winner = _overdue_winner(match)
    if winner is None:
        return False, "Не удалось определить победителя."
    apply_overdue_walkover(match, winner)

    # При просрочке не начисляем очки проигравшему
    advance_winner_and_award_loser(match, skip_points=True)
    if match.round_index == 1 and not match.is_consolation:
        ensure_consolation_created(match.tournament)
    finalize_tournament(match.tournament)

    return (
        True,
        f"Матч {match.pk} ({match.player1} vs {match.player2}): тех. победа {winner} (дедлайн истёк).",
    )
