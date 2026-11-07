"""Ручной режим формирования пар в турнирах.

Организатор запускает турнир, после чего сам составляет пары из
зарегистрированных участников. Модуль отвечает за запуск, проверку
составленных пар, создание матчей и автоматическое продолжение турнира,
если организатор выбрал автоматические следующие круги.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import cast

from django.db import transaction
from django.utils import timezone

from apps.users.models import Player

from .fan import _get_bye_player, _get_or_create_bye_team
from .models import (
    Match,
    PairingMode,
    Tournament,
    TournamentFormat,
    TournamentStatus,
    TournamentTeam,
)
from .utils import tournament_deadline_schedule_start

logger = logging.getLogger(__name__)

SUPPORTED_FORMATS: tuple[str, ...] = (
    "single_elimination",
    "round_robin",
)

FINAL_STATUSES: tuple[str, ...] = (
    "completed",
    "walkover",
)

# Защита от рекурсии: создание круга с bye-матчем вызывает post_save,
# который снова проверяет готовность следующего круга.
_ROUND_CREATION_GUARD: set[int] = set()


class ManualPairingError(ValueError):
    """Ошибка ручного формирования пар."""


@dataclass(frozen=True)
class PairingEntity:
    """Участник, которого можно поставить в пару."""

    token: str
    pk: int
    name: str
    strength: float
    rating: float
    player: Player | None = None
    team: TournamentTeam | None = None

    @property
    def is_team(self) -> bool:
        """Участник является командой парного турнира.

        Returns:
            bool: True для команды.
        """
        return self.team is not None

    @property
    def strength_label(self) -> str:
        """Короткая подпись уровня силы.

        Returns:
            str: Уровень силы с двумя знаками после запятой.
        """
        return f"{self.strength:.2f}"


@dataclass
class PairingRound:
    """Сохранённый круг пар."""

    index: int
    label: str
    matches: list[Match]
    is_complete: bool


@dataclass
class PairingState:
    """Состояние страницы ручного формирования пар."""

    is_started: bool
    is_complete: bool
    can_build: bool
    build_round: int
    build_label: str
    eligible: list[PairingEntity] = field(default_factory=list)
    rounds: list[PairingRound] = field(default_factory=list)
    open_round: int = 0
    previously_played: dict[str, list[int]] = field(default_factory=dict)
    total_entities: int = 0
    bye_count: int = 0


def supports_manual_pairing(tournament: Tournament) -> bool:
    """Проверить, поддерживает ли формат турнира ручные пары.

    Args:
        tournament: Турнир.

    Returns:
        bool: True, если формат поддерживается.
    """
    return getattr(tournament, "format", None) in SUPPORTED_FORMATS


def _ensure_supported(tournament: Tournament) -> None:
    """Проверить формат турнира.

    Args:
        tournament: Турнир.

    Raises:
        ManualPairingError: Если формат не поддерживает ручные пары.
    """
    if not supports_manual_pairing(tournament):
        raise ManualPairingError(
            "Ручное формирование пар доступно для олимпийской системы "
            "(до 1 поражения) и кругового турнира."
        )


def _withdrawn_ids(tournament: Tournament) -> tuple[set[int], set[int]]:
    """Вернуть идентификаторы снявшихся игроков и команд.

    Args:
        tournament: Турнир.

    Returns:
        tuple[set[int], set[int]]: Множества ID игроков и команд.
    """
    from .withdraw import get_withdrawn_player_ids, get_withdrawn_team_ids

    return (
        set(get_withdrawn_player_ids(tournament)),
        set(get_withdrawn_team_ids(tournament)),
    )


def load_entities(tournament: Tournament) -> list[PairingEntity]:
    """Загрузить участников, доступных для составления пар.

    Сортировка: от сильнейшего к слабейшему. Для пар — по среднему уровню силы.

    Args:
        tournament: Турнир.

    Returns:
        list[PairingEntity]: Участники в порядке силы.
    """
    withdrawn_players, withdrawn_teams = _withdrawn_ids(tournament)
    if tournament.is_doubles():
        teams = (
            tournament.teams.filter(player2__isnull=False)
            .exclude(player1__is_bye=True)
            .select_related("player1__user", "player2__user")
        )
        entities: list[PairingEntity] = []
        for team in teams:
            if team.pk in withdrawn_teams:
                continue
            strengths = [float(team.player1.ntrp_level or 0)]
            if team.player2_id:
                strengths.append(float(team.player2.ntrp_level or 0))
            strength = sum(strengths) / max(1, len(strengths))
            rating = float(team.player1.total_points or 0) + float(
                team.player2.total_points or 0 if team.player2 else 0
            )
            entities.append(
                PairingEntity(
                    token=f"t:{team.pk}",
                    pk=team.pk,
                    name=team.get_display_name(),
                    strength=strength,
                    rating=rating,
                    team=team,
                )
            )
    else:
        players = (
            tournament.participants.exclude(is_bye=True)
            .select_related("user")
            .order_by("user__last_name", "user__first_name")
        )
        entities = [
            PairingEntity(
                token=f"p:{player.pk}",
                pk=player.pk,
                name=player.get_display_name(),
                strength=float(player.ntrp_level or 0),
                rating=float(player.total_points or 0),
                player=player,
            )
            for player in players
            if player.pk not in withdrawn_players
        ]

    entities.sort(
        key=lambda entity: (
            -entity.strength,
            -entity.rating,
            entity.name.casefold(),
        )
    )
    return entities


def match_entity_token(match: Match, side: int) -> str | None:
    """Вернуть токен участника на стороне матча.

    Args:
        match: Матч.
        side: 1 или 2.

    Returns:
        str | None: Токен участника или None.
    """
    if side == 1:
        if match.team1_id:
            return f"t:{match.team1_id}"
        if match.player1_id:
            return f"p:{match.player1_id}"
    else:
        if match.team2_id:
            return f"t:{match.team2_id}"
        if match.player2_id:
            return f"p:{match.player2_id}"
    return None


def pair_key(token_a: str, token_b: str) -> str:
    """Канонический ключ пары (без учёта порядка сторон).

    Args:
        token_a: Первый участник.
        token_b: Второй участник.

    Returns:
        str: Ключ пары.
    """
    return "|".join(sorted((token_a, token_b)))


def _main_matches(tournament: Tournament):
    """QuerySet основных (не утешительных) матчей турнира.

    Args:
        tournament: Турнир.

    Returns:
        QuerySet: Матчи основной сетки.
    """
    return tournament.matches.filter(is_consolation=False)


def _round_matches(tournament: Tournament, round_index: int) -> list[Match]:
    """Матчи конкретного круга.

    Args:
        tournament: Турнир.
        round_index: Номер круга.

    Returns:
        list[Match]: Матчи круга.
    """
    return list(
        _main_matches(tournament)
        .filter(round_index=round_index)
        .select_related(
            "player1__user",
            "player2__user",
            "winner__user",
            "team1__player1__user",
            "team1__player2__user",
            "team2__player1__user",
            "team2__player2__user",
            "winner_team__player1__user",
            "winner_team__player2__user",
        )
        .order_by("round_order", "pk")
    )


def _is_complete(match: Match) -> bool:
    """Проверить, завершён ли матч.

    Args:
        match: Матч.

    Returns:
        bool: True для завершённого матча или тех. результата.
    """
    return match.status in FINAL_STATUSES


def _round_is_complete(matches: Iterable[Match]) -> bool:
    """Проверить, все ли матчи круга завершены.

    Args:
        matches: Матчи круга.

    Returns:
        bool: True, если все матчи завершены.
    """
    return all(_is_complete(match) for match in matches)


def get_round_winners(tournament: Tournament, round_index: int) -> list[PairingEntity]:
    """Вернуть победителей круга (без bye-участников).

    Args:
        tournament: Турнир.
        round_index: Номер круга.

    Returns:
        list[PairingEntity]: Победители в порядке матчей.
    """
    tokens: list[str] = []
    for match in _round_matches(tournament, round_index):
        if not _is_complete(match):
            continue
        if tournament.is_doubles():
            if match.winner_team_id and not getattr(
                match.winner_team.player1, "is_bye", False
            ):
                tokens.append(f"t:{match.winner_team_id}")
        elif match.winner_id and not getattr(match.winner, "is_bye", False):
            tokens.append(f"p:{match.winner_id}")

    by_token = {entity.token: entity for entity in load_entities(tournament)}
    return [by_token[token] for token in tokens if token in by_token]


def get_previously_played(
    tournament: Tournament, exclude_round: int | None = None
) -> dict[str, list[int]]:
    """Собрать уже сыгранные пары и номера кругов.

    Args:
        tournament: Турнир.
        exclude_round: Круг, который нужно исключить.

    Returns:
        dict[str, list[int]]: Ключ пары → номера кругов.
    """
    played: dict[str, list[int]] = {}
    for match in _main_matches(tournament).select_related(
        "player1", "player2", "team1", "team2"
    ):
        if exclude_round is not None and match.round_index == exclude_round:
            continue
        token_a = match_entity_token(match, 1)
        token_b = match_entity_token(match, 2)
        if not token_a or not token_b:
            continue
        key = pair_key(token_a, token_b)
        played.setdefault(key, [])
        if match.round_index not in played[key]:
            played[key].append(match.round_index)
    for rounds in played.values():
        rounds.sort()
    return played


def _round_label(tournament: Tournament, round_index: int, pairs_count: int) -> str:
    """Название круга для отображения.

    Args:
        tournament: Турнир.
        round_index: Номер круга.
        pairs_count: Количество пар в круге.

    Returns:
        str: Название круга.
    """
    if getattr(tournament, "format", None) == TournamentFormat.ROUND_ROBIN:
        return f"Тур {round_index}"
    if pairs_count <= 1:
        return "Финал"
    if pairs_count == 2:
        return "Полуфинал"
    if pairs_count == 4:
        return "1/4 финала"
    if pairs_count == 8:
        return "1/8 финала"
    return f"{round_index} круг"


def _deadline(tournament: Tournament, round_index: int):
    """Дедлайн матчей круга.

    Args:
        tournament: Турнир.
        round_index: Номер круга.

    Returns:
        datetime: Дедлайн.
    """
    start = tournament_deadline_schedule_start(tournament)
    days = int(getattr(tournament, "match_days_per_round", 7) or 7)
    return start + timedelta(days=days) * round_index


def _assign_match_side(
    match: Match, side: int, entity: PairingEntity, doubles: bool
) -> None:
    """Поставить участника на сторону матча.

    Args:
        match: Матч.
        side: 1 или 2.
        entity: Участник.
        doubles: Парный ли матч.
    """
    if doubles:
        team = entity.team
        player = team.player1 if team else None
        if side == 1:
            match.team1 = team
            match.player1 = player
        else:
            match.team2 = team
            match.player2 = player
        return
    if side == 1:
        match.player1 = entity.player
    else:
        match.player2 = entity.player


def _create_match(
    tournament: Tournament,
    *,
    round_index: int,
    round_name: str,
    round_order: int,
    entity_a: PairingEntity,
    entity_b: PairingEntity | None,
    bye: bool = False,
) -> Match:
    """Создать матч из участников.

    Args:
        tournament: Турнир.
        round_index: Номер круга.
        round_name: Название круга.
        round_order: Порядок матча в круге.
        entity_a: Первый участник.
        entity_b: Второй участник (None для bye).
        bye: Признак технического прохода.

    Returns:
        Match: Созданный матч.
    """
    kwargs: dict[str, object] = {
        "tournament": tournament,
        "round_name": round_name,
        "round_index": round_index,
        "round_order": round_order,
        "is_consolation": False,
        "deadline": _deadline(tournament, round_index),
        "status": Match.MatchStatus.SCHEDULED,
    }
    if tournament.is_doubles():
        kwargs["team1"] = entity_a.team
        kwargs["player1"] = entity_a.team.player1 if entity_a.team else None
        if entity_b and entity_b.team:
            kwargs["team2"] = entity_b.team
            kwargs["player2"] = entity_b.team.player1
    else:
        kwargs["player1"] = entity_a.player
        if entity_b and entity_b.player:
            kwargs["player2"] = entity_b.player

    if bye and entity_b is not None:
        kwargs["status"] = Match.MatchStatus.WALKOVER
        kwargs["completed_datetime"] = timezone.now()
        if tournament.is_doubles():
            kwargs["winner_team"] = entity_a.team
            kwargs["winner"] = entity_a.team.player1 if entity_a.team else None
        else:
            kwargs["winner"] = entity_a.player

    return cast(Match, Match.objects.create(**kwargs))


def _bye_entity(tournament: Tournament) -> PairingEntity | None:
    """Собрать служебного участника для bye-матча.

    Args:
        tournament: Турнир.

    Returns:
        PairingEntity | None: Служебный участник или None.
    """
    bye_player = _get_bye_player()
    if bye_player is None:
        return None
    if tournament.is_doubles():
        team = _get_or_create_bye_team(tournament, bye_player)
        return PairingEntity(
            token=f"t:{team.pk}",
            pk=team.pk,
            name="Свободный круг",
            strength=0,
            rating=0,
            team=team,
        )
    return PairingEntity(
        token=f"p:{bye_player.pk}",
        pk=bye_player.pk,
        name="Свободный круг",
        strength=0,
        rating=0,
        player=bye_player,
    )


def _winner_token(match: Match) -> str:
    """Вернуть токен победителя матча.

    Args:
        match: Матч.

    Returns:
        str: Токен ``p:<id>`` или ``t:<id>``, либо пустая строка.
    """
    if match.winner_team_id:
        return f"t:{match.winner_team_id}"
    if match.winner_id:
        return f"p:{match.winner_id}"
    return ""


def _link_previous_round(
    tournament: Tournament, previous_round: int, next_matches: list[Match]
) -> None:
    """Связать матчи предыдущего круга со следующим.

    Связь нужна для корректного определения финала и отображения сетки.
    Участники следующего круга уже заполнены вручную или автоматически.

    Args:
        tournament: Турнир.
        previous_round: Номер предыдущего круга.
        next_matches: Матчи следующего круга.
    """
    if not next_matches:
        return
    previous_matches = _round_matches(tournament, previous_round)
    for index, match in enumerate(previous_matches):
        winner_token = _winner_token(match)
        target = None
        if winner_token:
            for candidate in next_matches:
                tokens = (
                    match_entity_token(candidate, 1),
                    match_entity_token(candidate, 2),
                )
                if winner_token in tokens:
                    target = candidate
                    break
        if target is None:
            target = next_matches[min(index // 2, len(next_matches) - 1)]
        if match.next_match_id != target.pk:
            match.next_match = target
            match.save(update_fields=["next_match"])


def _create_round_matches(
    tournament: Tournament,
    *,
    round_index: int,
    pairs: list[tuple[PairingEntity, PairingEntity]],
    leftover: PairingEntity | None,
    auto_bye: bool,
) -> list[Match]:
    """Создать все матчи круга.

    Args:
        tournament: Турнир.
        round_index: Номер круга.
        pairs: Пары участников.
        leftover: Участник без пары (bye).
        auto_bye: Создавать ли bye-матч (для олимпийской системы).

    Returns:
        list[Match]: Созданные матчи.
    """
    pairs_count = len(pairs) + (1 if auto_bye and leftover else 0)
    round_name = _round_label(tournament, round_index, pairs_count)
    created: list[Match] = []
    tournament_id = tournament.pk
    _ROUND_CREATION_GUARD.add(tournament_id)
    try:
        for order, (entity_a, entity_b) in enumerate(pairs, start=1):
            created.append(
                _create_match(
                    tournament,
                    round_index=round_index,
                    round_name=round_name,
                    round_order=order,
                    entity_a=entity_a,
                    entity_b=entity_b,
                )
            )
        if auto_bye and leftover:
            bye = _bye_entity(tournament)
            if bye is not None:
                created.append(
                    _create_match(
                        tournament,
                        round_index=round_index,
                        round_name=round_name,
                        round_order=len(created) + 1,
                        entity_a=leftover,
                        entity_b=bye,
                        bye=True,
                    )
                )
    finally:
        _ROUND_CREATION_GUARD.discard(tournament_id)
    if tournament.format == TournamentFormat.SINGLE_ELIMINATION:
        _link_previous_round(tournament, round_index - 1, created)
    return created


def is_creating_round(tournament_id: int) -> bool:
    """Проверить, идёт ли создание круга (защита от рекурсии сигналов).

    Args:
        tournament_id: ID турнира.

    Returns:
        bool: True, если круг ещё создаётся.
    """
    return tournament_id in _ROUND_CREATION_GUARD


def start_manual_tournament(tournament: Tournament) -> tuple[bool, str]:
    """Запустить турнир в ручном режиме без формирования матчей.

    Срабатывает стандартный сценарий старта: регистрация закрывается,
    турнир становится активным, участники получают уведомление о начале.
    Пары организатор составляет отдельно.

    Args:
        tournament: Турнир.

    Returns:
        tuple[bool, str]: Успех и сообщение.
    """
    _ensure_supported(tournament)
    if tournament.status in (TournamentStatus.COMPLETED, TournamentStatus.CANCELLED):
        return False, "Завершённый или отменённый турнир нельзя запустить."
    if tournament.manual_pairing_has_started():
        return True, "Турнир уже запущен в ручном режиме."
    if tournament.bracket_generated:
        return False, "Сетка уже сформирована в автоматическом режиме."
    if tournament.is_doubles():
        count = tournament.full_teams_count()
    else:
        count = tournament.participants.exclude(is_bye=True).count()
    if count < 2:
        return False, "Нужно минимум 2 участника, чтобы запустить турнир."

    tournament.pairing_mode = PairingMode.MANUAL
    tournament.manual_started_at = timezone.now()
    tournament.bracket_generated = True
    update_fields = [
        "pairing_mode",
        "manual_started_at",
        "bracket_generated",
        "updated_at",
    ]
    if tournament.status == TournamentStatus.UPCOMING:
        tournament.status = TournamentStatus.ACTIVE
        update_fields.append("status")
    tournament.save(update_fields=update_fields)

    try:
        from apps.telegram_bot.notifications import notify_tournament_start

        notify_tournament_start(tournament)
    except Exception:
        logger.exception(
            "notify_tournament_start for manual tournament %s", tournament.slug
        )

    logger.info("Manual tournament started: %s", tournament.slug)
    return (
        True,
        "Турнир запущен. Участники получили уведомление о начале — "
        "теперь составьте пары первого круга.",
    )


def _build_matching(
    entities: list[PairingEntity],
    played: set[str],
    *,
    allow_rematch: bool,
) -> list[tuple[PairingEntity, PairingEntity]] | None:
    """Найти пары на круг без повторений.

    Используется минимальный перебор с эвристикой: первым берётся участник
    с наименьшим числом доступных соперников.

    Args:
        entities: Участники круга.
        played: Уже сыгранные пары.
        allow_rematch: Разрешить повторные пары.

    Returns:
        list[tuple[PairingEntity, PairingEntity]] | None: Пары или None.
    """
    if not entities:
        return []
    if len(entities) % 2 != 0:
        return None
    remaining = list(entities)
    result: list[tuple[PairingEntity, PairingEntity]] = []

    def candidates(entity: PairingEntity) -> list[PairingEntity]:
        options = []
        for opponent in remaining:
            if opponent.token == entity.token:
                continue
            if not allow_rematch and pair_key(entity.token, opponent.token) in played:
                continue
            options.append(opponent)
        return options

    def search() -> bool:
        if not remaining:
            return True
        entity = min(remaining, key=lambda item: len(candidates(item)))
        options = sorted(
            candidates(entity),
            key=lambda item: abs(item.strength - entity.strength),
        )
        if not options:
            return False
        remaining.remove(entity)
        for opponent in options:
            if opponent not in remaining:
                continue
            remaining.remove(opponent)
            result.append((entity, opponent))
            if search():
                return True
            result.pop()
            remaining.append(opponent)
        remaining.append(entity)
        remaining.sort(key=lambda item: entities.index(item))
        return False

    return result if search() else None


def _round_robin_schedule(
    entities: list[PairingEntity],
    first_pairs: list[tuple[PairingEntity, PairingEntity]],
    first_leftover: PairingEntity | None,
    start_round: int,
) -> list[tuple[int, list[tuple[PairingEntity, PairingEntity]], PairingEntity | None]]:
    """Достроить расписание кругового турнира по методу круга.

    Первый тур уже составлен организатором, а оставшиеся туры получаются
    вращением позиций. Метод круга гарантирует полное расписание без
    повторных пар, поэтому используется всегда, когда есть только первый тур.

    Args:
        entities: Все участники.
        first_pairs: Пары, уже составленные вручную.
        first_leftover: Участник, отдыхающий в первом туре.
        start_round: Номер первого автоматического тура.

    Returns:
        list[tuple[int, list[tuple[PairingEntity, PairingEntity]], PairingEntity | None]]:
        Список ``(номер тура, пары, отдыхающий)``.

    Raises:
        ManualPairingError: Если первый тур заполнен некорректно.
    """
    count = len(entities)
    if count < 2:
        return []

    dummy = PairingEntity(
        token="__round_robin_bye__",
        pk=0,
        name="",
        strength=0,
        rating=0,
    )
    slots_size = count if count % 2 == 0 else count + 1
    slots: list[PairingEntity | None] = [None] * slots_size
    for index, (entity_a, entity_b) in enumerate(first_pairs):
        slots[index] = entity_a
        slots[slots_size - 1 - index] = entity_b
    if slots_size != count:
        middle = slots_size // 2 - 1
        slots[middle] = first_leftover
        slots[middle + 1] = dummy
    if any(slot is None for slot in slots):
        raise ManualPairingError(
            "Первый тур заполнен не полностью — расписание достроить нельзя."
        )

    current: list[PairingEntity] = [slot for slot in slots if slot is not None]
    total_rounds = count - 1 if count % 2 == 0 else count
    schedule: list[
        tuple[int, list[tuple[PairingEntity, PairingEntity]], PairingEntity | None]
    ] = []
    for round_index in range(start_round, total_rounds + 1):
        last = current[-1]
        current = [current[0], last, *current[1:-1]]
        pairs: list[tuple[PairingEntity, PairingEntity]] = []
        leftover: PairingEntity | None = None
        for index in range(slots_size // 2):
            entity_a = current[index]
            entity_b = current[slots_size - 1 - index]
            if entity_a is dummy:
                leftover = entity_b
            elif entity_b is dummy:
                leftover = entity_a
            else:
                pairs.append((entity_a, entity_b))
        schedule.append((round_index, pairs, leftover))
    return schedule


def _create_missing_round_robin_rounds(tournament: Tournament) -> int:
    """Достроить недостающие туры кругового турнира без повторных пар.

    Берёт все уже созданные туры (включая составленные вручную) и подбирает
    пары для оставшихся туров так, чтобы ни одна пара не повторилась.
    Для нечётного числа участников каждый тур добавляет одного отдыхающего.

    Args:
        tournament: Турнир.

    Returns:
        int: Количество созданных туров.

    Raises:
        ManualPairingError: Если расписание достроить нельзя.
    """
    entities = load_entities(tournament)
    count = len(entities)
    if count < 2:
        return 0

    total_rounds = count - 1 if count % 2 == 0 else count
    existing_rounds = sorted(
        {int(match.round_index or 0) for match in _main_matches(tournament)}
    )
    if not existing_rounds:
        return 0
    last_round = existing_rounds[-1]
    if last_round >= total_rounds:
        return 0

    if existing_rounds == [1]:
        by_token = {entity.token: entity for entity in entities}
        first_pairs: list[tuple[PairingEntity, PairingEntity]] = []
        used_tokens: set[str] = set()
        for match in _round_matches(tournament, 1):
            token_a = match_entity_token(match, 1)
            token_b = match_entity_token(match, 2)
            if not token_a or not token_b:
                continue
            entity_a = by_token.get(token_a)
            entity_b = by_token.get(token_b)
            if entity_a is None or entity_b is None:
                continue
            first_pairs.append((entity_a, entity_b))
            used_tokens.update((entity_a.token, entity_b.token))
        first_leftover = next(
            (entity for entity in entities if entity.token not in used_tokens),
            None,
        )
        schedule = _round_robin_schedule(
            entities,
            first_pairs,
            first_leftover,
            start_round=2,
        )
        for round_index, next_pairs, next_leftover in schedule:
            _create_round_matches(
                tournament,
                round_index=round_index,
                pairs=next_pairs,
                leftover=next_leftover,
                auto_bye=False,
            )
        return len(schedule)

    played = set(get_previously_played(tournament).keys())
    rested: set[str] = set()
    if count % 2 == 1:
        for round_index in existing_rounds:
            round_tokens: set[str] = set()
            for match in _round_matches(tournament, round_index):
                for side in (1, 2):
                    token = match_entity_token(match, side)
                    if token:
                        round_tokens.add(token)
            for entity in entities:
                if entity.token not in round_tokens:
                    rested.add(entity.token)
                    break

    created = 0
    for round_index in range(last_round + 1, total_rounds + 1):
        leftover: PairingEntity | None = None
        pairs: list[tuple[PairingEntity, PairingEntity]] | None = None
        if count % 2 == 0:
            pairs = _build_matching(entities, played, allow_rematch=False)
        else:
            preferred = [entity for entity in entities if entity.token not in rested]
            fallback = [entity for entity in entities if entity.token in rested]
            for candidate in preferred + fallback:
                pool = [
                    entity for entity in entities if entity.token != candidate.token
                ]
                candidate_pairs = _build_matching(pool, played, allow_rematch=False)
                if candidate_pairs is not None:
                    leftover = candidate
                    pairs = candidate_pairs
                    break
        if pairs is None:
            raise ManualPairingError(
                f"Не удалось подобрать пары для тура {round_index} без повторов."
            )
        for entity_a, entity_b in pairs:
            played.add(pair_key(entity_a.token, entity_b.token))
        if leftover is not None:
            rested.add(leftover.token)
        _create_round_matches(
            tournament,
            round_index=round_index,
            pairs=pairs,
            leftover=leftover,
            auto_bye=False,
        )
        created += 1
    return created


def _validate_round_pairs(
    tournament: Tournament,
    *,
    pairs: list[tuple[PairingEntity, PairingEntity]],
    round_index: int,
    allow_rematch: bool,
) -> None:
    """Проверить корректность пар перед сохранением.

    Args:
        tournament: Турнир.
        pairs: Пары участников.
        round_index: Номер круга.
        allow_rematch: Разрешены ли повторные пары.

    Raises:
        ManualPairingError: Если пары некорректны.
    """
    if not pairs:
        raise ManualPairingError("Добавьте хотя бы одну пару.")
    tokens: list[str] = []
    for entity_a, entity_b in pairs:
        if entity_a.token == entity_b.token:
            raise ManualPairingError("В паре должны быть два разных участника.")
        tokens.extend((entity_a.token, entity_b.token))
    if len(tokens) != len(set(tokens)):
        raise ManualPairingError("Один участник не может играть в двух парах.")

    if not allow_rematch:
        played = get_previously_played(tournament, exclude_round=round_index)
        repeats = [
            (entity_a, entity_b, played[pair_key(entity_a.token, entity_b.token)])
            for entity_a, entity_b in pairs
            if pair_key(entity_a.token, entity_b.token) in played
        ]
        if repeats:
            details = "; ".join(
                f"{entity_a.name} — {entity_b.name} "
                f"(пара уже есть в {', '.join(f'{n} круге' for n in rounds)})"
                for entity_a, entity_b, rounds in repeats
            )
            raise ManualPairingError(
                "Есть повторные пары: "
                f"{details}. Подтвердите создание пары, если хотите продолжить."
            )


def save_manual_round(
    tournament: Tournament,
    *,
    pairs: list[tuple[PairingEntity, PairingEntity]],
    leftover: PairingEntity | None,
    auto_next: bool,
    allow_rematch: bool = False,
) -> tuple[bool, str]:
    """Сохранить круг пар и создать матчи.

    Args:
        tournament: Турнир.
        pairs: Пары участников.
        leftover: Участник без пары.
        auto_next: Автоматически составлять следующие круги.
        allow_rematch: Подтверждённые повторные пары.

    Returns:
        tuple[bool, str]: Успех и сообщение.
    """
    _ensure_supported(tournament)
    from .manual_stages import has_manual_stages

    if has_manual_stages(tournament):
        return False, "Используйте страницу ручных этапов этого турнира."

    if not tournament.manual_pairing_has_started():
        return False, "Сначала запустите турнир в ручном режиме."
    if tournament.status in (TournamentStatus.COMPLETED, TournamentStatus.CANCELLED):
        return False, "Турнир уже завершён или отменён."

    with transaction.atomic():
        locked = Tournament.objects.select_for_update().get(pk=tournament.pk)
        all_matches = list(_main_matches(locked))
        if not all_matches:
            round_index = 1
            eligible = load_entities(locked)
        else:
            last_round = max(match.round_index for match in all_matches)
            last_matches = _round_matches(locked, last_round)
            if not _round_is_complete(last_matches):
                return False, "Сначала внесите результаты всех матчей текущего круга."
            if locked.status == TournamentStatus.COMPLETED:
                return False, "Турнир уже завершён."
            round_index = last_round + 1
            if locked.format == TournamentFormat.SINGLE_ELIMINATION:
                eligible = get_round_winners(locked, last_round)
            else:
                eligible = load_entities(locked)

        eligible_tokens = {entity.token for entity in eligible}
        for entity_a, entity_b in pairs:
            if (
                entity_a.token not in eligible_tokens
                or entity_b.token not in eligible_tokens
            ):
                raise ManualPairingError(
                    "В паре есть участник, который не проходит в этот круг."
                )
        if leftover is not None and leftover.token not in eligible_tokens:
            raise ManualPairingError("Отдыхающий участник не проходит в этот круг.")
        if len(pairs) * 2 + (1 if leftover else 0) != len(eligible):
            raise ManualPairingError(
                "Распределите всех участников: осталось "
                f"{len(eligible) - len(pairs) * 2 - (1 if leftover else 0)}."
            )
        _validate_round_pairs(
            locked,
            pairs=pairs,
            round_index=round_index,
            allow_rematch=allow_rematch,
        )

        _create_round_matches(
            locked,
            round_index=round_index,
            pairs=pairs,
            leftover=leftover,
            auto_bye=locked.format == TournamentFormat.SINGLE_ELIMINATION,
        )

        auto_created = 0
        schedule_warning = ""
        effective_auto_next = auto_next
        if locked.format == TournamentFormat.ROUND_ROBIN and auto_next:
            try:
                with transaction.atomic():
                    auto_created = _create_missing_round_robin_rounds(locked)
            except ManualPairingError as exc:
                effective_auto_next = False
                schedule_warning = (
                    " Автоматическое расписание следующих туров не построено: "
                    f"{exc} Составьте их вручную."
                )
                logger.warning(
                    "Round-robin auto schedule failed for tournament %s: %s",
                    locked.slug,
                    exc,
                )

        locked.manual_pairing_auto_next_rounds = effective_auto_next
        locked.save(update_fields=["manual_pairing_auto_next_rounds", "updated_at"])

        message = (
            f"Круг сохранён: {len(pairs)} пар"
            + (f", {leftover.name} отдыхает" if leftover else "")
            + "."
        )
        if auto_created:
            message += f" Автоматически создано кругов: {auto_created}."
        message += schedule_warning

    if auto_next and locked.format == TournamentFormat.SINGLE_ELIMINATION:
        maybe_autoadvance_manual(locked)
    return True, message


def ensure_round_robin_schedule(
    tournament: Tournament,
) -> tuple[bool, str]:
    """Достроить расписание кругового турнира после включения автопродолжения.

    Учитывает все уже созданные туры, в том числе составленные вручную,
    и подбирает пары для оставшихся туров без повторов. Это позволяет
    включить автоматическое продолжение в любой момент, а не только при
    сохранении первого тура.

    Args:
        tournament: Турнир.

    Returns:
        tuple[bool, str]: Успех и сообщение.
    """
    from .manual_stages import has_manual_stages

    if has_manual_stages(tournament):
        return False, "Турнир использует ручные этапы."

    if getattr(tournament, "format", None) != TournamentFormat.ROUND_ROBIN:
        return False, "Формат турнира не круговой."
    if not tournament.manual_pairing_has_started():
        return False, "Ручной режим не запущен."
    if not tournament.manual_pairing_auto_next_rounds:
        return False, "Автоматическое продолжение выключено."

    try:
        with transaction.atomic():
            locked = Tournament.objects.select_for_update().get(pk=tournament.pk)
            if not _main_matches(locked).exists():
                return False, "Сначала сохраните первый тур."
            created = _create_missing_round_robin_rounds(locked)
    except ManualPairingError as exc:
        return False, f"Расписание не построено: {exc}"

    if not created:
        return False, "Все туры уже созданы."
    return True, f"Автоматически создано туров: {created}."


def update_manual_pair(
    tournament: Tournament,
    *,
    match: Match,
    entity_a: PairingEntity,
    entity_b: PairingEntity,
    allow_rematch: bool = False,
) -> tuple[bool, str]:
    """Изменить пару в ещё не сыгранном матче.

    Args:
        tournament: Турнир.
        match: Матч.
        entity_a: Новый первый участник.
        entity_b: Новый второй участник.
        allow_rematch: Подтверждённые повторные пары.

    Returns:
        tuple[bool, str]: Успех и сообщение.
    """
    _ensure_supported(tournament)
    from .manual_stages import has_manual_stages

    if has_manual_stages(tournament):
        return False, "Используйте страницу ручных этапов этого турнира."

    if not tournament.manual_pairing_has_started():
        return False, "Ручной режим не запущен."
    if match.tournament_id != tournament.pk:
        return False, "Матч не относится к турниру."
    if match.is_consolation:
        return False, "Матчи утешительной сетки нельзя редактировать здесь."
    if _is_complete(match):
        return False, "Матч уже сыгран — пару изменить нельзя."
    if entity_a.token == entity_b.token:
        return False, "В паре должны быть два разных участника."

    with transaction.atomic():
        locked = Tournament.objects.select_for_update().get(pk=tournament.pk)
        match = (
            Match.objects.select_for_update()
            .prefetch_related("tournament")
            .get(pk=match.pk)
        )
        if _is_complete(match):
            return False, "Матч уже сыгран — пару изменить нельзя."

        round_index = match.round_index
        if locked.format == TournamentFormat.SINGLE_ELIMINATION:
            if round_index == 1:
                eligible = load_entities(locked)
            else:
                eligible = get_round_winners(locked, round_index - 1)
        else:
            eligible = load_entities(locked)
        eligible_tokens = {entity.token for entity in eligible}
        if (
            entity_a.token not in eligible_tokens
            or entity_b.token not in eligible_tokens
        ):
            return False, "Этот участник не проходит в данный круг."

        old_tokens = [
            match_entity_token(match, 1),
            match_entity_token(match, 2),
        ]
        new_tokens = [entity_a.token, entity_b.token]
        displaced = [token for token in old_tokens if token and token not in new_tokens]
        other_matches = [
            other
            for other in _round_matches(locked, round_index)
            if other.pk != match.pk
        ]

        swaps: list[tuple[Match, int, str]] = []
        for entity in (entity_a, entity_b):
            token = entity.token
            for other in other_matches:
                for side in (1, 2):
                    if match_entity_token(other, side) != token:
                        continue
                    if _is_complete(other):
                        return False, (
                            "Участник уже сыграл в этом круге — "
                            "переставить его нельзя."
                        )
                    if not displaced:
                        return False, "Не удалось переставить участников местами."
                    swaps.append((other, side, displaced.pop(0)))
                    break
                else:
                    continue
                break

        if swaps:
            entities_by_token = {entity.token: entity for entity in eligible}
            for other, side, replacement_token in swaps:
                replacement = entities_by_token.get(replacement_token)
                if replacement is None:
                    return False, "Не удалось найти замену для другой пары."
                _assign_match_side(other, side, replacement, locked.is_doubles())
                other.save(
                    update_fields=(
                        ["team1", "player1"] if side == 1 else ["team2", "player2"]
                    )
                )

        result_pairs: list[tuple[PairingEntity, PairingEntity]] = [(entity_a, entity_b)]
        for other in other_matches:
            token_a = match_entity_token(other, 1)
            token_b = match_entity_token(other, 2)
            if token_a and token_b:
                first = next((item for item in eligible if item.token == token_a), None)
                second = next(
                    (item for item in eligible if item.token == token_b), None
                )
                if first and second:
                    result_pairs.append((first, second))

        _validate_round_pairs(
            locked,
            pairs=result_pairs,
            round_index=round_index,
            allow_rematch=allow_rematch,
        )

        _assign_match_side(match, 1, entity_a, locked.is_doubles())
        _assign_match_side(match, 2, entity_b, locked.is_doubles())
        match.save()

    return True, "Пара изменена."


def maybe_autoadvance_manual(tournament: Tournament) -> None:
    """Автоматически перейти к следующему кругу после результатов.

    Работает только для олимпийской системы с включённым автопродолжением.
    Для кругового турнира все круги создаются сразу при сохранении первого.

    Args:
        tournament: Турнир.
    """
    from .manual_stages import has_manual_stages

    if has_manual_stages(tournament):
        return

    if (
        not tournament.uses_manual_pairing()
        or getattr(tournament, "format", None) != TournamentFormat.SINGLE_ELIMINATION
        or tournament.status in (TournamentStatus.COMPLETED, TournamentStatus.CANCELLED)
        or is_creating_round(tournament.pk)
    ):
        return

    all_matches = list(_main_matches(tournament))
    if not all_matches:
        return
    last_round = max(match.round_index for match in all_matches)
    last_matches = _round_matches(tournament, last_round)
    if not last_matches or not _round_is_complete(last_matches):
        return

    # Финал завершает турнир даже при выключенном автопродолжении:
    # следующий круг уже не нужен, но итоги и очки должны быть подведены.
    if len(last_matches) == 1:
        from .fan import finalize_tournament

        finalize_tournament(tournament)
        return

    if not tournament.manual_pairing_auto_next_rounds:
        return

    winners = get_round_winners(tournament, last_round)
    if len(winners) < 2:
        return
    if len(winners) % 2 == 1:
        leftover = winners[-1]
        pairs_entities = winners[:-1]
    else:
        leftover = None
        pairs_entities = winners

    pairs: list[tuple[PairingEntity, PairingEntity]] = []
    for index in range(len(pairs_entities) // 2):
        pairs.append((pairs_entities[index], pairs_entities[-1 - index]))

    try:
        created = _create_round_matches(
            tournament,
            round_index=last_round + 1,
            pairs=pairs,
            leftover=leftover,
            auto_bye=True,
        )
    except Exception:
        logger.exception(
            "Manual auto-advance failed for tournament %s", tournament.slug
        )
        return
    if created:
        logger.info(
            "Manual tournament %s auto-created round %s",
            tournament.slug,
            last_round + 1,
        )


def pairing_state(tournament: Tournament) -> PairingState:
    """Собрать состояние ручного формирования пар для страницы.

    Args:
        tournament: Турнир.

    Returns:
        PairingState: Текущее состояние.
    """
    is_started = tournament.manual_pairing_has_started()
    main_matches = list(
        _main_matches(tournament)
        .select_related(
            "player1__user",
            "player2__user",
            "winner__user",
            "team1__player1__user",
            "team1__player2__user",
            "team2__player1__user",
            "team2__player2__user",
            "winner_team__player1__user",
            "winner_team__player2__user",
        )
        .order_by("round_index", "round_order", "pk")
    )
    grouped: dict[int, list[Match]] = {}
    for match in main_matches:
        grouped.setdefault(int(match.round_index or 0), []).append(match)

    rounds: list[PairingRound] = []
    for round_index in sorted(grouped):
        matches = grouped[round_index]
        rounds.append(
            PairingRound(
                index=round_index,
                label=matches[0].round_name or f"Круг {round_index}",
                matches=matches,
                is_complete=_round_is_complete(matches),
            )
        )

    is_complete = tournament.status == TournamentStatus.COMPLETED
    can_build = False
    build_round = 1
    build_label = "Первый круг"
    eligible: list[PairingEntity] = []
    open_round = 0
    if is_started and not is_complete:
        if not rounds:
            can_build = True
            build_round = 1
            eligible = load_entities(tournament)
        else:
            incomplete = [item for item in rounds if not item.is_complete]
            if incomplete:
                open_round = incomplete[0].index
            else:
                can_build = True
                build_round = rounds[-1].index + 1
                if tournament.format == TournamentFormat.SINGLE_ELIMINATION:
                    eligible = get_round_winners(tournament, rounds[-1].index)
                else:
                    eligible = load_entities(tournament)
        if can_build and len(eligible) < 2:
            # Победитель уже определён, но турнир ещё не успел завершиться.
            can_build = False
        if can_build:
            pairs_count = max(1, len(eligible) // 2)
            build_label = _round_label(tournament, build_round, pairs_count)

    previously_played = get_previously_played(tournament)
    return PairingState(
        is_started=is_started,
        is_complete=is_complete,
        can_build=can_build,
        build_round=build_round,
        build_label=build_label,
        eligible=eligible,
        rounds=rounds,
        open_round=open_round,
        previously_played=previously_played,
        total_entities=len(load_entities(tournament)),
        bye_count=sum(
            1
            for match in main_matches
            if match.is_bye_match() and match.round_index == open_round
        ),
    )


def parse_pair_tokens(
    tournament: Tournament, tokens: Iterable[str]
) -> list[PairingEntity]:
    """Преобразовать токены в участников с проверкой.

    Args:
        tournament: Турнир.
        tokens: Токены участников.

    Returns:
        list[PairingEntity]: Участники.

    Raises:
        ManualPairingError: Если токен неизвестен.
    """
    by_token = {entity.token: entity for entity in load_entities(tournament)}
    result: list[PairingEntity] = []
    for token in tokens:
        entity = by_token.get(token)
        if entity is None:
            raise ManualPairingError("Участник не найден в турнире.")
        result.append(entity)
    return result
