"""
Агрегаты и данные для стат-дашборда страницы турнирной таблицы.

Строит KPI, графики, инсайты и тепловую карту из матчей турнира
без дополнительных тяжёлых запросов (один проход по завершённым матчам).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any

from apps.tournaments.models import Match, Tournament
from apps.tournaments.round_robin import get_match_matrix
from apps.users.models import Player


def _player_label(player: Player | None) -> str:
    """Краткая подпись игрока для графиков и инсайтов."""
    if player is None:
        return "—"
    return str(player)


def _side_label(match: Match, side: int) -> str:
    """Подпись стороны 1 или 2 матча (игрок или команда)."""
    if side == 1:
        if match.team1 is not None:
            return str(match.team1)
        return _player_label(match.player1)
    if match.team2 is not None:
        return str(match.team2)
    return _player_label(match.player2)


def _winner_label(match: Match) -> str:
    """Подпись победителя матча."""
    if match.winner_team is not None:
        return str(match.winner_team)
    return _player_label(match.winner)


def _loser_player(match: Match) -> Player | None:
    """Проигравший игрок (для singles) или капитан проигравшей команды."""
    if match.winner_team_id and match.team1_id and match.team2_id:
        loser_team = (
            match.team2 if match.winner_team_id == match.team1_id else match.team1
        )
        if loser_team is None:
            return None
        captain: Player | None = loser_team.player1
        return captain
    if match.winner_id and match.player1_id and match.player2_id:
        loser: Player | None = (
            match.player2 if match.winner_id == match.player1_id else match.player1
        )
        return loser
    return None


def _winner_player(match: Match) -> Player | None:
    """Победивший игрок или капитан победившей команды."""
    if match.winner_team is not None:
        captain: Player | None = match.winner_team.player1
        return captain
    winner: Player | None = match.winner
    return winner


def _match_sets(match: Match) -> list[tuple[int, int]]:
    """Список сыгранных сетов как пары (геймы_п1, геймы_п2)."""
    sets: list[tuple[int, int]] = []
    for i in range(1, 4):
        s1 = getattr(match, f"player1_set{i}")
        s2 = getattr(match, f"player2_set{i}")
        if s1 is not None and s2 is not None:
            sets.append((int(s1), int(s2)))
    return sets


def _set_bucket(a: int, b: int) -> str:
    """Корзина счёта сета (больший:меньший), например 6:4, 7:6."""
    hi, lo = (a, b) if a >= b else (b, a)
    return f"{hi}:{lo}"


def _sets_won_by_side(sets: list[tuple[int, int]]) -> tuple[int, int]:
    """Сколько сетов выиграла сторона 1 и сторона 2."""
    w1 = sum(1 for a, b in sets if a > b)
    w2 = sum(1 for a, b in sets if b > a)
    return w1, w2


@dataclass(frozen=True)
class TablesDashboardData:
    """Готовые данные для шаблона и Chart.js на странице турнирной таблицы.

    Attributes:
        kpi: словарь KPI-показателей.
        charts: словарь секций для Chart.js.
        insights: список карточек-фактов.
        heatmap: данные тепловой карты (или None).
        show_flags: флаги показа секций графиков.
    """

    kpi: dict[str, Any]
    charts: dict[str, Any]
    insights: list[dict[str, str]]
    heatmap: dict[str, Any] | None
    show_flags: dict[str, bool]


def build_tables_dashboard(
    tournament: Tournament,
    *,
    is_round_robin: bool,
    is_fan: bool,
    is_tvd: bool,
    participants: list[Player],
    chart_status_labels: list[str],
    chart_status_data: list[int],
    chart_round_labels: list[str],
    chart_round_data: list[int],
    ratings_labels: list[str],
    ratings_sorted: list[float],
) -> TablesDashboardData:
    """Собирает KPI, графики, инсайты и heatmap для турнирной таблицы.

    Args:
        tournament: Турнир.
        is_round_robin: Круговой формат.
        is_fan: FAN-формат (single elimination).
        is_tvd: Формат ТВД.
        participants: Список участников (для рейтингов).
        chart_status_labels: Подписи статусов матчей.
        chart_status_data: Значения статусов.
        chart_round_labels: Подписи раундов вылета.
        chart_round_data: Значения раундов вылета.
        ratings_labels: Подписи топ-рейтинга.
        ratings_sorted: Значения топ-рейтинга.

    Returns:
        TablesDashboardData: Готовый пакет данных для шаблона.
    """
    main_matches = list(
        tournament.matches.filter(is_consolation=False).select_related(
            "player1__user",
            "player2__user",
            "winner__user",
            "team1__player1__user",
            "team1__player2__user",
            "team2__player1__user",
            "team2__player2__user",
            "winner_team__player1__user",
        )
    )
    completed = [
        m
        for m in main_matches
        if m.status in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER)
    ]
    played = [m for m in completed if m.status == Match.MatchStatus.COMPLETED]
    walkovers = [m for m in completed if m.status == Match.MatchStatus.WALKOVER]

    sets_played = 0
    games_total = 0
    three_set_count = 0
    tiebreak_count = 0
    straight_wins = 0  # 2:0
    deciding_wins = 0  # 2:1
    set_buckets: dict[str, int] = defaultdict(int)
    timeline_days: dict[date, int] = defaultdict(int)
    rating_deltas: dict[int, float] = defaultdict(float)
    player_names: dict[int, str] = {}
    comeback_count = 0
    upset_count = 0

    longest_match: Match | None = None
    longest_games = -1
    biggest_win: Match | None = None
    biggest_margin = -1

    # Серии побед считаем в хронологическом порядке
    played_chrono = sorted(
        played,
        key=lambda m: (
            m.completed_datetime or m.scheduled_datetime or m.created_at,
            m.id,
        ),
    )
    win_streaks: dict[int, int] = defaultdict(int)
    max_streaks: dict[int, int] = defaultdict(int)
    streak_names: dict[int, str] = {}

    for m in played:
        sets = _match_sets(m)
        if not sets:
            continue
        sets_played += len(sets)
        match_games = sum(a + b for a, b in sets)
        games_total += match_games
        if len(sets) >= 3:
            three_set_count += 1
        for a, b in sets:
            set_buckets[_set_bucket(a, b)] += 1
            if {a, b} == {7, 6} or max(a, b) >= 7 and abs(a - b) == 1:
                tiebreak_count += 1

        w1, w2 = _sets_won_by_side(sets)
        if max(w1, w2) == 2 and min(w1, w2) == 0:
            straight_wins += 1
        elif max(w1, w2) == 2 and min(w1, w2) == 1:
            deciding_wins += 1

        if match_games > longest_games:
            longest_games = match_games
            longest_match = m

        # Крупная победа: разница геймов при 2:0
        if max(w1, w2) == 2 and min(w1, w2) == 0:
            margin = abs(sum(a for a, _ in sets) - sum(b for _, b in sets))
            if margin > biggest_margin:
                biggest_margin = margin
                biggest_win = m

        # Волевая: проиграл 1-й сет, выиграл матч
        if len(sets) >= 2:
            first_a, first_b = sets[0]
            winner_is_p1 = (
                (m.winner_team_id == m.team1_id)
                if m.winner_team_id
                else (m.winner_id == m.player1_id)
            )
            if winner_is_p1 and first_a < first_b:
                comeback_count += 1
            elif (not winner_is_p1) and first_b < first_a:
                comeback_count += 1

        # Апсет: победитель с меньшим FAN-рейтингом
        wp = _winner_player(m)
        lp = _loser_player(m)
        if wp is not None and lp is not None:
            if float(wp.total_points or 0) + 0.5 < float(lp.total_points or 0):
                upset_count += 1

        if m.completed_datetime is not None:
            timeline_days[m.completed_datetime.date()] += 1

        # Рейтинг-дельты
        for pid, delta, pl in (
            (m.player1_id, m.rating_delta_player1, m.player1),
            (m.player2_id, m.rating_delta_player2, m.player2),
        ):
            if pid and delta:
                rating_deltas[pid] += float(delta)
                if pl is not None:
                    player_names[pid] = _player_label(pl)

    for m in played_chrono:
        # Streaks в хронологическом порядке
        winner_id: int | None = None
        loser_id: int | None = None
        if m.winner_team_id is not None:
            wid = int(m.winner_team_id)
            winner_id = wid
            if m.winner_team_id == m.team1_id and m.team2_id is not None:
                lid = int(m.team2_id)
            elif m.team1_id is not None:
                lid = int(m.team1_id)
            else:
                lid = None
            loser_id = lid
            streak_names[wid] = _winner_label(m)
            if lid is not None:
                streak_names[lid] = (
                    str(m.team2) if m.winner_team_id == m.team1_id else str(m.team1)
                )
        elif m.winner_id is not None:
            wid = int(m.winner_id)
            winner_id = wid
            if m.winner_id == m.player1_id and m.player2_id is not None:
                lid = int(m.player2_id)
            elif m.player1_id is not None:
                lid = int(m.player1_id)
            else:
                lid = None
            loser_id = lid
            streak_names[wid] = _winner_label(m)
            if lid is not None:
                streak_names[lid] = _player_label(
                    m.player2 if m.winner_id == m.player1_id else m.player1
                )
        if winner_id is not None:
            win_streaks[winner_id] += 1
            max_streaks[winner_id] = max(max_streaks[winner_id], win_streaks[winner_id])
        if loser_id is not None:
            win_streaks[loser_id] = 0

    # Walkovers тоже идут в timeline
    for m in walkovers:
        if m.completed_datetime is not None:
            timeline_days[m.completed_datetime.date()] += 1

    played_count = len(played)
    completed_count = len(completed)
    matches_total = len(main_matches)
    progress_pct = (
        int(100 * completed_count / matches_total) if matches_total > 0 else 0
    )
    avg_games = round(games_total / played_count, 1) if played_count > 0 else 0.0
    three_set_pct = int(100 * three_set_count / played_count) if played_count > 0 else 0

    # Timeline sorted
    timeline_sorted = sorted(timeline_days.items(), key=lambda x: x[0])
    timeline_labels = [d.strftime("%d.%m") for d, _ in timeline_sorted]
    timeline_daily = [c for _, c in timeline_sorted]
    cumulative = 0
    timeline_cumulative: list[int] = []
    for c in timeline_daily:
        cumulative += c
        timeline_cumulative.append(cumulative)

    # Set score buckets in preferred order
    preferred_buckets = [
        "6:0",
        "6:1",
        "6:2",
        "6:3",
        "6:4",
        "7:5",
        "7:6",
    ]
    set_labels: list[str] = []
    set_data: list[int] = []
    for bucket in preferred_buckets:
        count = set_buckets.get(bucket, 0)
        if count > 0:
            set_labels.append(bucket)
            set_data.append(count)
    for bucket, count in sorted(set_buckets.items()):
        if bucket not in preferred_buckets and count > 0:
            set_labels.append(bucket)
            set_data.append(count)

    # Character of wins doughnut
    character_labels: list[str] = []
    character_data: list[int] = []
    if straight_wins:
        character_labels.append("Всухую (2:0)")
        character_data.append(straight_wins)
    if deciding_wins:
        character_labels.append("Волевые (2:1)")
        character_data.append(deciding_wins)
    if walkovers:
        character_labels.append("Тех. поражение")
        character_data.append(len(walkovers))

    # Rating deltas top +/-
    sorted_deltas = sorted(rating_deltas.items(), key=lambda x: x[1], reverse=True)
    top_gain = [x for x in sorted_deltas if x[1] > 0][:8]
    top_loss = [x for x in sorted_deltas if x[1] < 0][-8:]
    # For diverging bar: loss first (bottom) then gains, or labels with values
    delta_entries = list(reversed(top_loss)) + top_gain
    delta_labels = [player_names.get(pid, f"#{pid}") for pid, _ in delta_entries]
    delta_values = [round(v, 1) for _, v in delta_entries]

    # Insights
    insights: list[dict[str, str]] = []
    if longest_match is not None and longest_games > 0:
        insights.append(
            {
                "title": "Самый длинный матч",
                "text": (
                    f"{_side_label(longest_match, 1)} — "
                    f"{_side_label(longest_match, 2)}: "
                    f"{longest_match.score_display} ({longest_games} геймов)"
                ),
            }
        )
    if biggest_win is not None and biggest_margin > 0:
        insights.append(
            {
                "title": "Самая крупная победа",
                "text": (
                    f"{_winner_label(biggest_win)} — "
                    f"{biggest_win.score_display} "
                    f"(разница {biggest_margin} геймов)"
                ),
            }
        )
    if comeback_count > 0:
        insights.append(
            {
                "title": "Волевые победы",
                "text": (
                    f"{comeback_count} матч(ей), где победитель " f"проиграл первый сет"
                ),
            }
        )
    if upset_count > 0:
        insights.append(
            {
                "title": "Апсеты",
                "text": (
                    f"{upset_count} побед(ы) над соперником "
                    f"с более высоким рейтингом"
                ),
            }
        )
    if max_streaks:
        best_id, best_streak = max(max_streaks.items(), key=lambda x: x[1])
        if best_streak >= 2:
            insights.append(
                {
                    "title": "Лучшая серия",
                    "text": (
                        f"{streak_names.get(best_id, '—')} — "
                        f"{best_streak} побед подряд"
                    ),
                }
            )
    if tiebreak_count > 0:
        insights.append(
            {
                "title": "Тай-брейки",
                "text": f"Сыграно {tiebreak_count} сетов с тай-брейком",
            }
        )

    # Heatmap for RR
    heatmap: dict[str, Any] | None = None
    if is_round_robin:
        entities, matrix = get_match_matrix(tournament)
        if entities and matrix:
            labels = [str(e) for e in entities]
            cells: list[list[dict[str, Any]]] = []
            for i, row in enumerate(matrix):
                row_cells: list[dict[str, Any]] = []
                for j, cell in enumerate(row):
                    if i == j:
                        row_cells.append({"kind": "self", "text": "—"})
                    elif cell.get("win") is None:
                        row_cells.append({"kind": "empty", "text": ""})
                    elif cell.get("win") == 1:
                        row_cells.append(
                            {
                                "kind": "win",
                                "text": cell.get("games") or "W",
                            }
                        )
                    else:
                        row_cells.append(
                            {
                                "kind": "loss",
                                "text": cell.get("games") or "L",
                            }
                        )
                cells.append(row_cells)
            heatmap = {
                "labels": labels,
                "rows": [
                    {"label": labels[i], "index": i + 1, "cells": cells[i]}
                    for i in range(len(labels))
                ],
            }

    kpi = {
        "participants_count": len(participants),
        "matches_total": matches_total,
        "matches_completed": completed_count,
        "progress_pct": progress_pct,
        "sets_played": sets_played,
        "games_total": games_total,
        "avg_games": avg_games,
        "three_set_pct": three_set_pct,
        "tiebreaks": tiebreak_count,
        "walkovers": len(walkovers),
    }

    charts = {
        "colors": {
            "primary": "#A6824A",
            "accent": "#83530cd3",
            "palette": ["#A6824A", "#83530c", "#2d5a27", "#6b7280", "#9ca3af"],
            "border": "#16302B",
            "success": "#2d5a27",
            "danger": "#9b3a3a",
            "grid": "rgba(148, 163, 184, 0.15)",
            "text": "#94a3b8",
        },
        "status": {"labels": chart_status_labels, "data": chart_status_data},
        "rounds": {"labels": chart_round_labels, "data": chart_round_data},
        "ratings": {"labels": ratings_labels, "data": ratings_sorted},
        "timeline": {
            "labels": timeline_labels,
            "daily": timeline_daily,
            "cumulative": timeline_cumulative,
        },
        "setScores": {"labels": set_labels, "data": set_data},
        "character": {"labels": character_labels, "data": character_data},
        "ratingDeltas": {"labels": delta_labels, "data": delta_values},
    }

    show_flags = {
        "show_chart_status": len(chart_status_labels) > 0,
        "show_chart_rounds": (is_fan or is_tvd) and len(chart_round_labels) > 0,
        "show_chart_ratings": len(ratings_sorted) > 0,
        "show_chart_timeline": len(timeline_labels) > 0,
        "show_chart_sets": len(set_labels) > 0,
        "show_chart_character": len(character_labels) > 0,
        "show_chart_deltas": len(delta_labels) > 0,
        "show_insights": len(insights) > 0,
        "show_heatmap": heatmap is not None,
    }

    return TablesDashboardData(
        kpi=kpi,
        charts=charts,
        insights=insights,
        heatmap=heatmap,
        show_flags=show_flags,
    )
