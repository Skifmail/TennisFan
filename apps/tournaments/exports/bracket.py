"""Раскладка сетки для PDF: пустая до жеребьёвки и заполненная после.

Колонки раундов, шахматки круговой системы и группы ТВД считаются здесь,
шаблон только рисует.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.utils import timezone

from apps.tournaments.fan import _bracket_params, _round_name
from apps.tournaments.models import (
    Match,
    Tournament,
    TournamentFormat,
    TournamentTeam,
    TVDGroup,
)
from apps.tournaments.round_robin import (
    compute_standings,
    get_match_matrix,
    order_matrix_by_standings,
)
from apps.tournaments.tvd import calculate_group_structure
from apps.users.models import Player

_COMPACT_FROM = 9
_SPLIT_FROM = 17
_GROUP_NAMES = "ABCDEF"


@dataclass(frozen=True)
class BracketSide:
    """Одна сторона матча в карточке сетки."""

    name: str
    is_winner: bool = False
    is_placeholder: bool = False
    is_bye: bool = False


@dataclass(frozen=True)
class BracketMatch:
    """Карточка матча."""

    side1: BracketSide
    side2: BracketSide
    score: str = ""
    deadline: str = ""


@dataclass(frozen=True)
class BracketRound:
    """Колонка одного раунда."""

    name: str
    matches: tuple[BracketMatch, ...]


@dataclass(frozen=True)
class BracketPage:
    """Одна страница сетки. Большие сетки делятся пополам."""

    rounds: tuple[BracketRound, ...]
    compact: bool
    short: bool = False


@dataclass(frozen=True)
class BracketBoard:
    """Основная, утешительная или плей-офф сетка."""

    title: str
    pages: tuple[BracketPage, ...]


@dataclass(frozen=True)
class ChessRow:
    """Строка шахматки."""

    name: str
    cells: tuple[str, ...]
    wins: str
    points: str
    place: str


@dataclass(frozen=True)
class ChessTable:
    """Таблица круговой системы или группы."""

    title: str
    headers: tuple[str, ...]
    rows: tuple[ChessRow, ...]


@dataclass(frozen=True)
class BracketLayout:
    """Всё, что нужно шаблону сетки."""

    kind: str
    headline: str
    format_label: str
    meta: tuple[tuple[str, str], ...]
    generated: bool
    status_note: str
    boards: tuple[BracketBoard, ...]
    tables: tuple[ChessTable, ...]
    footnote: str


def build_bracket_layout(tournament: Tournament) -> BracketLayout:
    """Собрать сетку турнира, в том числе пустую до формирования.

    Args:
        tournament: Турнир любого поддерживаемого формата.

    Returns:
        BracketLayout: Колонки, шахматки и подписи для PDF.
    """
    doubles = tournament.is_doubles()
    generated = bool(tournament.bracket_generated)
    boards: tuple[BracketBoard, ...]
    tables: tuple[ChessTable, ...]
    if tournament.format == TournamentFormat.ROUND_ROBIN:
        boards = ()
        tables = (_round_robin_table(tournament, doubles, generated),)
        footnote = ""
        kind = "round_robin"
    elif tournament.format == TournamentFormat.WEEKEND_DAY:
        tables, boards, footnote = _tvd_layout(tournament, doubles, generated)
        kind = "weekend_day"
    else:
        boards = _elimination_boards(tournament, doubles, generated)
        tables = ()
        footnote = ""
        kind = "elimination"
    return BracketLayout(
        kind=kind,
        headline=tournament.name,
        format_label=tournament.get_format_display(),
        meta=_meta(tournament),
        generated=generated,
        status_note=_status_note(tournament, generated),
        boards=boards,
        tables=tables,
        footnote=footnote,
    )


def _elimination_boards(
    tournament: Tournament,
    doubles: bool,
    generated: bool,
) -> tuple[BracketBoard, ...]:
    """Основная сетка и подвал, если он уже создан."""
    if not generated:
        rounds = _empty_elimination_rounds(_planned_draw_size(tournament))
        return (BracketBoard("Основная сетка", _paginate(rounds)),)
    main = list(
        tournament.matches.filter(is_consolation=False, tvd_group__isnull=True)
        .select_related(
            "player1__user",
            "player2__user",
            "winner__user",
            "team1__player1__user",
            "team1__player2__user",
            "team2__player1__user",
            "team2__player2__user",
            "winner_team",
        )
        .order_by("round_index", "round_order")
    )
    consolation = list(
        tournament.matches.filter(is_consolation=True, tvd_group__isnull=True)
        .select_related(
            "player1__user",
            "player2__user",
            "winner__user",
            "team1__player1__user",
            "team1__player2__user",
            "team2__player1__user",
            "team2__player2__user",
            "winner_team",
        )
        .order_by("placement_min", "round_index", "round_order")
    )
    if not main:
        rounds = _empty_elimination_rounds(_planned_draw_size(tournament))
        return (BracketBoard("Основная сетка", _paginate(rounds)),)
    grouped = _boards_by_placement(main, doubles, "Основная сетка")
    boards = [grouped[0]] if grouped else []
    boards.extend(_boards_by_placement(consolation, doubles, "Подвал"))
    return tuple(boards)


def _boards_by_placement(
    matches: list[Match],
    doubles: bool,
    default_title: str,
) -> list[BracketBoard]:
    """Сгруппировать матчи по диапазону мест, внутри — по раундам."""
    buckets: dict[tuple[int, int], list[Match]] = {}
    order: list[tuple[int, int]] = []
    for match in matches:
        key = (match.placement_min or 0, match.placement_max or 0)
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(match)
    boards: list[BracketBoard] = []
    for key in order:
        low, high = key
        if low and high:
            title = f"Места {low}–{high}" if low != high else f"Место {low}"
        else:
            title = default_title
        rounds = _rounds_from_matches(buckets[key], doubles)
        boards.append(BracketBoard(title, _paginate(rounds)))
    return boards


def _empty_elimination_rounds(draw_size: int) -> tuple[BracketRound, ...]:
    """Пустые колонки размера ближайшей степени двойки."""
    bracket_size, total_rounds = _bracket_params(draw_size)
    rounds: list[BracketRound] = []
    for offset in range(total_rounds):
        round_index = offset + 1
        match_count = bracket_size // (2 ** (offset + 1))
        name = "Финал" if round_index == total_rounds else _round_name(round_index)
        matches: list[BracketMatch] = []
        for order in range(match_count):
            if offset == 0:
                side1 = _placeholder(f"Позиция {order + 1}")
                side2 = _placeholder(f"Позиция {bracket_size - order}")
            else:
                side1 = _placeholder("—")
                side2 = _placeholder("—")
            matches.append(BracketMatch(side1=side1, side2=side2))
        rounds.append(BracketRound(name=name, matches=tuple(matches)))
    return tuple(rounds)


def _rounds_from_matches(
    matches: list[Match], doubles: bool
) -> tuple[BracketRound, ...]:
    """Колонки по round_index в порядке жеребьёвки."""
    grouped: dict[int, list[Match]] = {}
    names: dict[int, str] = {}
    for match in matches:
        grouped.setdefault(match.round_index, []).append(match)
        names.setdefault(
            match.round_index, match.round_name or f"Раунд {match.round_index}"
        )
    rounds: list[BracketRound] = []
    for index in sorted(grouped):
        ordered = sorted(grouped[index], key=lambda item: item.round_order)
        rounds.append(
            BracketRound(
                name=names[index],
                matches=tuple(_match_card(match, doubles) for match in ordered),
            )
        )
    return tuple(rounds)


def _match_card(match: Match, doubles: bool) -> BracketMatch:
    """Карточка с именами, счётом и дедлайном."""
    return BracketMatch(
        side1=_side(match, 1, doubles),
        side2=_side(match, 2, doubles),
        score=_score_text(match),
        deadline=_deadline_text(match),
    )


def _side(match: Match, number: int, doubles: bool) -> BracketSide:
    """Имя стороны и признак победителя."""
    if doubles:
        team = match.team1 if number == 1 else match.team2
        if team is None:
            return _placeholder("—")
        is_bye = bool(getattr(team.player1, "is_bye", False))
        is_winner = match.winner_team_id is not None and match.winner_team_id == team.id
        return BracketSide(
            name=team.get_display_name(),
            is_winner=is_winner and not is_bye,
            is_bye=is_bye,
        )
    player = match.player1 if number == 1 else match.player2
    if player is None:
        return _placeholder("—")
    is_bye = bool(getattr(player, "is_bye", False))
    is_winner = match.winner_id is not None and match.winner_id == player.id
    return BracketSide(
        name=player.get_display_name(),
        is_winner=is_winner and not is_bye,
        is_bye=is_bye,
    )


def _round_robin_table(
    tournament: Tournament,
    doubles: bool,
    generated: bool,
) -> ChessTable:
    """Шахматка: пустая по плановому составу или с результатами."""
    del doubles
    if not generated:
        return _empty_chess("Круговая таблица", _planned_draw_size(tournament))
    participants, matrix = get_match_matrix(tournament)
    standings = compute_standings(tournament)
    if standings:
        participants, matrix = order_matrix_by_standings(
            participants, matrix, standings
        )
    if not participants:
        return _empty_chess("Круговая таблица", _planned_draw_size(tournament))
    by_id = {}
    for row in standings:
        entity = row.get("team") or row.get("player")
        if entity is not None:
            by_id[entity.id] = row
    return _chess_from_matrix("Круговая таблица", participants, matrix, by_id)


def _tvd_layout(
    tournament: Tournament,
    doubles: bool,
    generated: bool,
) -> tuple[tuple[ChessTable, ...], tuple[BracketBoard, ...], str]:
    """Группы ТВД и плей-офф. До жеребьёвки — пустой шаблон групп."""
    if not generated:
        size = _planned_draw_size(tournament)
        sizes = calculate_group_structure(size) if size >= 4 else [3, 3]
        tables = tuple(
            _empty_chess(f"Группа {_GROUP_NAMES[index]}", group_size)
            for index, group_size in enumerate(sizes)
            if index < len(_GROUP_NAMES)
        )
        footnote = _join_notes(
            (
                (
                    "Группы будут пересчитаны, когда наберётся минимум 4 участника. "
                    "Ниже — предварительный шаблон."
                )
                if size < 4
                else ""
            ),
            "Плей-офф формируется после группового этапа.",
        )
        return tables, (), footnote
    tables = tuple(
        _group_chess(group, doubles)
        for group in tournament.tvd_groups.order_by("order", "name")
    )
    playoff = list(
        tournament.matches.filter(tvd_group__isnull=True)
        .select_related(
            "player1__user",
            "player2__user",
            "winner__user",
            "team1__player1__user",
            "team2__player1__user",
            "winner_team",
        )
        .order_by("is_consolation", "round_index", "round_order")
    )
    main = [match for match in playoff if not match.is_consolation]
    consolation = [match for match in playoff if match.is_consolation]
    boards: list[BracketBoard] = []
    if main:
        boards.append(
            BracketBoard("Плей-офф", _paginate(_rounds_from_matches(main, doubles)))
        )
    boards.extend(_boards_by_placement(consolation, doubles, "Утешительная сетка"))
    footnote = "" if boards else "Плей-офф ещё не сформирован."
    return tables, tuple(boards), footnote


def _group_chess(group: TVDGroup, doubles: bool) -> ChessTable:
    """Шахматка одной группы по её матчам."""
    members = list(
        group.members.select_related(
            "player__user",
            "team__player1__user",
            "team__player2__user",
        ).order_by("seed", "pk")
    )
    entities: list[Player | TournamentTeam] = []
    stats: dict[int, object] = {}
    for member in members:
        entity = member.team if member.team_id else member.player
        if entity is None:
            continue
        entities.append(entity)
        stats[entity.id] = member
    if not entities:
        return _empty_chess(f"Группа {group.name}", 3)
    index_by_id = {entity.id: index for index, entity in enumerate(entities)}
    size = len(entities)
    games = [["" for _ in range(size)] for _ in range(size)]
    for match in group.matches.all():
        _fill_group_cell(games, index_by_id, match, doubles)
    headers = tuple(str(number) for number in range(1, size + 1))
    rows: list[ChessRow] = []
    for index, entity in enumerate(entities):
        member = stats[entity.id]
        place = getattr(member, "final_place", None)
        wins = getattr(member, "wins", 0)
        cells = tuple(
            "—" if index == column else games[index][column] for column in range(size)
        )
        rows.append(
            ChessRow(
                name=_entity_label(entity),
                cells=cells,
                wins=str(wins),
                points=str(wins),
                place=str(place) if place else "—",
            )
        )
    return ChessTable(title=f"Группа {group.name}", headers=headers, rows=tuple(rows))


def _fill_group_cell(
    games: list[list[str]],
    index_by_id: dict[int, int],
    match: Match,
    doubles: bool,
) -> None:
    """Записать счёт матча с точки зрения каждой стороны."""
    if doubles:
        left_id, right_id = match.team1_id, match.team2_id
    else:
        left_id, right_id = match.player1_id, match.player2_id
    if left_id not in index_by_id or right_id not in index_by_id:
        return
    left = index_by_id[left_id]
    right = index_by_id[right_id]
    own, other = _set_games(match)
    if not own and match.status != Match.MatchStatus.WALKOVER:
        return
    if match.status == Match.MatchStatus.WALKOVER and not own:
        games[left][right] = "тех."
        games[right][left] = "тех."
        return
    games[left][right] = own
    games[right][left] = other


def _chess_from_matrix(
    title: str,
    entities: list[Player | TournamentTeam],
    matrix: list[list[dict[str, object]]],
    standings_by_id: dict[int, dict[str, object]],
) -> ChessTable:
    """Шахматка кругового турнира в порядке мест."""
    headers = tuple(str(number) for number in range(1, len(entities) + 1))
    rows: list[ChessRow] = []
    for index, entity in enumerate(entities):
        cells: list[str] = []
        for column, cell in enumerate(matrix[index]):
            if index == column:
                cells.append("—")
                continue
            raw = cell.get("games")
            cells.append(str(raw).replace("/", ":") if raw else "")
        standing = standings_by_id.get(entity.id, {})
        place = standing.get("place")
        wins = standing.get("wins", "—")
        points = standing.get("points", wins)
        rows.append(
            ChessRow(
                name=_entity_label(entity),
                cells=tuple(cells),
                wins=str(wins),
                points=str(points),
                place=str(place) if place else "—",
            )
        )
    return ChessTable(title=title, headers=headers, rows=tuple(rows))


def _empty_chess(title: str, size: int) -> ChessTable:
    """Пустая шахматка с номерами позиций."""
    safe = max(size, 2)
    headers = tuple(str(number) for number in range(1, safe + 1))
    rows = tuple(
        ChessRow(
            name=f"Позиция {index + 1}",
            cells=tuple("—" if index == column else "" for column in range(safe)),
            wins="—",
            points="—",
            place="—",
        )
        for index in range(safe)
    )
    return ChessTable(title=title, headers=headers, rows=rows)


def _paginate(rounds: tuple[BracketRound, ...]) -> tuple[BracketPage, ...]:
    """Одна страница или две половины, если первый круг не помещается."""
    if not rounds:
        return ()
    first_count = len(rounds[0].matches)
    compact = first_count >= _COMPACT_FROM
    if first_count < _SPLIT_FROM:
        return (BracketPage(rounds=rounds, compact=compact, short=first_count <= 2),)
    middle = first_count // 2
    pages: list[BracketPage] = []
    for half_start in (0, middle):
        page_rounds: list[BracketRound] = []
        start = half_start
        count = middle
        for round_item in rounds:
            chunk = round_item.matches[start : start + count]
            if not chunk and round_item.matches:
                chunk = round_item.matches[:1]
            page_rounds.append(BracketRound(name=round_item.name, matches=tuple(chunk)))
            start //= 2
            count = max(1, count // 2)
        pages.append(BracketPage(rounds=tuple(page_rounds), compact=True, short=False))
    return tuple(pages)


def _planned_draw_size(tournament: Tournament) -> int:
    """Сколько мест рисовать до жеребьёвки: не меньше минимума и не больше максимума."""
    if tournament.is_doubles():
        current = int(tournament.teams.filter(player2__isnull=False).count())
        minimum = int(tournament.min_teams or 0)
        maximum = tournament.max_teams
    else:
        current = int(tournament.participants.filter(is_bye=False).count())
        minimum = int(tournament.min_participants or 0)
        maximum = tournament.max_participants
    size = max(current, minimum)
    if maximum:
        size = min(size, int(maximum))
    return max(size, 2)


def _status_note(tournament: Tournament, generated: bool) -> str:
    """Подпись «предварительная» или дата формирования."""
    if not generated:
        return "Предварительная сетка"
    first_created = (
        tournament.matches.order_by("created_at")
        .values_list("created_at", flat=True)
        .first()
    )
    if first_created is None:
        return "Сетка сформирована"
    moment = timezone.localtime(first_created)
    return f"Сетка сформирована {moment.strftime('%d.%m.%Y')}"


def _meta(tournament: Tournament) -> tuple[tuple[str, str], ...]:
    """Короткие факты в шапке листа сетки."""
    if tournament.start_date:
        dates = tournament.start_date.strftime("%d.%m.%Y")
        if tournament.end_date:
            dates = f"{dates} — {tournament.end_date.strftime('%d.%m.%Y')}"
    elif tournament.start_after_fill:
        dates = "Старт после набора"
    else:
        dates = "Даты не указаны"
    place = tournament.city or "Место не указано"
    if tournament.court_id and tournament.court is not None:
        place = f"{place}, {tournament.court.name}"
    return (
        ("Формат", tournament.get_format_display()),
        ("Вариант", tournament.get_variant_display()),
        ("Даты", dates),
        ("Место", place),
        ("Статус", tournament.get_status_display()),
    )


def _placeholder(name: str) -> BracketSide:
    """Пустой слот до жеребьёвки."""
    return BracketSide(name=name, is_placeholder=True)


def _entity_label(entity: Player | TournamentTeam) -> str:
    """Имя игрока или пары."""
    if hasattr(entity, "get_display_name"):
        return str(entity.get_display_name())
    return str(entity)


def _score_text(match: Match) -> str:
    """Счёт по сетам или пометка о техническом результате."""
    text = match.score_display
    if text and text != "—":
        return text
    if match.status == Match.MatchStatus.WALKOVER:
        return "тех."
    return ""


def _deadline_text(match: Match) -> str:
    """Дедлайн матча, если он задан."""
    if not match.deadline:
        return ""
    moment = match.deadline
    if timezone.is_aware(moment):
        moment = timezone.localtime(moment)
    return str(moment.strftime("%d.%m.%Y"))


def _set_games(match: Match) -> tuple[str, str]:
    """Счёт геймов «сторона 1» и зеркало для стороны 2."""
    left: list[str] = []
    right: list[str] = []
    for index in range(1, 4):
        games_left = getattr(match, f"player1_set{index}")
        games_right = getattr(match, f"player2_set{index}")
        if games_left is None or games_right is None:
            continue
        left.append(f"{games_left}:{games_right}")
        right.append(f"{games_right}:{games_left}")
    return " ".join(left), " ".join(right)


def _join_notes(*parts: str) -> str:
    """Склеить непустые примечания."""
    return " ".join(part for part in parts if part)
