"""
Турнир выходного дня (ТВД): группы (мини-круговой) + плей-офф (нокаут) + утешительная сетка.
Поддержка 2–6 групп, посев змейкой по Elo, продвижение по сетке.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, cast

from django.utils import timezone

from apps.users.models import Player

from .models import (
    Match,
    Tournament,
    TournamentPlayerResult,
    TournamentStatus,
    TournamentTeam,
    TVDGroup,
    TVDGroupMember,
)
from .season_utils import get_current_season

logger = logging.getLogger(__name__)

TVD_FORMAT = "weekend_day"

# Этапы ТВД для Match.tvd_stage
TVD_STAGE_GROUP = "group"
TVD_STAGE_MAIN_QF = "main_qf"
TVD_STAGE_MAIN_SF = "main_sf"
TVD_STAGE_MAIN_FINAL = "main_final"
TVD_STAGE_THIRD_PLACE = "third_place"
TVD_STAGE_CONSOLATION_SF = "consolation_sf"
TVD_STAGE_CONSOLATION_FINAL = "consolation_final"
TVD_STAGE_MAIN_RR = "main_round_robin"
TVD_STAGE_MAIN_RR_1_3 = "main_rr_1_3"  # 3 группы: круг победителей групп за 1–3 места
TVD_STAGE_MAIN_RR_4_6 = "main_rr_4_6"  # 3 группы: круг вторых мест за 4–6 места
TVD_STAGE_CONSOLATION_RR = "consolation_round_robin"


def get_tvd_rr_entities_and_matches(
    tournament: Tournament, tvd_stage: str
) -> tuple[list[Player | TournamentTeam] | None, list[Match]]:
    """
    Участники и матчи круговой сетки ТВД по этапу (main_round_robin / consolation_round_robin).
    Возвращает (entities, matches) или (None, []) если матчей нет.
    """
    matches = list(
        tournament.matches.filter(tvd_stage=tvd_stage).select_related(
            "player1", "player2", "winner", "team1", "team2", "winner_team"
        )
    )
    if not matches:
        return None, []
    is_doubles = tournament.is_doubles()
    seen: set[int] = set()
    entities: list[Player | TournamentTeam] = []
    for m in matches:
        if is_doubles:
            for t in (m.team1, m.team2):
                if t and t.id not in seen:
                    seen.add(t.id)
                    entities.append(t)
        else:
            for p in (m.player1, m.player2):
                if p and p.id not in seen:
                    seen.add(p.id)
                    entities.append(p)
    entities.sort(key=lambda e: e.id)
    return entities, matches


def _is_tvd(t: Tournament | None) -> bool:
    return t is not None and getattr(t, "format", None) == TVD_FORMAT


def _tournament_start_dt(tournament: Tournament):
    """Дата/время старта турнира для дедлайнов."""
    start = timezone.now()
    if tournament.start_date:
        d = tournament.start_date
        if isinstance(d, str):
            d = datetime.strptime(d, "%Y-%m-%d").date()
        start = timezone.make_aware(datetime.combine(d, datetime.min.time()))
    return start


def calculate_group_structure(n: int) -> list[int]:
    """
    Определение числа и размера групп (2–6 групп).
    Приоритет: группы по 3, остаток — по 4 или 2.
    Минимум 4 участника: при n < 4 возвращаем [] (группы из 1 человека недопустимы).
    """
    if n < 4:
        return []
    group_count = min(6, max(2, (n + 2) // 3))
    base = n // group_count
    remainder = n % group_count
    sizes = []
    for i in range(group_count):
        size = base + (1 if i < remainder else 0)
        if size < 2:
            return []  # не допускаем группу из 1
        sizes.append(size)
    return sizes


def _team_rating(team: TournamentTeam) -> float:
    """Средний рейтинг команды (для посева парного ТВД)."""
    r1 = getattr(team.player1, "hidden_rating", None) or 0
    r2: float = (
        (getattr(team.player2, "hidden_rating", None) or 0) if team.player2_id else 0
    )
    return (r1 + r2) / 2 if team.player2_id else float(r1)


def serpentine_distribution(
    players: list[Player], group_count: int
) -> list[list[Player]]:
    """
    Распределение игроков по группам змейкой по рейтингу (Elo / hidden_rating, убывание).
    1 в A, 2 в B, ..., group_count в последнюю, затем в обратном порядке.
    """
    ordered = sorted(
        players,
        key=lambda p: (-(getattr(p, "hidden_rating", 0) or 0), p.pk),
    )
    groups: list[list[Player]] = [[] for _ in range(group_count)]
    for i, p in enumerate(ordered):
        row = i // group_count
        if row % 2 == 0:
            col = i % group_count
        else:
            col = group_count - 1 - (i % group_count)
        groups[col].append(p)
    return groups


def serpentine_distribution_teams(
    teams: list[TournamentTeam], group_count: int
) -> list[list[TournamentTeam]]:
    """Распределение команд по группам змейкой по среднему рейтингу команды."""
    ordered = sorted(
        teams,
        key=lambda t: (-_team_rating(t), t.pk),
    )
    groups: list[list[TournamentTeam]] = [[] for _ in range(group_count)]
    for i, t in enumerate(ordered):
        row = i // group_count
        if row % 2 == 0:
            col = i % group_count
        else:
            col = group_count - 1 - (i % group_count)
        groups[col].append(t)
    return groups


def generate_groups(tournament: Tournament) -> tuple[bool, str]:
    """
    Создать группы ТВД, TVDGroupMember и матчи группового этапа (мини-круговой в каждой группе).
    Одиночный: участники — игроки. Парный: участники — полные команды (player2 не null).
    """
    if not _is_tvd(tournament):
        return False, "Турнир не в формате ТВД."
    if tournament.bracket_generated:
        return False, "Группы уже сформированы (сетка зафиксирована)."

    start = _tournament_start_dt(tournament)
    days = getattr(tournament, "match_days_per_round", 7) or 7
    delta = timedelta(days=days)

    if tournament.is_doubles():
        full_teams = list(
            tournament.teams.filter(player2__isnull=False).select_related(
                "player1", "player2"
            )
        )
        n = len(full_teams)
        if n < 4:
            return False, "Нужно минимум 4 полные команды для формирования групп ТВД."
        sizes = calculate_group_structure(n)
        group_count = len(sizes)
        if group_count < 2 or group_count > 6:
            return False, f"Получено {group_count} групп; допустимо 2–6."
        distribution = serpentine_distribution_teams(full_teams, group_count)
        names = "ABCDEF"[:group_count]
        for gi, (size, name) in enumerate(zip(sizes, names, strict=True)):
            group_teams = distribution[gi][:size]
            if len(group_teams) < 2:
                continue
            group = TVDGroup.objects.create(
                tournament=tournament,
                name=name,
                order=gi + 1,
                is_completed=False,
            )
            for seed, team in enumerate(group_teams, 1):
                TVDGroupMember.objects.create(
                    group=group,
                    team=team,
                    seed=seed,
                    wins=0,
                    losses=0,
                    games_won=0,
                    games_lost=0,
                )
            for i, t1 in enumerate(group_teams):
                for t2 in group_teams[i + 1 :]:
                    if t1.pk > t2.pk:
                        t1, t2 = t2, t1
                    Match.objects.create(
                        tournament=tournament,
                        round_name=f"Группа {name}",
                        round_index=1,
                        round_order=0,
                        is_consolation=False,
                        status=Match.MatchStatus.SCHEDULED,
                        deadline=start + delta,
                        tvd_group=group,
                        tvd_stage=TVD_STAGE_GROUP,
                        team1=t1,
                        team2=t2,
                        player1=t1.player1,
                        player2=t2.player1,
                    )
    else:
        participants = list(
            tournament.participants.exclude(is_bye=True).select_related("user")
        )
        n = len(participants)
        if n < 4:
            return False, "Нужно минимум 4 участника для формирования групп ТВД."
        sizes = calculate_group_structure(n)
        group_count = len(sizes)
        if group_count < 2 or group_count > 6:
            return False, f"Получено {group_count} групп; допустимо 2–6."
        distribution = serpentine_distribution(participants, group_count)
        names = "ABCDEF"[:group_count]
        for gi, (size, name) in enumerate(zip(sizes, names, strict=True)):
            group_players = distribution[gi][:size]
            if len(group_players) < 2:
                continue
            group = TVDGroup.objects.create(
                tournament=tournament,
                name=name,
                order=gi + 1,
                is_completed=False,
            )
            for seed, player in enumerate(group_players, 1):
                TVDGroupMember.objects.create(
                    group=group,
                    player=player,
                    seed=seed,
                    wins=0,
                    losses=0,
                    games_won=0,
                    games_lost=0,
                )
            for i, p1 in enumerate(group_players):
                for p2 in group_players[i + 1 :]:
                    if p1.pk > p2.pk:
                        p1, p2 = p2, p1
                    Match.objects.create(
                        tournament=tournament,
                        round_name=f"Группа {name}",
                        round_index=1,
                        round_order=0,
                        is_consolation=False,
                        status=Match.MatchStatus.SCHEDULED,
                        deadline=start + delta,
                        tvd_group=group,
                        tvd_stage=TVD_STAGE_GROUP,
                        player1=p1,
                        player2=p2,
                    )

    group_count = len(sizes)
    tournament.bracket_generated = True
    tournament.status = TournamentStatus.GROUP_STAGE
    tournament.save(update_fields=["bracket_generated", "status"])
    logger.info("TVD groups created for %s: %d groups", tournament.name, group_count)
    try:
        from apps.telegram_bot.notifications import notify_bracket_formed

        notify_bracket_formed(tournament, subtitle="Группы сформированы")
    except Exception as e:
        logger.exception("notify_bracket_formed for TVD %s: %s", tournament.slug, e)
    return True, f"Сформировано {group_count} групп, матчи группового этапа созданы."


def recalculate_group_standings(group: TVDGroup) -> None:
    """
    Пересчёт wins/losses/games по матчам группы и определение мест.
    Критерии: победы → разница геймов → личная встреча.
    Поддержка одиночного (player) и парного (team) ТВД.
    """
    is_doubles = group.members.filter(team_id__isnull=False).exists()
    if is_doubles:
        members = {
            m.team_id: m
            for m in group.members.select_related("team").filter(team_id__isnull=False)
        }
    else:
        members = {
            m.player_id: m
            for m in group.members.select_related("player").filter(
                player_id__isnull=False
            )
        }

    for m in members.values():
        m.wins = 0
        m.losses = 0
        m.games_won = 0
        m.games_lost = 0
        m.save(update_fields=["wins", "losses", "games_won", "games_lost"])

    if is_doubles:
        matches = Match.objects.filter(
            tvd_group=group,
            tvd_stage=TVD_STAGE_GROUP,
            status__in=(Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER),
        ).select_related("team1", "team2")
        for m in matches:
            if not m.winner_team_id or not m.team1_id or not m.team2_id:
                continue
            t1_id, t2_id = m.team1_id, m.team2_id
            if t1_id not in members or t2_id not in members:
                continue
            g1 = m.player1_set1 or 0
            g2 = m.player2_set1 or 0
            if m.player1_set2 is not None and m.player2_set2 is not None:
                g1 += m.player1_set2
                g2 += m.player2_set2
            if m.player1_set3 is not None and m.player2_set3 is not None:
                g1 += m.player1_set3
                g2 += m.player2_set3
            if m.winner_team_id == t1_id:
                members[t1_id].wins += 1
                members[t2_id].losses += 1
            else:
                members[t2_id].wins += 1
                members[t1_id].losses += 1
            members[t1_id].games_won += g1
            members[t1_id].games_lost += g2
            members[t2_id].games_won += g2
            members[t2_id].games_lost += g1
        for m in members.values():
            m.save(update_fields=["wins", "losses", "games_won", "games_lost"])
        h2h_winner: dict[tuple[int, int], int] = {}
        for m in matches:
            if m.winner_team_id and m.team1_id and m.team2_id:
                t1, t2 = m.team1_id, m.team2_id
                h2h_winner[(t1, t2)] = m.winner_team_id
                h2h_winner[(t2, t1)] = m.winner_team_id

        def _should_swap(a: TVDGroupMember, b: TVDGroupMember) -> bool:
            if b.wins != a.wins:
                return bool(b.wins > a.wins)
            diff_a = a.games_won - a.games_lost
            diff_b = b.games_won - b.games_lost
            if diff_b != diff_a:
                return bool(diff_b > diff_a)
            winner = h2h_winner.get((a.team_id, b.team_id))
            if winner == b.team_id:
                return True
            if winner == a.team_id:
                return False
            return bool((b.team_id or 0) < (a.team_id or 0))

        member_list = list(members.values())
    else:
        matches = Match.objects.filter(
            tvd_group=group,
            tvd_stage=TVD_STAGE_GROUP,
            status__in=(Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER),
        ).select_related("player1", "player2")
        for m in matches:
            if not m.winner_id or not m.player1_id or not m.player2_id:
                continue
            p1_id, p2_id = m.player1_id, m.player2_id
            if p1_id not in members or p2_id not in members:
                continue
            g1 = m.player1_set1 or 0
            g2 = m.player2_set1 or 0
            if m.player1_set2 is not None and m.player2_set2 is not None:
                g1 += m.player1_set2
                g2 += m.player2_set2
            if m.player1_set3 is not None and m.player2_set3 is not None:
                g1 += m.player1_set3
                g2 += m.player2_set3
            if m.winner_id == p1_id:
                members[p1_id].wins += 1
                members[p2_id].losses += 1
            else:
                members[p2_id].wins += 1
                members[p1_id].losses += 1
            members[p1_id].games_won += g1
            members[p1_id].games_lost += g2
            members[p2_id].games_won += g2
            members[p2_id].games_lost += g1

        for m in members.values():
            m.save(update_fields=["wins", "losses", "games_won", "games_lost"])

        h2h_winner = {}
        for m in matches:
            if m.winner_id and m.player1_id and m.player2_id:
                p1, p2 = m.player1_id, m.player2_id
                h2h_winner[(p1, p2)] = m.winner_id
                h2h_winner[(p2, p1)] = m.winner_id

        def _should_swap(a: TVDGroupMember, b: TVDGroupMember) -> bool:
            if b.wins != a.wins:
                return bool(b.wins > a.wins)
            diff_a = a.games_won - a.games_lost
            diff_b = b.games_won - b.games_lost
            if diff_b != diff_a:
                return bool(diff_b > diff_a)
            winner = h2h_winner.get((a.player_id, b.player_id))
            if winner == b.player_id:
                return True
            if winner == a.player_id:
                return False
            return bool((b.player_id or 0) < (a.player_id or 0))

        member_list = list(members.values())
        for m in members.values():
            m.save(update_fields=["wins", "losses", "games_won", "games_lost"])

    n = len(member_list)
    for i in range(n):
        for j in range(i + 1, n):
            if _should_swap(member_list[i], member_list[j]):
                member_list[i], member_list[j] = member_list[j], member_list[i]

    for place, m in enumerate(member_list, 1):
        m.final_place = place
        m.save(update_fields=["final_place"])

    all_played = group.matches.filter(
        tvd_stage=TVD_STAGE_GROUP,
        status__in=(Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER),
    ).count()
    expected = sum(1 for _ in group.members.all()) * (group.members.count() - 1) // 2
    if group.members.count() >= 2 and all_played >= expected:
        group.is_completed = True
        group.save(update_fields=["is_completed"])


def is_group_stage_complete(tournament: Tournament) -> bool:
    """Все ли группы ТВД завершены."""
    if not _is_tvd(tournament):
        return False
    return not tournament.tvd_groups.filter(is_completed=False).exists()


def get_playoff_format(group_count: int) -> dict[str, Any]:
    """
    Формат плей-офф по числу групп:
    2 группы: полуфинал + финал (4 игрока)
    3 группы: полуфинал + финал с 1 BYE (6)
    4 группы: 1/4 + 1/2 + финал (8)
    5 групп: сетка на 8 с BYE для лучших (10)
    6 групп: 1/6 + 1/3 + финальная группа из 3 (12)
    """
    if group_count == 2:
        return {
            "main_rounds": ["main_sf", "main_final"],
            "consolation_rounds": [],
            "slots_main": 4,
            "slots_consolation": 0,
            "byes": 0,
        }
    if group_count == 3:
        return {
            "main_rounds": ["main_sf", "main_final"],
            "consolation_rounds": ["consolation_sf", "consolation_final"],
            "slots_main": 6,
            "slots_consolation": 4,
            "byes": 1,
        }
    if group_count == 4:
        return {
            "main_rounds": ["main_qf", "main_sf", "main_final"],
            "consolation_rounds": ["consolation_sf", "consolation_final"],
            "slots_main": 8,
            "slots_consolation": 4,
            "byes": 0,
        }
    if group_count == 5:
        return {
            "main_rounds": ["main_qf", "main_sf", "main_final"],
            "consolation_rounds": ["consolation_sf", "consolation_final"],
            "slots_main": 8,
            "slots_consolation": 4,
            "byes": 2,
        }
    if group_count == 6:
        return {
            "main_rounds": ["main_qf", "main_sf", "main_final"],
            "consolation_rounds": ["consolation_sf", "consolation_final"],
            "slots_main": 12,
            "slots_consolation": 4,
            "byes": 0,
        }
    return {}


def _advancers_per_group(group_count: int) -> tuple[int, int]:
    """Сколько с мест 1, 2 и 3 выходят в основную сетку и в утешительную."""
    if group_count <= 2:
        return 2, 0
    if group_count == 3:
        return 2, 1
    return 2, 1


def _collect_place_123(tournament: Tournament) -> dict[str, Any] | None:
    """Собрать place_1, place_2, place_3 и служебные данные. None если не ТВД или группы не завершены."""
    if not _is_tvd(tournament) or not is_group_stage_complete(tournament):
        return None
    is_doubles = tournament.is_doubles()
    groups = list(
        tournament.tvd_groups.select_related("tournament")
        .prefetch_related("members__player", "members__team")
        .order_by("order")
    )
    group_count = len(groups)
    fmt = get_playoff_format(group_count)
    if not fmt:
        return None
    adv_main, adv_cons = _advancers_per_group(group_count)
    place_1: list[tuple[str, Player | TournamentTeam, TVDGroupMember]] = []
    place_2: list[tuple[str, Player | TournamentTeam]] = []
    place_3: list[tuple[str, Player | TournamentTeam, TVDGroupMember]] = []
    for g in groups:
        for m in g.members.order_by("final_place"):
            entity = m.team if (is_doubles and m.team_id) else m.player
            if not entity:
                continue
            if m.final_place == 1:
                place_1.append((g.name, entity, m))
            elif m.final_place == 2:
                place_2.append((g.name, entity))
            elif m.final_place == 3:
                place_3.append((g.name, entity, m))
    if group_count == 5:
        place_1.sort(key=lambda x: (-x[2].wins, -(x[2].games_won - x[2].games_lost)))
    start = _tournament_start_dt(tournament)
    days = getattr(tournament, "match_days_per_round", 7) or 7
    delta = timedelta(days=days)
    return {
        "groups": groups,
        "place_1": place_1,
        "place_2": place_2,
        "place_3": place_3,
        "group_count": group_count,
        "fmt": fmt,
        "start": start,
        "delta": delta,
        "is_doubles": is_doubles,
    }


def _create_tvd_round_robin(
    tournament: Tournament,
    entities: list[Player | TournamentTeam],
    is_consolation: bool,
    stage: str,
    round_name_prefix: str = "",
) -> tuple[bool, str]:
    """Создать круговые матчи (каждый с каждым) для списка сущностей. Возвращает (ok, msg)."""
    from .round_robin import _circle_schedule

    if len(entities) < 2:
        return False, "Нужно минимум 2 участника для круговой сетки."
    schedule = _circle_schedule(entities)
    start = _tournament_start_dt(tournament)
    days = getattr(tournament, "match_days_per_round", 7) or 7
    delta = timedelta(days=days)
    is_doubles = isinstance(entities[0], TournamentTeam)
    created = 0
    for round_idx, round_pairs in enumerate(schedule, 1):
        round_name = (
            f"{round_name_prefix}Тур {round_idx}"
            if round_name_prefix
            else f"Тур {round_idx}"
        )
        deadline = start + delta * round_idx
        for order, (e1, e2) in enumerate(round_pairs, 1):
            if e1 is None or e2 is None:
                continue
            if is_doubles:
                t1, t2 = (e1, e2) if e1.pk < e2.pk else (e2, e1)
                if Match.objects.filter(
                    tournament=tournament,
                    round_index=round_idx,
                    tvd_stage=stage,
                    team1=t1,
                    team2=t2,
                ).exists():
                    continue
                Match.objects.create(
                    tournament=tournament,
                    round_name=round_name,
                    round_index=round_idx,
                    round_order=order,
                    is_consolation=is_consolation,
                    status=Match.MatchStatus.SCHEDULED,
                    deadline=deadline,
                    tvd_stage=stage,
                    team1=t1,
                    team2=t2,
                    player1=t1.player1,
                    player2=t2.player1,
                )
            else:
                p1, p2 = (e1, e2) if e1.pk < e2.pk else (e2, e1)
                if Match.objects.filter(
                    tournament=tournament,
                    round_index=round_idx,
                    tvd_stage=stage,
                    player1=p1,
                    player2=p2,
                ).exists():
                    continue
                Match.objects.create(
                    tournament=tournament,
                    round_name=round_name,
                    round_index=round_idx,
                    round_order=order,
                    is_consolation=is_consolation,
                    status=Match.MatchStatus.SCHEDULED,
                    deadline=deadline,
                    tvd_stage=stage,
                    player1=p1,
                    player2=p2,
                )
            created += 1
    return True, f"Круговая сетка создана ({created} матчей)."


def generate_main_bracket(
    tournament: Tournament, bracket_format: str = "olympic"
) -> tuple[bool, str]:
    """
    Создать только основную сетку плей-офф.
    bracket_format: "olympic" (нокаут) или "circular" (круговой).
    """
    if not _is_tvd(tournament):
        return False, "Турнир не в формате ТВД."
    if tournament.status == TournamentStatus.PLAYOFFS:
        return False, "Плей-офф уже сформирован."
    data = _collect_place_123(tournament)
    if not data:
        return False, "Не все группы завершены или неподдерживаемое число групп."
    place_1 = data["place_1"]
    place_2 = data["place_2"]
    if bracket_format == "circular":
        entities = [x[1] for x in place_1] + [x[1] for x in place_2]
        ok, msg = _create_tvd_round_robin(
            tournament, entities, is_consolation=False, stage=TVD_STAGE_MAIN_RR
        )
        if not ok:
            return ok, msg
        tournament.status = TournamentStatus.PLAYOFFS
        tournament.save(update_fields=["status"])
        logger.info("TVD main round-robin created for %s", tournament.name)
        try:
            from apps.telegram_bot.notifications import notify_bracket_formed

            notify_bracket_formed(tournament, subtitle="Основная сетка сформирована")
        except Exception as e:
            logger.exception("notify_bracket_formed %s: %s", tournament.slug, e)
        return True, "Основная сетка (круговой формат) создана."
    # olympic: delegate to generate_playoffs with skip_consolation
    ok, msg = generate_playoffs(tournament, skip_consolation=True)
    return ok, msg or "Основная сетка создана."


def generate_consolation_bracket(
    tournament: Tournament, bracket_format: str = "olympic"
) -> tuple[bool, str]:
    """
    Создать только утешительную сетку.
    bracket_format: "olympic" (нокаут) или "circular" (круговой).
    Разрешается при любом числе третьих мест (2–6).
    """
    if not _is_tvd(tournament):
        return False, "Турнир не в формате ТВД."
    data = _collect_place_123(tournament)
    if not data:
        return False, "Не все группы завершены или неподдерживаемое число групп."
    place_3 = data["place_3"]
    if len(place_3) < 2:
        return False, "Нужно минимум 2 участника (3-и места) для утешительной сетки."
    start = data["start"]
    delta = data["delta"]
    if bracket_format == "circular":
        entities = [x[1] for x in place_3]
        ok, msg = _create_tvd_round_robin(
            tournament, entities, is_consolation=True, stage=TVD_STAGE_CONSOLATION_RR
        )
        if not ok:
            return ok, msg
        logger.info("TVD consolation round-robin created for %s", tournament.name)
        try:
            from apps.telegram_bot.notifications import notify_bracket_formed

            notify_bracket_formed(
                tournament, subtitle="Утешительная сетка сформирована"
            )
        except Exception as e:
            logger.exception("notify_bracket_formed %s: %s", tournament.slug, e)
        return True, "Утешительная сетка (круговой формат) создана."
    # olympic: create knockout consolation for 2–6 third places
    ok, msg = _create_olympic_consolation(tournament, place_3, start, delta)
    return ok, msg


def _create_olympic_consolation(
    tournament: Tournament,
    place_3: list[tuple[str, Player | TournamentTeam, TVDGroupMember]],
    start: datetime,
    delta: timedelta,
) -> tuple[bool, str]:
    """Создать олимпийскую (нокаут) утешительную сетку для списка третьих мест (2–6 участников)."""
    n = len(place_3)
    if n < 2:
        return False, "Нужно минимум 2 участника для утешительной сетки."
    sorted_place_3 = sorted(
        place_3, key=lambda x: (-x[2].wins, -(x[2].games_won - x[2].games_lost))
    )
    if n == 2:
        cons_pairs = [(sorted_place_3[0][1], sorted_place_3[1][1])]
        _create_tvd_matches(
            tournament,
            TVD_STAGE_CONSOLATION_FINAL,
            cons_pairs,
            start + delta * 2,
            is_consolation=True,
            round_name_override="Финал утешительной",
        )
    elif n == 3:
        # 1 BYE, 2 в полуфинал; один матч R1
        bye_entity = sorted_place_3[0][1]
        cons_r1_pairs = [(sorted_place_3[1][1], sorted_place_3[2][1])]
        cons_r1 = _create_tvd_matches(
            tournament,
            TVD_STAGE_CONSOLATION_SF,
            cons_r1_pairs,
            start + delta,
            is_consolation=True,
        )
        cons_final = Match.objects.create(
            tournament=tournament,
            round_name="Финал утешительной",
            round_index=3,
            round_order=100,
            is_consolation=True,
            status=Match.MatchStatus.SCHEDULED,
            deadline=start + delta * 3,
            tvd_stage=TVD_STAGE_CONSOLATION_FINAL,
        )
        _fill_tvd_slot(cons_final, bye_entity, True)
        cons_r1[0].next_match = cons_final
        cons_r1[0].save(update_fields=["next_match"])
    elif n == 4:
        cons_pairs = [
            (sorted_place_3[0][1], sorted_place_3[3][1]),
            (sorted_place_3[1][1], sorted_place_3[2][1]),
        ]
        cons_sf = _create_tvd_matches(
            tournament,
            TVD_STAGE_CONSOLATION_SF,
            cons_pairs,
            start + delta * 2,
            is_consolation=True,
        )
        cons_final = Match.objects.create(
            tournament=tournament,
            round_name="Финал утешительной",
            round_index=3,
            round_order=100,
            is_consolation=True,
            status=Match.MatchStatus.SCHEDULED,
            deadline=start + delta * 3,
            tvd_stage=TVD_STAGE_CONSOLATION_FINAL,
        )
        for m in cons_sf:
            m.next_match = cons_final
            m.save(update_fields=["next_match"])
    elif n == 5:
        bye1, bye2 = sorted_place_3[0][1], sorted_place_3[1][1]
        cons_r1_pairs = [(sorted_place_3[2][1], sorted_place_3[3][1])]
        cons_r1 = _create_tvd_matches(
            tournament,
            TVD_STAGE_CONSOLATION_SF,
            cons_r1_pairs,
            start + delta,
            is_consolation=True,
        )
        cons_sf1 = Match.objects.create(
            tournament=tournament,
            round_name="1/2 утешительной",
            round_index=2,
            round_order=101,
            is_consolation=True,
            status=Match.MatchStatus.SCHEDULED,
            deadline=start + delta * 2,
            tvd_stage=TVD_STAGE_CONSOLATION_SF,
        )
        cons_sf2 = Match.objects.create(
            tournament=tournament,
            round_name="1/2 утешительной",
            round_index=2,
            round_order=102,
            is_consolation=True,
            status=Match.MatchStatus.SCHEDULED,
            deadline=start + delta * 2,
            tvd_stage=TVD_STAGE_CONSOLATION_SF,
        )
        _fill_tvd_slot(cons_sf1, bye1, True)
        _fill_tvd_slot(cons_sf2, bye2, True)
        _fill_tvd_slot(cons_sf2, sorted_place_3[4][1], False)
        cons_r1[0].next_match = cons_sf1
        cons_r1[0].save(update_fields=["next_match"])
        cons_final = Match.objects.create(
            tournament=tournament,
            round_name="Финал утешительной",
            round_index=3,
            round_order=100,
            is_consolation=True,
            status=Match.MatchStatus.SCHEDULED,
            deadline=start + delta * 3,
            tvd_stage=TVD_STAGE_CONSOLATION_FINAL,
        )
        cons_sf1.next_match = cons_final
        cons_sf2.next_match = cons_final
        cons_sf1.save(update_fields=["next_match"])
        cons_sf2.save(update_fields=["next_match"])
    else:
        # n == 6: 2 BYE, 4 in R1
        bye1, bye2 = sorted_place_3[0][1], sorted_place_3[1][1]
        cons_r1_pairs = [
            (sorted_place_3[2][1], sorted_place_3[5][1]),
            (sorted_place_3[3][1], sorted_place_3[4][1]),
        ]
        cons_r1 = _create_tvd_matches(
            tournament,
            TVD_STAGE_CONSOLATION_SF,
            cons_r1_pairs,
            start + delta,
            is_consolation=True,
        )
        cons_sf1 = Match.objects.create(
            tournament=tournament,
            round_name="1/2 утешительной",
            round_index=2,
            round_order=101,
            is_consolation=True,
            status=Match.MatchStatus.SCHEDULED,
            deadline=start + delta * 2,
            tvd_stage=TVD_STAGE_CONSOLATION_SF,
        )
        cons_sf2 = Match.objects.create(
            tournament=tournament,
            round_name="1/2 утешительной",
            round_index=2,
            round_order=102,
            is_consolation=True,
            status=Match.MatchStatus.SCHEDULED,
            deadline=start + delta * 2,
            tvd_stage=TVD_STAGE_CONSOLATION_SF,
        )
        _fill_tvd_slot(cons_sf1, bye1, True)
        _fill_tvd_slot(cons_sf2, bye2, True)
        cons_r1[0].next_match = cons_sf1
        cons_r1[1].next_match = cons_sf2
        cons_r1[0].save(update_fields=["next_match"])
        cons_r1[1].save(update_fields=["next_match"])
        cons_final = Match.objects.create(
            tournament=tournament,
            round_name="Финал утешительной",
            round_index=3,
            round_order=100,
            is_consolation=True,
            status=Match.MatchStatus.SCHEDULED,
            deadline=start + delta * 3,
            tvd_stage=TVD_STAGE_CONSOLATION_FINAL,
        )
        cons_sf1.next_match = cons_final
        cons_sf2.next_match = cons_final
        cons_sf1.save(update_fields=["next_match"])
        cons_sf2.save(update_fields=["next_match"])
    logger.info(
        "TVD olympic consolation created for %s (%d third places)", tournament.name, n
    )
    try:
        from apps.telegram_bot.notifications import notify_bracket_formed

        notify_bracket_formed(tournament, subtitle="Утешительная сетка сформирована")
    except Exception as e:
        logger.exception("notify_bracket_formed %s: %s", tournament.slug, e)
    return True, "Утешительная сетка (олимпийская) создана."


def generate_playoffs(
    tournament: Tournament, skip_consolation: bool = False
) -> tuple[bool, str]:
    """
    Сформировать плей-офф и утешительную сетку.
    Игроки из одной группы не встречаются в первом раунде.
    skip_consolation: если True, создаётся только основная сетка (для generate_main_bracket).
    """
    if not _is_tvd(tournament):
        return False, "Турнир не в формате ТВД."
    if tournament.status == TournamentStatus.PLAYOFFS:
        return False, "Плей-офф уже сформирован."
    if not is_group_stage_complete(tournament):
        return False, "Не все группы завершены."

    is_doubles = tournament.is_doubles()
    groups = list(
        tournament.tvd_groups.select_related("tournament")
        .prefetch_related("members__player", "members__team")
        .order_by("order")
    )
    group_count = len(groups)
    fmt = get_playoff_format(group_count)
    if not fmt:
        return False, f"Неподдерживаемое число групп: {group_count}."

    adv_main, adv_cons = _advancers_per_group(group_count)
    place_1: list[tuple[str, Player | TournamentTeam, TVDGroupMember]] = []
    place_2: list[tuple[str, Player | TournamentTeam]] = []
    place_3: list[tuple[str, Player | TournamentTeam, TVDGroupMember]] = []
    for g in groups:
        for m in g.members.order_by("final_place"):
            entity = m.team if (is_doubles and m.team_id) else m.player
            if not entity:
                continue
            if m.final_place == 1:
                place_1.append((g.name, entity, m))
            elif m.final_place == 2:
                place_2.append((g.name, entity))
            elif m.final_place == 3:
                place_3.append((g.name, entity, m))
    if group_count == 5:
        place_1.sort(key=lambda x: (-x[2].wins, -(x[2].games_won - x[2].games_lost)))

    start = _tournament_start_dt(tournament)
    days = getattr(tournament, "match_days_per_round", 7) or 7
    delta = timedelta(days=days)

    # Main bracket first round: pair 1st vs 2nd from different groups
    main_round1_stage = fmt["main_rounds"][0]
    pairs: list[tuple[Player | TournamentTeam, Player | TournamentTeam]] = []
    if group_count == 2:
        # 1A vs 2B, 1B vs 2A
        for i, (_, p1, _) in enumerate(place_1):
            p2 = place_2[1 - i][1]
            pairs.append((p1, p2))
    elif group_count == 3:
        # 3 pairs: 1A-2B, 1B-2C, 1C-2A (no same group)
        for i in range(3):
            p1 = place_1[i][1]
            p2 = place_2[(i + 1) % 3][1]
            pairs.append((p1, p2))
    elif group_count == 4:
        # QF: 1A-2D, 1C-2B, 1B-2C, 1D-2A (plan diagram)
        pairs = [
            (place_1[0][1], place_2[3][1]),
            (place_1[2][1], place_2[1][1]),
            (place_1[1][1], place_2[2][1]),
            (place_1[3][1], place_2[0][1]),
        ]
    elif group_count == 5:
        # 2 byes for two strongest 1st (place_1 sorted); 4 QF matches.
        # place_2 remains in original group order (A, B, C, D, E -> indices 0..4).
        # Пары: place_1[2] vs place_2[X], place_1[3] vs place_2[Y], place_1[4] vs place_2[Z], place_2[W] vs place_2[V]
        # Проверяем, чтобы игроки из одной группы не встречались. place_1[i][0] = group name.
        def _find_opponent_5(p1_group: str, p2_list: list, exclude_groups: set) -> int:
            """Найти индекс в place_2 для оппонента, избегая p1_group и exclude_groups."""
            for idx, (g, _p) in enumerate(p2_list):
                if g not in exclude_groups and g != p1_group:
                    return idx
            # fallback: любой, но не p1_group
            for idx, (g, _p) in enumerate(p2_list):
                if g != p1_group and idx not in {
                    i for i, (gg, _) in enumerate(p2_list) if gg in exclude_groups
                }:
                    return idx
            return 0  # should not happen

        used_p2_indices: set[int] = set()
        qf_pairs_5: list[tuple[Player, Player]] = []
        # QF1: place_1[2] vs ?
        p1_2_group = place_1[2][0]
        opp1_idx = _find_opponent_5(p1_2_group, place_2, set())
        qf_pairs_5.append((place_1[2][1], place_2[opp1_idx][1]))
        used_p2_indices.add(opp1_idx)
        # QF2: place_1[3] vs ?
        p1_3_group = place_1[3][0]
        opp2_idx = _find_opponent_5(
            p1_3_group, place_2, {place_2[i][0] for i in used_p2_indices}
        )
        # fallback if same
        if place_2[opp2_idx][0] == p1_3_group or opp2_idx in used_p2_indices:
            for i in range(len(place_2)):
                if i not in used_p2_indices and place_2[i][0] != p1_3_group:
                    opp2_idx = i
                    break
        qf_pairs_5.append((place_1[3][1], place_2[opp2_idx][1]))
        used_p2_indices.add(opp2_idx)
        # QF3: place_1[4] vs ?
        p1_4_group = place_1[4][0]
        opp3_idx = -1
        for i in range(len(place_2)):
            if i not in used_p2_indices and place_2[i][0] != p1_4_group:
                opp3_idx = i
                break
        if opp3_idx == -1:
            # any unused
            opp3_idx = [i for i in range(len(place_2)) if i not in used_p2_indices][0]
        qf_pairs_5.append((place_1[4][1], place_2[opp3_idx][1]))
        used_p2_indices.add(opp3_idx)
        # QF4: two remaining 2nd places
        remaining_p2 = [i for i in range(len(place_2)) if i not in used_p2_indices]
        qf_pairs_5.append((place_2[remaining_p2[0]][1], place_2[remaining_p2[1]][1]))
        qf_pairs = qf_pairs_5
        qf_matches = _create_tvd_matches(
            tournament,
            main_round1_stage,
            qf_pairs,
            start + delta,
            is_consolation=False,
        )
        sf1 = Match.objects.create(
            tournament=tournament,
            round_name="1/2 финала",
            round_index=2,
            round_order=1,
            is_consolation=False,
            status=Match.MatchStatus.SCHEDULED,
            deadline=start + delta * 2,
            tvd_stage=TVD_STAGE_MAIN_SF,
        )
        sf2 = Match.objects.create(
            tournament=tournament,
            round_name="1/2 финала",
            round_index=2,
            round_order=2,
            is_consolation=False,
            status=Match.MatchStatus.SCHEDULED,
            deadline=start + delta * 2,
            tvd_stage=TVD_STAGE_MAIN_SF,
        )
        final = Match.objects.create(
            tournament=tournament,
            round_name="Финал",
            round_index=3,
            round_order=1,
            is_consolation=False,
            status=Match.MatchStatus.SCHEDULED,
            deadline=start + delta * 3,
            tvd_stage=TVD_STAGE_MAIN_FINAL,
        )
        qf_matches[0].next_match = sf1
        qf_matches[1].next_match = sf1
        qf_matches[2].next_match = sf2
        qf_matches[3].next_match = sf2
        for m in qf_matches:
            m.save(update_fields=["next_match"])
        sf1.next_match = final
        sf2.next_match = final
        _fill_tvd_slot(sf1, place_1[0][1], True)
        _fill_tvd_slot(sf2, place_1[1][1], True)
        sf1.save(update_fields=["next_match"])
        sf2.save(update_fields=["next_match"])
        if not skip_consolation:
            if len(place_3) >= 4:
                cons_pairs = [
                    (place_3[0][1], place_3[3][1]),
                    (place_3[1][1], place_3[2][1]),
                ]
            else:
                cons_pairs = (
                    [(place_3[0][1], place_3[1][1])] if len(place_3) >= 2 else []
                )
            if cons_pairs:
                cons_sf = _create_tvd_matches(
                    tournament,
                    TVD_STAGE_CONSOLATION_SF,
                    cons_pairs,
                    start + delta * 2,
                    is_consolation=True,
                )
                cons_final = Match.objects.create(
                    tournament=tournament,
                    round_name="Финал утешительной",
                    round_index=3,
                    round_order=100,
                    is_consolation=True,
                    status=Match.MatchStatus.SCHEDULED,
                    deadline=start + delta * 3,
                    tvd_stage=TVD_STAGE_CONSOLATION_FINAL,
                )
                for m in cons_sf:
                    m.next_match = cons_final
                    m.save(update_fields=["next_match"])
        tournament.status = TournamentStatus.PLAYOFFS
        tournament.save(update_fields=["status"])
        logger.info("TVD playoffs (5 groups) created for %s", tournament.name)
        try:
            from apps.telegram_bot.notifications import notify_bracket_formed

            notify_bracket_formed(tournament, subtitle="Плей-офф сформирован")
        except Exception as e:
            logger.exception(
                "notify_bracket_formed for TVD playoffs %s: %s", tournament.slug, e
            )
        if skip_consolation:
            return True, "Основная сетка создана (5 групп)."
        return True, "Плей-офф и утешительная сетка созданы (5 групп)."
    elif group_count == 6:
        # 6 групп: 12 в основной сетке. 6 матчей 1/6 (все пары 1-е место vs 2-е из другой группы), затем 3 полуфинала, финал + матч за 3-е место.
        r1_pairs = [
            (place_1[0][1], place_2[1][1]),
            (place_1[1][1], place_2[2][1]),
            (place_1[2][1], place_2[3][1]),
            (place_1[3][1], place_2[4][1]),
            (place_1[4][1], place_2[5][1]),
            (place_1[5][1], place_2[0][1]),
        ]
        main_matches = _create_tvd_matches(
            tournament,
            main_round1_stage,
            r1_pairs,
            start + delta,
            is_consolation=False,
            round_name_override="1/6 финала",
        )
        sf1 = Match.objects.create(
            tournament=tournament,
            round_name="1/3 финала",
            round_index=2,
            round_order=1,
            is_consolation=False,
            status=Match.MatchStatus.SCHEDULED,
            deadline=start + delta * 2,
            tvd_stage=TVD_STAGE_MAIN_SF,
        )
        sf2 = Match.objects.create(
            tournament=tournament,
            round_name="1/3 финала",
            round_index=2,
            round_order=2,
            is_consolation=False,
            status=Match.MatchStatus.SCHEDULED,
            deadline=start + delta * 2,
            tvd_stage=TVD_STAGE_MAIN_SF,
        )
        sf3 = Match.objects.create(
            tournament=tournament,
            round_name="1/3 финала",
            round_index=2,
            round_order=3,
            is_consolation=False,
            status=Match.MatchStatus.SCHEDULED,
            deadline=start + delta * 2,
            tvd_stage=TVD_STAGE_MAIN_SF,
        )
        final = Match.objects.create(
            tournament=tournament,
            round_name="Финал",
            round_index=3,
            round_order=1,
            is_consolation=False,
            status=Match.MatchStatus.SCHEDULED,
            deadline=start + delta * 3,
            tvd_stage=TVD_STAGE_MAIN_FINAL,
        )
        third_place = Match.objects.create(
            tournament=tournament,
            round_name="Матч за 3-е место",
            round_index=3,
            round_order=2,
            is_consolation=False,
            status=Match.MatchStatus.SCHEDULED,
            deadline=start + delta * 3,
            tvd_stage=TVD_STAGE_THIRD_PLACE,
        )
        main_matches[0].next_match = sf1
        main_matches[1].next_match = sf1
        main_matches[2].next_match = sf2
        main_matches[3].next_match = sf2
        main_matches[4].next_match = sf3
        main_matches[5].next_match = sf3
        for m in main_matches:
            m.save(update_fields=["next_match"])
        sf1.next_match = final
        sf2.next_match = final
        sf3.next_match = third_place
        sf1.save(update_fields=["next_match"])
        sf2.save(update_fields=["next_match"])
        sf3.save(update_fields=["next_match"])
        # Победитель SF3 → third_place.player1 (advance_winner); проигравший финала → third_place.player2 (advance_winner при MAIN_FINAL).
        # SF1/SF2 проигравшие получают места 5–6 через _assign_tvd_places_5_onwards.
        tournament.status = TournamentStatus.PLAYOFFS
        tournament.save(update_fields=["status"])
        if skip_consolation:
            logger.info(
                "TVD playoffs (6 groups, main only) created for %s", tournament.name
            )
            try:
                from apps.telegram_bot.notifications import notify_bracket_formed

                notify_bracket_formed(tournament, subtitle="Плей-офф сформирован")
            except Exception as e:
                logger.exception(
                    "notify_bracket_formed for TVD playoffs %s: %s", tournament.slug, e
                )
            return True, "Основная сетка создана (6 групп)."
        # Consolation: 6 third-places. Формат: 2 BYE (лучшие) → SF, 2 матча R1 (4 остальных) → SF → финал.
        # Утешительная сетка только если во всех 6 группах есть 3-е место (минимум 3 участника в группе).
        if len(place_3) >= 6:
            sorted_place_3 = sorted(
                place_3, key=lambda x: (-x[2].wins, -(x[2].games_won - x[2].games_lost))
            )
            # BYE: первые два (сильнейшие) идут сразу в SF
            bye1 = sorted_place_3[0][1]
            bye2 = sorted_place_3[1][1]
            # R1: остальные 4
            cons_r1_pairs = [
                (sorted_place_3[2][1], sorted_place_3[5][1]),
                (sorted_place_3[3][1], sorted_place_3[4][1]),
            ]
            cons_r1_matches = _create_tvd_matches(
                tournament,
                TVD_STAGE_CONSOLATION_SF,  # reuse stage for R1 of consolation
                cons_r1_pairs,
                start + delta,
                is_consolation=True,
            )
            # SF: bye1 vs winner(cons_r1_matches[0]), bye2 vs winner(cons_r1_matches[1])
            cons_sf1 = Match.objects.create(
                tournament=tournament,
                round_name="1/2 утешительной",
                round_index=2,
                round_order=101,
                is_consolation=True,
                status=Match.MatchStatus.SCHEDULED,
                deadline=start + delta * 2,
                tvd_stage=TVD_STAGE_CONSOLATION_SF,
            )
            cons_sf2 = Match.objects.create(
                tournament=tournament,
                round_name="1/2 утешительной",
                round_index=2,
                round_order=102,
                is_consolation=True,
                status=Match.MatchStatus.SCHEDULED,
                deadline=start + delta * 2,
                tvd_stage=TVD_STAGE_CONSOLATION_SF,
            )
            _fill_tvd_slot(cons_sf1, bye1, True)
            _fill_tvd_slot(cons_sf2, bye2, True)
            cons_r1_matches[0].next_match = cons_sf1
            cons_r1_matches[1].next_match = cons_sf2
            cons_r1_matches[0].save(update_fields=["next_match"])
            cons_r1_matches[1].save(update_fields=["next_match"])
            cons_final = Match.objects.create(
                tournament=tournament,
                round_name="Финал утешительной",
                round_index=3,
                round_order=100,
                is_consolation=True,
                status=Match.MatchStatus.SCHEDULED,
                deadline=start + delta * 3,
                tvd_stage=TVD_STAGE_CONSOLATION_FINAL,
            )
            cons_sf1.next_match = cons_final
            cons_sf2.next_match = cons_final
            cons_sf1.save(update_fields=["next_match"])
            cons_sf2.save(update_fields=["next_match"])
            logger.info("TVD playoffs (6 groups) created for %s", tournament.name)
            try:
                from apps.telegram_bot.notifications import notify_bracket_formed

                notify_bracket_formed(tournament, subtitle="Плей-офф сформирован")
            except Exception as e:
                logger.exception(
                    "notify_bracket_formed for TVD playoffs %s: %s", tournament.slug, e
                )
            return True, "Плей-офф и утешительная сетка созданы (6 групп)."
        logger.info(
            "TVD playoffs (6 groups, no consolation) created for %s", tournament.name
        )
        try:
            from apps.telegram_bot.notifications import notify_bracket_formed

            notify_bracket_formed(tournament, subtitle="Плей-офф сформирован")
        except Exception as e:
            logger.exception(
                "notify_bracket_formed for TVD playoffs %s: %s", tournament.slug, e
            )
        return (
            True,
            "Плей-офф создан (6 групп). Утешительная сетка не создана: не во всех группах есть 3-е место.",
        )

    # 2, 3, 4 groups
    if group_count == 3:
        # 3 группы: круговая мини-сетка — победители групп играют круг за 1–3 места, вторые места — круг за 4–6 места.
        entities_1_3 = [place_1[i][1] for i in range(3)]
        entities_4_6 = [place_2[i][1] for i in range(3)]
        ok1, msg1 = _create_tvd_round_robin(
            tournament,
            entities_1_3,
            is_consolation=False,
            stage=TVD_STAGE_MAIN_RR_1_3,
            round_name_prefix="1–3 места: ",
        )
        if not ok1:
            return False, msg1 or "Не удалось создать круг за 1–3 места."
        ok2, msg2 = _create_tvd_round_robin(
            tournament,
            entities_4_6,
            is_consolation=False,
            stage=TVD_STAGE_MAIN_RR_4_6,
            round_name_prefix="4–6 места: ",
        )
        if not ok2:
            return False, msg2 or "Не удалось создать круг за 4–6 места."
    else:
        main_matches = _create_tvd_matches(
            tournament,
            main_round1_stage,
            pairs,
            start + delta,
            is_consolation=False,
        )
        round_names = {
            "main_sf": "1/2 финала",
            "main_final": "Финал",
            "main_qf": "1/4 финала",
        }
        prev_round = main_matches
        for ri, stage in enumerate(fmt["main_rounds"][1:], 2):
            num_prev = len(prev_round)
            num_matches = num_prev // 2
            next_round: list[Match] = []
            round_name = round_names.get(stage, stage)
            for o in range(num_matches):
                parent = Match.objects.create(
                    tournament=tournament,
                    round_name=round_name,
                    round_index=ri,
                    round_order=o + 1,
                    is_consolation=False,
                    status=Match.MatchStatus.SCHEDULED,
                    deadline=start + delta * ri,
                    tvd_stage=stage,
                )
                next_round.append(parent)
                prev_round[o * 2].next_match = parent
                prev_round[o * 2 + 1].next_match = parent
                prev_round[o * 2].save(update_fields=["next_match"])
                prev_round[o * 2 + 1].save(update_fields=["next_match"])
            prev_round = next_round

    if not skip_consolation and fmt["slots_consolation"] >= 4 and place_3:
        if group_count == 3 and len(place_3) == 3:
            # 3 группы: один матч за 3-е место (двое играют; победитель — 3-е, проигравший — 4-е).
            # Третий игрок с 3-го места в группе получает 5-е место без матча (см. правила ТВД).
            cons_third = Match.objects.create(
                tournament=tournament,
                round_name="Матч за 3-е место",
                round_index=3,
                round_order=1,
                is_consolation=True,
                status=Match.MatchStatus.SCHEDULED,
                deadline=start + delta * 3,
                tvd_stage=TVD_STAGE_CONSOLATION_FINAL,
            )
            _fill_tvd_slot(cons_third, place_3[0][1], True)
            _fill_tvd_slot(cons_third, place_3[1][1], False)
        else:
            cons_pairs = []
            if len(place_3) >= 4:
                cons_pairs = [
                    (place_3[0][1], place_3[3][1]),
                    (place_3[1][1], place_3[2][1]),
                ]
            else:
                cons_pairs = (
                    [(place_3[0][1], place_3[1][1])] if len(place_3) >= 2 else []
                )
            if cons_pairs:
                cons_sf = _create_tvd_matches(
                    tournament,
                    TVD_STAGE_CONSOLATION_SF,
                    cons_pairs,
                    start + delta * 2,
                    is_consolation=True,
                )
                cons_final = Match.objects.create(
                    tournament=tournament,
                    round_name="Финал утешительной",
                    round_index=3,
                    round_order=100,
                    is_consolation=True,
                    status=Match.MatchStatus.SCHEDULED,
                    deadline=start + delta * 3,
                    tvd_stage=TVD_STAGE_CONSOLATION_FINAL,
                )
                for m in cons_sf:
                    m.next_match = cons_final
                    m.save(update_fields=["next_match"])

    tournament.status = TournamentStatus.PLAYOFFS
    tournament.save(update_fields=["status"])
    logger.info("TVD playoffs created for %s: %d groups", tournament.name, group_count)
    try:
        from apps.telegram_bot.notifications import notify_bracket_formed

        notify_bracket_formed(tournament, subtitle="Плей-офф сформирован")
    except Exception as e:
        logger.exception(
            "notify_bracket_formed for TVD playoffs %s: %s", tournament.slug, e
        )
    if skip_consolation:
        return True, "Основная сетка создана."
    return True, "Плей-офф и утешительная сетка созданы."


def _fill_tvd_slot(
    match: Match,
    entity: Player | TournamentTeam,
    slot1: bool,
    update_fields: list[str] | None = None,
) -> None:
    """Заполнить слот матча (player1/player2 или team1/team2) в зависимости от типа entity."""
    if isinstance(entity, TournamentTeam):
        if slot1:
            match.team1 = entity
            match.player1 = entity.player1
        else:
            match.team2 = entity
            match.player2 = entity.player1
        fields = ["team1", "team2", "player1", "player2"]
    else:
        if slot1:
            match.player1 = entity
        else:
            match.player2 = entity
        fields = ["player1", "player2"]
    match.save(update_fields=update_fields or fields)


def _create_tvd_matches(
    tournament: Tournament,
    stage: str,
    pairs: list[tuple[Player | TournamentTeam, Player | TournamentTeam]],
    deadline,
    is_consolation: bool,
    round_name_override: str | None = None,
) -> list[Match]:
    created = []
    if round_name_override:
        round_name = round_name_override
    elif stage == TVD_STAGE_MAIN_QF:
        round_name = "1/6 финала" if len(pairs) == 6 else "1/4 финала"
    elif stage == TVD_STAGE_CONSOLATION_SF:
        round_name = "1/2 утешит."
    else:
        round_name = "1/2 финала"
    for o, (e1, e2) in enumerate(pairs):
        if isinstance(e1, TournamentTeam):
            m = Match.objects.create(
                tournament=tournament,
                round_name=round_name,
                round_index=2 if "sf" in stage else 1,
                round_order=o + 1,
                is_consolation=is_consolation,
                status=Match.MatchStatus.SCHEDULED,
                deadline=deadline,
                tvd_stage=stage,
                team1=e1,
                team2=e2,
                player1=e1.player1,
                player2=e2.player1,
            )
        else:
            m = Match.objects.create(
                tournament=tournament,
                round_name=round_name,
                round_index=2 if "sf" in stage else 1,
                round_order=o + 1,
                is_consolation=is_consolation,
                status=Match.MatchStatus.SCHEDULED,
                deadline=deadline,
                tvd_stage=stage,
                player1=e1,
                player2=e2,
            )
        created.append(m)
    return created


def advance_winner(match: Match) -> Match | None:
    """
    Продвижение победителя в следующий матч (next_match).
    Заполняет player1/player2 или team1/team2 в parent по round_order.
    Для 6 групп: проигравший в полуфинале основной сетки идёт в матч за 3-е место.
    """
    if not match.tournament_id or not _is_tvd(match.tournament):
        return None
    winner_entity: Player | TournamentTeam | None = (
        match.winner_team if getattr(match, "winner_team_id", None) else match.winner
    )
    if not winner_entity:
        return None
    if isinstance(winner_entity, Player) and getattr(winner_entity, "is_bye", False):
        return None
    if isinstance(winner_entity, TournamentTeam) and getattr(
        winner_entity.player1, "is_bye", False
    ):
        return None
    parent = match.next_match
    if parent is not None:
        fill_slot1 = match.round_order % 2 == 1
        _fill_tvd_slot(parent, winner_entity, fill_slot1)
        both_filled = (parent.team1_id and parent.team2_id) or (
            parent.player1_id and parent.player2_id
        )
        if both_filled:
            parent.status = Match.MatchStatus.SCHEDULED
            parent.save(update_fields=["status"])

    # При 6 группах: в матч за 3-е место попадают победитель SF3 (через next_match) и проигравший в финале
    if match.tvd_stage == TVD_STAGE_MAIN_FINAL:
        third_place = match.tournament.matches.filter(
            tvd_stage=TVD_STAGE_THIRD_PLACE, is_consolation=False
        ).first()
        if third_place:
            if match.winner_team_id:
                loser_entity = (
                    match.team2
                    if match.winner_team_id == match.team1_id
                    else match.team1
                )
            else:
                loser_entity = (
                    match.player2
                    if match.winner_id == match.player1_id
                    else match.player1
                )
            if loser_entity and not getattr(
                getattr(loser_entity, "player1", loser_entity), "is_bye", False
            ):
                slot1 = not (third_place.team1_id or third_place.player1_id)
                _fill_tvd_slot(third_place, loser_entity, slot1)
                if (third_place.team1_id and third_place.team2_id) or (
                    third_place.player1_id and third_place.player2_id
                ):
                    third_place.status = Match.MatchStatus.SCHEDULED
                    third_place.save(update_fields=["status"])

    return cast(Match | None, parent)


def _set_tvd_place_for_entity(
    tournament: Tournament,
    entity: Player | TournamentTeam,
    place: int,
    pts: int,
    round_eliminated: str,
    is_consolation: bool,
    match: Match | None = None,
) -> None:
    """Записать место и очки игроку или обоим игрокам команды."""
    if isinstance(entity, TournamentTeam):
        for p in (entity.player1, entity.player2):
            if p and not getattr(p, "is_bye", False):
                TournamentPlayerResult.objects.update_or_create(
                    tournament=tournament,
                    player=p,
                    defaults={
                        "place": place,
                        "fan_points": pts,
                        "round_eliminated": round_eliminated,
                        "is_consolation": is_consolation,
                    },
                )
                _update_season_points_tvd(p, pts, match)
    else:
        TournamentPlayerResult.objects.update_or_create(
            tournament=tournament,
            player=entity,
            defaults={
                "place": place,
                "fan_points": pts,
                "round_eliminated": round_eliminated,
                "is_consolation": is_consolation,
            },
        )
        _update_season_points_tvd(entity, pts, match)


def tvd_points_for_place(tournament: Tournament, place: int) -> int:
    """Очки за место в ТВД. Берутся из настроек турнира (fan_points_*) без масштабирования."""
    if place == 1:
        return int(tournament.fan_points_winner)
    if place == 2:
        return int(tournament.fan_points_final)
    if 3 <= place <= 4:
        return int(tournament.fan_points_sf)
    if 5 <= place <= 8:
        return int(tournament.fan_points_r2)
    return int(tournament.fan_points_r1)


def _update_season_points_tvd(player: Player, points: int, match: Match | None) -> None:
    """Начислить сезонные очки игроку (аналог fan._update_season_points)."""
    if not player or getattr(player, "is_bye", False) or points <= 0:
        return
    if match and match.is_sparring():
        return
    from .models import SeasonPoints

    current_season = get_current_season()
    sp, _ = SeasonPoints.objects.get_or_create(
        player=player,
        defaults={
            "current_season_points": 0,
            "season_name": current_season.name,
            "season_year": current_season.year,
        },
    )
    if sp.season_name != current_season.name or sp.season_year != current_season.year:
        sp.current_season_points = 0
        sp.season_name = current_season.name
        sp.season_year = current_season.year
    sp.current_season_points += points
    sp.save(
        update_fields=[
            "current_season_points",
            "season_name",
            "season_year",
            "updated_at",
        ]
    )


def _assign_tvd_places_5_onwards(tournament: Tournament) -> None:
    """
    Присвоить следующие места (проигравшие в плей-офф и остальные участники), записать в fan_results и начислить очки.
    Вызывается из check_and_finalize после присвоения 1–2 (и при наличии матча за 3-е — 3–4).
    Следующее место = max(уже присвоенных) + 1: при 4 участниках (2 группы) SF-проигравшие получают 3 и 4; при 6+ группах с матчем за 3-е — 5 и 6.
    """
    results_with_place = tournament.fan_results.filter(place__isnull=False)
    assigned_ids = set(results_with_place.values_list("player_id", flat=True))
    assigned_places = list(
        results_with_place.values_list("place", flat=True).distinct()
    )
    next_place = max(assigned_places) + 1 if assigned_places else 1
    is_doubles = tournament.is_doubles()

    # Собираем id игроков, которые участвуют в матче за 3-е место (для 6 групп) — их не начисляем здесь
    third_place_player_ids: set[int] = set()
    third_place_match = (
        tournament.matches.filter(tvd_stage=TVD_STAGE_THIRD_PLACE, is_consolation=False)
        .select_related("team1", "team2")
        .first()
    )
    if third_place_match:
        if third_place_match.team1_id and third_place_match.team2_id:
            if third_place_match.team1.player1_id:
                third_place_player_ids.add(third_place_match.team1.player1_id)
            if third_place_match.team1.player2_id:
                third_place_player_ids.add(third_place_match.team1.player2_id)
            if third_place_match.team2.player1_id:
                third_place_player_ids.add(third_place_match.team2.player1_id)
            if third_place_match.team2.player2_id:
                third_place_player_ids.add(third_place_match.team2.player2_id)
        else:
            if third_place_match.player1_id:
                third_place_player_ids.add(third_place_match.player1_id)
            if third_place_match.player2_id:
                third_place_player_ids.add(third_place_match.player2_id)

    # SF-проигравшие (для 4–6 групп) получают места 5–6, кроме тех, кто в матче за 3-е место
    sf_matches = tournament.matches.filter(
        tvd_stage=TVD_STAGE_MAIN_SF,
        is_consolation=False,
        status__in=(Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER),
    ).select_related("team1", "team2", "player1", "player2")
    for m in sf_matches:
        if not (m.winner_id or m.winner_team_id):
            continue
        if m.winner_team_id:
            loser_entity = m.team2 if m.winner_team_id == m.team1_id else m.team1
            if not loser_entity:
                continue
            loser_ids = {
                loser_entity.player1_id,
                loser_entity.player2_id,
            } - {None}
            if any(
                pid in assigned_ids or pid in third_place_player_ids
                for pid in loser_ids
            ):
                continue
            _set_tvd_place_for_entity(
                tournament,
                loser_entity,
                next_place,
                tvd_points_for_place(tournament, next_place),
                "sf",
                False,
                None,
            )
            assigned_ids.update(loser_ids)
            next_place += 1
        else:
            loser = m.player2 if m.winner_id == m.player1_id else m.player1
            if (
                loser
                and not getattr(loser, "is_bye", False)
                and loser.id not in assigned_ids
                and loser.id not in third_place_player_ids
            ):
                pts = tvd_points_for_place(tournament, next_place)
                TournamentPlayerResult.objects.update_or_create(
                    tournament=tournament,
                    player=loser,
                    defaults={
                        "place": next_place,
                        "fan_points": pts,
                        "round_eliminated": TournamentPlayerResult.RoundEliminated.SF,
                        "is_consolation": False,
                    },
                )
                _update_season_points_tvd(loser, pts, None)
                assigned_ids.add(loser.id)
                next_place += 1

    # Проигравшие в R1 (main_qf) — для 6 групп это 1/6 (6 игроков), для 4–5 групп — 1/4 (4 игрока)
    qf_matches = (
        tournament.matches.filter(
            tvd_stage=TVD_STAGE_MAIN_QF,
            status__in=(Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER),
        )
        .select_related("team1", "team2", "player1", "player2")
        .order_by("round_index", "round_order")
    )
    for m in qf_matches:
        if not (m.winner_id or m.winner_team_id):
            continue
        if m.winner_team_id:
            loser_entity = m.team2 if m.winner_team_id == m.team1_id else m.team1
            if not loser_entity:
                continue
            loser_ids = {loser_entity.player1_id, loser_entity.player2_id} - {None}
            if any(pid in assigned_ids for pid in loser_ids):
                continue
            _set_tvd_place_for_entity(
                tournament,
                loser_entity,
                next_place,
                tvd_points_for_place(tournament, next_place),
                "r2",
                False,
                None,
            )
            assigned_ids.update(loser_ids)
            next_place += 1
        else:
            loser = m.player2 if m.winner_id == m.player1_id else m.player1
            if (
                loser
                and not getattr(loser, "is_bye", False)
                and loser.id not in assigned_ids
            ):
                pts = tvd_points_for_place(tournament, next_place)
                TournamentPlayerResult.objects.update_or_create(
                    tournament=tournament,
                    player=loser,
                    defaults={
                        "place": next_place,
                        "fan_points": pts,
                        "round_eliminated": TournamentPlayerResult.RoundEliminated.R2,
                        "is_consolation": False,
                    },
                )
                _update_season_points_tvd(loser, pts, None)
                assigned_ids.add(loser.id)
                next_place += 1

    # Остальные участники: не вышли в плей-офф или вылетели в группе
    if is_doubles:
        members_with_team = (
            TVDGroupMember.objects.filter(
                group__tournament=tournament,
                team_id__isnull=False,
            )
            .select_related("team", "group")
            .order_by("final_place", "group__name")
        )
        for member in members_with_team:
            if not member.team_id:
                continue
            t = member.team
            ids = {t.player1_id, t.player2_id} - {None}
            if ids <= assigned_ids:
                continue
            place = next_place
            next_place += 1
            pts = tvd_points_for_place(tournament, place)
            _set_tvd_place_for_entity(
                tournament,
                t,
                place,
                pts,
                "r1",
                False,
                None,
            )
            assigned_ids.update(ids)
    else:
        remaining_participant_ids = [p.id for p in tournament.participants.all()]
        remaining_ids = [
            pid for pid in remaining_participant_ids if pid not in assigned_ids
        ]
        if not remaining_ids:
            return
        members_order = (
            TVDGroupMember.objects.filter(
                group__tournament=tournament,
                player_id__in=remaining_ids,
            )
            .select_related("group")
            .order_by("final_place", "group__name")
            .values_list("player_id", flat=True)
        )
        ordered_remaining = list(dict.fromkeys(members_order))
        for pid in remaining_ids:
            if pid not in ordered_remaining:
                ordered_remaining.append(pid)
        for player_id in ordered_remaining:
            player = Player.objects.get(pk=player_id)
            if getattr(player, "is_bye", False):
                continue
            place = next_place
            next_place += 1
            pts = tvd_points_for_place(tournament, place)
            TournamentPlayerResult.objects.update_or_create(
                tournament=tournament,
                player=player,
                defaults={
                    "place": place,
                    "fan_points": pts,
                    "round_eliminated": TournamentPlayerResult.RoundEliminated.R1,
                    "is_consolation": False,
                },
            )
            _update_season_points_tvd(player, pts, None)


def check_and_finalize(tournament: Tournament) -> tuple[bool, str]:
    """
    Проверить, завершён ли финал и все места определены; начислить очки, завершить турнир.
    Для 3 групп с круговой мини-сеткой: проверяем завершённость двух кругов (1–3 и 4–6 места).
    """
    if not _is_tvd(tournament) or tournament.status == TournamentStatus.COMPLETED:
        return False, "Турнир не ТВД или уже завершён."

    # 3 группы: круговая мини-сетка за 1–3 и 4–6 места (без финала)
    rr_1_3_matches = list(
        tournament.matches.filter(
            tvd_stage=TVD_STAGE_MAIN_RR_1_3, is_consolation=False
        ).select_related("player1", "player2", "team1", "team2")
    )
    if rr_1_3_matches:
        from .round_robin import compute_standings_for_entities

        rr_4_6_matches = list(
            tournament.matches.filter(
                tvd_stage=TVD_STAGE_MAIN_RR_4_6, is_consolation=False
            ).select_related("player1", "player2", "team1", "team2")
        )
        completed = (
            Match.MatchStatus.COMPLETED,
            Match.MatchStatus.WALKOVER,
        )
        for m in rr_1_3_matches + rr_4_6_matches:
            if m.status not in completed or not (m.winner_id or m.winner_team_id):
                return False, "Завершите все матчи кругов за 1–3 и 4–6 места."
        entities_1_3, _ = get_tvd_rr_entities_and_matches(
            tournament, TVD_STAGE_MAIN_RR_1_3
        )
        entities_4_6, _ = get_tvd_rr_entities_and_matches(
            tournament, TVD_STAGE_MAIN_RR_4_6
        )
        if not entities_1_3 or not entities_4_6:
            return False, "Некорректные данные круговой мини-сетки."
        standings_1_3 = compute_standings_for_entities(
            tournament, entities_1_3, rr_1_3_matches
        )
        standings_4_6 = compute_standings_for_entities(
            tournament, entities_4_6, rr_4_6_matches
        )
        for place, row in enumerate(standings_1_3, 1):
            entity = row.get("team") or row.get("player")
            if entity:
                pts = tvd_points_for_place(tournament, place)
                _set_tvd_place_for_entity(
                    tournament,
                    entity,
                    place,
                    pts,
                    ("winner" if place == 1 else "sf"),
                    False,
                    None,
                )
        for place, row in enumerate(standings_4_6, 4):
            entity = row.get("team") or row.get("player")
            if entity:
                pts = tvd_points_for_place(tournament, place)
                _set_tvd_place_for_entity(
                    tournament,
                    entity,
                    place,
                    pts,
                    "sf",
                    False,
                    None,
                )
        _assign_tvd_places_5_onwards(tournament)
        tournament.status = TournamentStatus.COMPLETED
        tournament.save(update_fields=["status"])
        logger.info("TVD tournament %s completed (3-group RR).", tournament.name)
        return True, "Турнир завершён, очки начислены."

    final = tournament.matches.filter(
        tvd_stage=TVD_STAGE_MAIN_FINAL,
        is_consolation=False,
    ).first()
    if not final or final.status not in (
        Match.MatchStatus.COMPLETED,
        Match.MatchStatus.WALKOVER,
    ):
        return False, "Финал основной сетки не завершён."
    if not (final.winner_id or getattr(final, "winner_team_id", None)):
        return False, "Финал основной сетки не завершён."

    is_doubles = tournament.is_doubles() and final.winner_team_id
    if is_doubles:
        winner_entity = final.winner_team
        loser_entity = (
            final.team2 if final.winner_team_id == final.team1_id else final.team1
        )
    else:
        winner_entity = final.winner
        loser_entity = (
            final.player2 if final.winner_id == final.player1_id else final.player1
        )
    if not winner_entity or not loser_entity:
        return False, "Финал без победителя или проигравшего."
    if isinstance(winner_entity, Player) and getattr(winner_entity, "is_bye", False):
        return False, "Финал не может быть с BYE."
    if isinstance(loser_entity, Player) and getattr(loser_entity, "is_bye", False):
        return False, "Финал не может быть с BYE."

    # Assign places 1 and 2
    points_1 = tvd_points_for_place(tournament, 1)
    points_2 = tvd_points_for_place(tournament, 2)
    _set_tvd_place_for_entity(
        tournament,
        winner_entity,
        1,
        points_1,
        "winner",
        False,
        final,
    )
    _set_tvd_place_for_entity(
        tournament,
        loser_entity,
        2,
        points_2,
        "final",
        False,
        final,
    )

    # 3-е и 4-е места: матч за 3-е место основной сетки (3 или 6 групп) или финал утешительной (2/4/5 групп)
    main_third = tournament.matches.filter(
        tvd_stage=TVD_STAGE_THIRD_PLACE,
        is_consolation=False,
    ).first()
    if main_third and main_third.status not in (
        Match.MatchStatus.COMPLETED,
        Match.MatchStatus.WALKOVER,
    ):
        return False, "Завершите матч за 3-е место основной сетки."
    if (
        main_third
        and main_third.status
        in (
            Match.MatchStatus.COMPLETED,
            Match.MatchStatus.WALKOVER,
        )
        and (main_third.winner_id or main_third.winner_team_id)
    ):
        if main_third.winner_team_id:
            third_entity = main_third.winner_team
            loser_cons_entity = (
                main_third.team2
                if main_third.winner_team_id == main_third.team1_id
                else main_third.team1
            )
        else:
            third_entity = main_third.winner
            loser_cons_entity = (
                main_third.player2
                if main_third.winner_id == main_third.player1_id
                else main_third.player1
            )
        pts_3 = tvd_points_for_place(tournament, 3)
        pts_4 = tvd_points_for_place(tournament, 4)
        _set_tvd_place_for_entity(
            tournament,
            third_entity,
            3,
            pts_3,
            "sf",
            False,
            main_third,
        )
        if loser_cons_entity:
            _set_tvd_place_for_entity(
                tournament,
                loser_cons_entity,
                4,
                pts_4,
                "sf",
                False,
                main_third,
            )
    elif not main_third:
        cons_final = tournament.matches.filter(
            tvd_stage=TVD_STAGE_CONSOLATION_FINAL,
            is_consolation=True,
        ).first()
        if (
            cons_final
            and cons_final.status
            in (
                Match.MatchStatus.COMPLETED,
                Match.MatchStatus.WALKOVER,
            )
            and (cons_final.winner_id or cons_final.winner_team_id)
        ):
            if cons_final.winner_team_id:
                third_entity = cons_final.winner_team
                loser_cons_entity = (
                    cons_final.team2
                    if cons_final.winner_team_id == cons_final.team1_id
                    else cons_final.team1
                )
            else:
                third_entity = cons_final.winner
                loser_cons_entity = (
                    cons_final.player2
                    if cons_final.winner_id == cons_final.player1_id
                    else cons_final.player1
                )
            pts_3 = tvd_points_for_place(tournament, 3)
            pts_4 = tvd_points_for_place(tournament, 4)
            _set_tvd_place_for_entity(
                tournament,
                third_entity,
                3,
                pts_3,
                "sf",
                True,
                cons_final,
            )
            if loser_cons_entity:
                _set_tvd_place_for_entity(
                    tournament,
                    loser_cons_entity,
                    4,
                    pts_4,
                    "sf",
                    True,
                    cons_final,
                )

    _assign_tvd_places_5_onwards(tournament)

    tournament.status = TournamentStatus.COMPLETED
    tournament.save(update_fields=["status"])
    logger.info("TVD tournament %s completed.", tournament.name)
    return True, "Турнир завершён, очки начислены."


def process_overdue_match(match: Match) -> tuple[bool, str]:
    """Тех. победа при просрочке дедлайна; продвижение по сетке."""
    if not match.tournament_id or not _is_tvd(match.tournament):
        return False, "Не ТВД-матч."
    if match.status in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER):
        return False, "Матч уже завершён."
    if not match.deadline or match.deadline > timezone.now():
        return False, "Дедлайн не истёк."

    from .fan import _overdue_winner, apply_overdue_walkover

    if match.tvd_stage == TVD_STAGE_GROUP:
        if match.team1_id and match.team2_id:
            # Парный групповой матч: победитель по среднему рейтингу команды
            t1, t2 = match.team1, match.team2
            r1, r2 = _team_rating(t1), _team_rating(t2)
            if r1 > r2 or (r1 == r2 and t1.pk < t2.pk):
                winner_team = t1
            else:
                winner_team = t2
            match.winner_team = winner_team
            match.winner = winner_team.player1
            match.status = Match.MatchStatus.WALKOVER
            match.completed_datetime = timezone.now()
            match.save(
                update_fields=["winner_team", "winner", "status", "completed_datetime"]
            )
        else:
            winner = _overdue_winner(match)
            if not winner:
                return False, "Не удалось определить победителя."
            apply_overdue_walkover(match, winner)
        recalculate_group_standings(match.tvd_group)
        return True, f"Матч {match.pk}: тех. победа (группа), таблица пересчитана."
    else:
        winner = _overdue_winner(match)
        if not winner:
            return False, "Не удалось определить победителя."
        apply_overdue_walkover(match, winner)
        if match.team1_id and match.team2_id:
            match.winner_team = (
                match.team1
                if match.winner_id == match.team1.player1_id
                else match.team2
            )
            match.save(update_fields=["winner_team"])
        advance_winner(match)
        check_and_finalize(match.tournament)
        return True, f"Матч {match.pk}: тех. победа (плей-офф)."
