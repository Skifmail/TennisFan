"""
Круговой турнир: алгоритм составления сетки, подсчёт очков, определение победителя.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from django.utils import timezone

from apps.users.models import Player

from .models import Match, Tournament, TournamentTeam

logger = logging.getLogger(__name__)

ROUND_ROBIN_FORMAT = "round_robin"
BYE_EMAIL = "bye@tennisfan.local"


def _is_round_robin(t: Tournament) -> bool:
    return getattr(t, "format", None) == ROUND_ROBIN_FORMAT


def _get_bye_player() -> Optional[Player]:
    """Служебный игрок «Свободный круг» для тура при нечётном числе участников."""
    return Player.objects.filter(user__email=BYE_EMAIL, is_bye=True).select_related("user").first()


def _tournament_start_dt(tournament: Tournament):
    """Дата/время старта турнира для дедлайнов."""
    start = timezone.now()
    if tournament.start_date:
        d = tournament.start_date
        if isinstance(d, str):
            d = datetime.strptime(d, "%Y-%m-%d").date()
        start = timezone.make_aware(datetime.combine(d, datetime.min.time()))
    return start


def _circle_schedule(participants: list) -> list[list[tuple]]:
    """
    Circle method (метод вращения): каждый играет с каждым ровно раз.
    При нечётном N добавляется BYE — тот, кто «играет» с BYE, отдыхает.
    Возвращает список раундов, каждый раунд — список пар (player1, player2).
    player2 может быть None (BYE).
    """
    n = len(participants)
    if n < 2:
        return []

    if n % 2 == 1:
        participants = list(participants) + [None]  # BYE
        n += 1

    units = list(participants)
    schedule = []
    for _ in range(n - 1):
        round_pairs = []
        for i in range(n // 2):
            round_pairs.append((units[i], units[n - i - 1]))
        schedule.append(round_pairs)
        # Вращение: фиксируем позицию 0, последний переходит на позицию 1
        units.insert(1, units.pop())
    return schedule


def generate_bracket(tournament: Tournament) -> tuple[bool, str]:
    """
    Сформировать сетку кругового турнира.
    Каждый участник/команда играет с каждым ровно один раз.
    При нечётном N — один отдыхает в каждом туре (BYE); матчей с BYE не создаём.
    Нет продвижения по раундам — проблема «двойного bye» к круговому формату не относится.
    Поддерживает одиночные и парные турниры.
    """
    if not _is_round_robin(tournament):
        return False, "Турнир не в круговом формате."
    if tournament.bracket_generated:
        return False, "Сетка уже сформирована."

    from .solo_teams import remove_solo_teams_from_doubles_tournament

    is_doubles = tournament.is_doubles()
    if is_doubles:
        removed = remove_solo_teams_from_doubles_tournament(tournament)
        if removed:
            logger.info("Removed %d solo teams from round-robin doubles tournament %s", removed, tournament.slug)
    if is_doubles:
        entities = list(
            tournament.teams.filter(player2__isnull=False)
            .select_related("player1__user", "player2__user")
            .order_by("player1__user__last_name", "player1__user__first_name")
        )
        max_n = tournament.max_teams
        entity_name = "команд"
    else:
        entities = list(tournament.participants.order_by("user__last_name", "user__first_name"))
        max_n = tournament.max_participants
        entity_name = "участников"

    n = len(entities)
    if n < 2:
        return False, f"Нужно минимум 2 {entity_name[:-1]} для формирования сетки."

    if max_n is not None and n > max_n:
        return False, f"Зарегистрировано {n}, максимум {max_n}."

    bye_player = _get_bye_player()
    if n % 2 == 1 and not bye_player:
        return False, "Не найден служебный игрок «Свободный круг» (bye). Выполните: python manage.py ensure_bye_player"

    schedule = _circle_schedule(entities)
    start = _tournament_start_dt(tournament)
    days = getattr(tournament, "match_days_per_round", 7) or 7
    delta = timedelta(days=days)

    created = 0
    for round_idx, round_pairs in enumerate(schedule, 1):
        round_name = f"Тур {round_idx}"
        deadline = start + delta * round_idx
        for order, (e1, e2) in enumerate(round_pairs, 1):
            if e1 is None or e2 is None:
                continue
            if is_doubles:
                team1, team2 = (e1, e2) if e1.pk < e2.pk else (e2, e1)
                if getattr(team1.player1, "is_bye", False) or getattr(team2.player1, "is_bye", False):
                    continue
                if Match.objects.filter(
                    tournament=tournament,
                    round_index=round_idx,
                    team1=team1,
                    team2=team2,
                ).exists():
                    continue
                Match.objects.create(
                    tournament=tournament,
                    round_name=round_name,
                    round_index=round_idx,
                    round_order=order,
                    is_consolation=False,
                    team1=team1,
                    team2=team2,
                    player1=team1.player1,
                    player2=team2.player1,
                    status=Match.MatchStatus.SCHEDULED,
                    deadline=deadline,
                )
            else:
                p1, p2 = e1, e2
                if getattr(p1, "is_bye", False) or getattr(p2, "is_bye", False):
                    continue
                player1, player2 = (p1, p2) if p1.pk < p2.pk else (p2, p1)
                if Match.objects.filter(
                    tournament=tournament,
                    round_index=round_idx,
                    player1=player1,
                    player2=player2,
                ).exists():
                    continue
                Match.objects.create(
                    tournament=tournament,
                    round_name=round_name,
                    round_index=round_idx,
                    round_order=order,
                    is_consolation=False,
                    player1=player1,
                    player2=player2,
                    status=Match.MatchStatus.SCHEDULED,
                    deadline=deadline,
                )
            created += 1

    tournament.bracket_generated = True
    tournament.save(update_fields=["bracket_generated"])
    total_expected = n * (n - 1) // 2
    logger.info("Round-robin bracket created for %s: %d matches (n=%d)", tournament.name, created, n)
    return True, f"Сетка сформирована: {created} матчей (ожидалось {total_expected}), {entity_name} {n}."


def _get_winner_entity(match: Match, is_doubles: bool):
    """Возвращает победителя: Player для singles, TournamentTeam для doubles."""
    if is_doubles and match.winner_team_id:
        return match.winner_team
    if match.winner_id:
        return match.winner
    return None


def _get_loser_entity(match: Match, is_doubles: bool):
    """Возвращает проигравшего: Player для singles, TournamentTeam для doubles."""
    if is_doubles and match.team1_id and match.team2_id:
        if match.winner_team_id == match.team1_id:
            return match.team2
        return match.team1
    if match.winner_id:
        return match.player2 if match.winner_id == match.player1_id else match.player1
    return None


def _entity_id(entity) -> Optional[int]:
    """ID сущности (player или team) для использования как ключа."""
    return entity.pk if entity else None


def compute_standings(tournament: Tournament) -> list[dict]:
    """
    Вычислить таблицу результатов кругового турнира.
    Сортировка: 1) победы, 2) личная встреча, 3) разница сетов, 4) разница геймов,
    5) больше выигранных геймов, 6) жеребьёвка (по id).
    Поддерживает одиночные и парные турниры.
    """
    is_doubles = tournament.is_doubles()
    if is_doubles:
        entities = list(
            tournament.teams.filter(player2__isnull=False)
            .select_related("player1__user", "player2__user")
            .order_by("player1__user__last_name", "player1__user__first_name")
        )
    else:
        entities = list(tournament.participants.select_related("user"))
    if not entities:
        return []

    matches = tournament.matches.filter(
        is_consolation=False,
        status__in=[Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER],
    ).select_related("player1", "player2", "winner", "team1", "team2", "winner_team")

    stats = {}
    for e in entities:
        if is_doubles:
            if getattr(e.player1, "is_bye", False):
                continue
            stats[e.id] = {
                "team": e,
                "player": None,
                "matches": 0,
                "wins": 0,
                "losses": 0,
                "sets_won": 0,
                "sets_lost": 0,
                "games_won": 0,
                "games_lost": 0,
            }
        else:
            if getattr(e, "is_bye", False):
                continue
            stats[e.id] = {
                "player": e,
                "team": None,
                "matches": 0,
                "wins": 0,
                "losses": 0,
                "sets_won": 0,
                "sets_lost": 0,
                "games_won": 0,
                "games_lost": 0,
            }

    for m in matches:
        w = _get_winner_entity(m, is_doubles)
        los = _get_loser_entity(m, is_doubles)
        if not w or not los:
            continue
        if is_doubles:
            if getattr(w.player1, "is_bye", False) or getattr(los.player1, "is_bye", False):
                continue
        else:
            if getattr(w, "is_bye", False) or getattr(los, "is_bye", False):
                continue
        wid, lid = _entity_id(w), _entity_id(los)
        if wid not in stats or lid not in stats:
            continue
        stats[wid]["matches"] += 1
        stats[lid]["matches"] += 1
        stats[wid]["wins"] += 1
        stats[lid]["losses"] += 1

        for i in range(1, 4):
            s1 = getattr(m, f"player1_set{i}")
            s2 = getattr(m, f"player2_set{i}")
            if s1 is not None and s2 is not None:
                side1_won_set = s1 > s2
                if is_doubles:
                    winner_side1 = m.winner_team_id == m.team1_id
                    if winner_side1 == side1_won_set:
                        stats[wid]["sets_won"] += 1
                        stats[lid]["sets_lost"] += 1
                    else:
                        stats[lid]["sets_won"] += 1
                        stats[wid]["sets_lost"] += 1
                else:
                    if (m.winner_id == m.player1_id) == side1_won_set:
                        stats[wid]["sets_won"] += 1
                        stats[lid]["sets_lost"] += 1
                    else:
                        stats[lid]["sets_won"] += 1
                        stats[wid]["sets_lost"] += 1
                if is_doubles:
                    if wid == m.team1_id:
                        stats[wid]["games_won"] += s1
                        stats[wid]["games_lost"] += s2
                        stats[lid]["games_won"] += s2
                        stats[lid]["games_lost"] += s1
                    else:
                        stats[wid]["games_won"] += s2
                        stats[wid]["games_lost"] += s1
                        stats[lid]["games_won"] += s1
                        stats[lid]["games_lost"] += s2
                else:
                    stats[m.player1_id]["games_won"] += s1
                    stats[m.player1_id]["games_lost"] += s2
                    stats[m.player2_id]["games_won"] += s2
                    stats[m.player2_id]["games_lost"] += s1

    rows = [v for v in stats.values()]
    if not rows:
        return []

    def head_to_head(e1_id: int, e2_id: int) -> int:
        for m in matches:
            if is_doubles:
                if {m.team1_id, m.team2_id} == {e1_id, e2_id} and m.winner_team_id:
                    return 1 if m.winner_team_id == e1_id else -1
            else:
                if {m.player1_id, m.player2_id} == {e1_id, e2_id} and m.winner_id:
                    return 1 if m.winner_id == e1_id else -1
        return 0

    def entity_id(row):
        return row["team"].id if row["team"] else row["player"].id

    def sort_key(row):
        eid = entity_id(row)
        h2h_sum = 0
        for other in rows:
            if entity_id(other) == eid:
                continue
            h2h_sum += head_to_head(eid, entity_id(other))
        return (
            -row["wins"],
            -h2h_sum,
            -(row["sets_won"] - row["sets_lost"]),
            -(row["games_won"] - row["games_lost"]),
            -row["games_won"],
            eid,
        )

    rows.sort(key=sort_key)
    has_completed_matches = any(r["matches"] > 0 for r in rows)
    standings = []
    for i, row in enumerate(rows, 1):
        standings.append({
            "place": i if has_completed_matches else None,
            "player": row["player"],
            "team": row["team"],
            "matches": row["matches"],
            "wins": row["wins"],
            "losses": row["losses"],
            "sets": f"{row['sets_won']}–{row['sets_lost']}",
            "games": f"{row['games_won']}–{row['games_lost']}",
            "points": row["wins"],
        })
    return standings


def get_match_matrix(tournament: Tournament) -> tuple[list, list]:
    """
    Построить матрицу результатов для визуализации.
    Возвращает (participants_list, matrix_rows).
    participants_list — список Player (singles) или TournamentTeam (doubles).
    matrix_rows[i][j] = {"win": 0|1, "games": "X/Y"} с точки зрения участника i против j.
    """
    is_doubles = tournament.is_doubles()
    if is_doubles:
        participants = list(
            tournament.teams.filter(player2__isnull=False)
            .select_related("player1__user", "player2__user")
            .order_by("player1__user__last_name", "player1__user__first_name")
        )
        participants = [t for t in participants if not getattr(t.player1, "is_bye", False)]
        id_field = "id"
    else:
        participants = list(
            tournament.participants.select_related("user").order_by("user__last_name", "user__first_name")
        )
        participants = [p for p in participants if not getattr(p, "is_bye", False)]
        id_field = "id"

    n = len(participants)
    pid_to_idx = {getattr(p, id_field): i for i, p in enumerate(participants)}

    matrix = [[{"win": None, "games": None} for _ in range(n)] for _ in range(n)]

    matches = tournament.matches.filter(
        is_consolation=False,
        status__in=[Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER],
    ).select_related("team1", "team2", "winner", "winner_team")

    for m in matches:
        if is_doubles:
            if not m.team1_id or not m.team2_id or not m.winner_team_id:
                continue
            if getattr(m.team1.player1, "is_bye", False) or getattr(m.team2.player1, "is_bye", False):
                continue
            i = pid_to_idx.get(m.team1_id)
            j = pid_to_idx.get(m.team2_id)
            winner_team = m.winner_team
        else:
            if getattr(m.player1, "is_bye", False) or getattr(m.player2, "is_bye", False):
                continue
            i = pid_to_idx.get(m.player1_id)
            j = pid_to_idx.get(m.player2_id)
            winner_team = m.winner
        if i is None or j is None:
            continue
        g1 = (m.player1_set1 or 0) + (m.player1_set2 or 0) + (m.player1_set3 or 0)
        g2 = (m.player2_set1 or 0) + (m.player2_set2 or 0) + (m.player2_set3 or 0)
        if is_doubles:
            side1_won = m.winner_team_id == m.team1_id
        else:
            side1_won = m.winner_id == m.player1_id
        if side1_won:
            matrix[i][j] = {"win": 1, "games": f"{g1}/{g2}"}
            matrix[j][i] = {"win": 0, "games": f"{g2}/{g1}"}
        else:
            matrix[i][j] = {"win": 0, "games": f"{g1}/{g2}"}
            matrix[j][i] = {"win": 1, "games": f"{g2}/{g1}"}

    return participants, matrix


def check_and_finalize_if_complete(tournament: Tournament) -> bool:
    """
    Если все матчи кругового турнира сыграны — установить статус «Завершён».
    Очки (1 за победу) начисляются при завершении каждого матча (в signal).
    """
    if not _is_round_robin(tournament) or tournament.status == "completed":
        return False
    main_matches = tournament.matches.filter(is_consolation=False)
    total = main_matches.count()
    if total == 0:
        return False
    completed = main_matches.filter(
        status__in=[Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER]
    ).exclude(winner__isnull=True).count()
    if completed >= total:
        tournament.status = "completed"
        tournament.save(update_fields=["status"])
        logger.info("Round-robin tournament %s completed (all %d matches done).", tournament.name, total)
        return True
    return False
