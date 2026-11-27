"""Круговой отбор и две олимпийские сетки в обычном ручном турнире."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, cast

from django.core.signing import Signer
from django.db import transaction
from django.utils import timezone

from apps.users.models import Player

from .manual_pairing import (
    FINAL_STATUSES,
    ManualPairingError,
    PairingEntity,
    _assign_match_side,
    _deadline,
    load_entities,
    match_entity_token,
    supports_manual_pairing,
)
from .models import (
    ManualTournamentStage,
    Match,
    Tournament,
    TournamentDuration,
    TournamentPlayerResult,
    TournamentStatus,
    TournamentTeam,
)
from .round_robin import (
    _circle_schedule,
    _round_robin_points_for_place,
    compute_standings_for_entities,
)


def has_manual_stages(tournament: Tournament) -> bool:
    """Проверить наличие свободного сценария турнира."""
    return bool(tournament.manual_stages.exists())


def _ensure_active(tournament: Tournament) -> None:
    """Проверить формат и запуск ручного турнира."""
    if (
        not supports_manual_pairing(tournament)
        or not tournament.manual_pairing_has_started()
    ):
        raise ManualPairingError("Сначала запустите этот турнир в ручном режиме.")
    if tournament.status in (TournamentStatus.COMPLETED, TournamentStatus.CANCELLED):
        raise ManualPairingError("Турнир уже завершён или отменён.")


def _entities(tournament: Tournament) -> dict[str, PairingEntity]:
    """Получить допущенных игроков или полные команды."""
    return {entity.token: entity for entity in load_entities(tournament)}


def _validate_partition(
    tournament: Tournament, batches: list[list[str]]
) -> dict[str, PairingEntity]:
    """Проверить полное однократное распределение участников."""
    entities = _entities(tournament)
    flat = [token for batch in batches for token in batch]
    if len(flat) != len(set(flat)):
        raise ManualPairingError("Один участник не может занимать два места.")
    if set(flat) != set(entities):
        raise ManualPairingError("Распределите всех участников ровно по одному разу.")
    if any(len(batch) < 2 for batch in batches):
        raise ManualPairingError(
            "В каждом отборе и в каждой сетке нужно минимум два участника."
        )
    return entities


def _ready_deadline(tournament: Tournament, round_index: int) -> datetime:
    """Дать готовому матчу полный круг, не продлевая короткое событие."""
    deadline = _deadline(tournament, round_index)
    if tournament.duration == TournamentDuration.MULTI_DAY:
        return cast(
            datetime,
            max(
                deadline,
                timezone.now() + timedelta(days=tournament.match_days_per_round or 7),
            ),
        )
    return deadline


def _match(
    stage: ManualTournamentStage,
    round_index: int,
    order: int,
    first: PairingEntity | None,
    second: PairingEntity | None,
) -> Match:
    """Создать матч этапа с отдельными номером и названием круга."""
    match = Match(
        tournament=stage.tournament,
        manual_stage=stage,
        round_index=round_index,
        round_order=order,
        round_name=f"{stage.name[:38]} · круг {round_index}",
        is_consolation=stage.kind == ManualTournamentStage.Kind.CONSOLATION,
        deadline=(
            _ready_deadline(stage.tournament, round_index) if first and second else None
        ),
    )
    if first:
        _assign_match_side(match, 1, first, stage.tournament.is_doubles())
    if second:
        _assign_match_side(match, 2, second, stage.tournament.is_doubles())
    match.save()
    return match


@transaction.atomic
def create_qualifiers(tournament: Tournament, groups: dict[str, list[str]]) -> None:
    """Сохранить ручной состав отборов и полное круговое расписание каждого."""
    tournament = Tournament.objects.select_for_update().get(pk=tournament.pk)
    _ensure_active(tournament)
    if tournament.matches.exists() or has_manual_stages(tournament):
        raise ManualPairingError(
            "Матчи уже созданы. Изменить сценарий после начала игр нельзя."
        )
    if len(_entities(tournament)) < 4:
        raise ManualPairingError(
            "Для отбора и двух сеток нужно минимум четыре участника или команды."
        )
    if len({name.strip() for name in groups}) != len(groups):
        raise ManualPairingError("Названия отборов должны различаться.")
    if not groups or len(groups) > 8:
        raise ManualPairingError("Создайте от одного до восьми круговых отборов.")
    if any(not name.strip() or len(name.strip()) > 24 for name in groups):
        raise ManualPairingError(
            "Название отбора должно содержать от 1 до 24 символов."
        )
    entities = _validate_partition(tournament, list(groups.values()))
    for index, (name, tokens) in enumerate(groups.items(), 1):
        stage = ManualTournamentStage.objects.create(
            tournament=tournament,
            name=f"Отбор {name.strip()}",
            kind=ManualTournamentStage.Kind.QUALIFIER,
            order=index,
            member_tokens=tokens,
        )
        for ri, pairs in enumerate(_circle_schedule([entities[t] for t in tokens]), 1):
            for order, (first, second) in enumerate(pairs, 1):
                if first is not None and second is not None:
                    _match(stage, ri, order, first, second)


def qualifiers_complete(tournament: Tournament) -> bool:
    """Проверить завершение всех матчей кругового отбора."""
    matches = tournament.matches.filter(
        manual_stage__kind=ManualTournamentStage.Kind.QUALIFIER
    )
    return matches.exists() and not matches.exclude(status__in=FINAL_STATUSES).exists()


def _build_knockout(
    stage: ManualTournamentStage, entities: dict[str, PairingEntity]
) -> None:
    """Построить дерево с ручным посевом и проходами без игры при неполной сетке."""
    seeds = [entities[token] for token in stage.member_tokens]
    size = 1 << (len(seeds) - 1).bit_length()
    bye_count = size - len(seeds)
    # Сначала свободные проходы, затем пары из соседних позиций посева.
    slots: list[tuple[PairingEntity, PairingEntity | None]] = [
        (entity, None) for entity in seeds[:bye_count]
    ]
    remaining = seeds[bye_count:]
    slots.extend((remaining[i], remaining[i + 1]) for i in range(0, len(remaining), 2))
    previous: list[Match] = []
    round_index = 1
    width = size // 2
    while width:
        current = [
            _match(
                stage,
                round_index,
                i + 1,
                slots[i][0] if round_index == 1 else None,
                slots[i][1] if round_index == 1 else None,
            )
            for i in range(width)
        ]
        for index, source in enumerate(previous):
            Match.objects.filter(pk=source.pk).update(next_match=current[index // 2])
        previous = current
        round_index += 1
        width //= 2
    # Проходы без игры не влияют на рейтинг и статистику реальных матчей.
    first_round = list(stage.matches.filter(round_index=1).order_by("round_order"))
    for match in first_round[:bye_count]:
        Match.objects.filter(pk=match.pk).update(
            status=Match.MatchStatus.WALKOVER,
            winner_id=match.player1_id,
            winner_team_id=match.team1_id,
            completed_datetime=timezone.now(),
        )
        match.refresh_from_db()
        _advance(match)


@transaction.atomic
def create_two_knockouts(
    tournament: Tournament, main: list[str], consolation: list[str]
) -> None:
    """Зафиксировать ручной посев в две независимые олимпийские сетки."""
    tournament = Tournament.objects.select_for_update().get(pk=tournament.pk)
    _ensure_active(tournament)
    if not qualifiers_complete(tournament):
        raise ManualPairingError("Сначала завершите все матчи кругового отбора.")
    if tournament.manual_stages.exclude(
        kind=ManualTournamentStage.Kind.QUALIFIER
    ).exists():
        raise ManualPairingError("Обе олимпийские сетки уже созданы.")
    entities = _validate_partition(tournament, [main, consolation])
    for index, (kind, name, tokens) in enumerate(
        [
            (ManualTournamentStage.Kind.MAIN, "Основная сетка", main),
            (ManualTournamentStage.Kind.CONSOLATION, "Утешительная сетка", consolation),
        ],
        20,
    ):
        stage = ManualTournamentStage.objects.create(
            tournament=tournament,
            kind=kind,
            name=name,
            order=index,
            member_tokens=tokens,
        )
        _build_knockout(stage, entities)


def _advance(match: Match) -> None:
    """Передать победителя в закреплённую сторону следующего матча.

    Args:
        match: Завершённый матч ручного этапа.

    Raises:
        ManualPairingError: Если следующий матч потерял связь с турниром.
    """
    if not match.next_match_id or not (match.winner_id or match.winner_team_id):
        return
    target = Match.objects.select_for_update().get(pk=match.next_match_id)
    tournament = target.tournament
    if tournament is None:
        raise ManualPairingError("У следующего матча нет турнира.")
    side = 1 if match.round_order % 2 else 2
    fields: dict[str, Any] = {f"player{side}_id": match.winner_id}
    if match.winner_team_id:
        fields[f"team{side}_id"] = match.winner_team_id
    # Служебное заполнение участника не запускает повторный учёт результата.
    was_ready = bool(target.player1_id and target.player2_id)
    Match.objects.filter(pk=target.pk).update(**fields)
    target.refresh_from_db()
    if not was_ready and target.player1_id and target.player2_id:
        from .signals import notify_telegram_match_created

        target.deadline = _ready_deadline(tournament, target.round_index)
        Match.objects.filter(pk=target.pk).update(deadline=target.deadline)
        notify_telegram_match_created(Match, target, created=True)


def result_is_locked(match: Match) -> bool:
    """Запретить правку результата, на котором уже основан следующий этап."""
    stage = match.manual_stage
    if stage is None or match.status not in FINAL_STATUSES:
        return False
    if stage.kind in CUSTOM_KINDS:
        if stage.tournament.manual_stages.filter(order__gt=stage.order).exists():
            return True
        if stage.kind == Kind.PLACES:
            return bool(
                stage.matches.filter(round_index__gt=match.round_index).exists()
            )
    if stage.kind == ManualTournamentStage.Kind.QUALIFIER:
        return bool(
            stage.tournament.manual_stages.exclude(
                kind=ManualTournamentStage.Kind.QUALIFIER
            ).exists()
        )
    return bool(match.next_match_id and (match.winner_id or match.winner_team_id))


@transaction.atomic
def handle_result(tournament: Tournament, match: Match) -> None:
    """Продвинуть победителя и завершить турнир только после обеих сеток."""
    tournament = Tournament.objects.select_for_update().get(pk=tournament.pk)
    if tournament.status in (TournamentStatus.COMPLETED, TournamentStatus.CANCELLED):
        return
    match_stage = match.manual_stage
    if match_stage is None:
        return
    if match_stage.kind in CUSTOM_KINDS:
        if match_stage.kind == Kind.KNOCKOUT:
            _advance(match)
        elif match_stage.kind == Kind.PLACES:
            _classification(match_stage, generate=True)
        return
    if match_stage.kind != ManualTournamentStage.Kind.QUALIFIER:
        _advance(match)
    stages = list(
        tournament.manual_stages.exclude(kind=ManualTournamentStage.Kind.QUALIFIER)
    )
    if len(stages) != 2:
        return
    for stage in stages:
        final = stage.matches.filter(next_match__isnull=True).first()
        if (
            not final
            or final.status not in FINAL_STATUSES
            or not (final.winner_id or final.winner_team_id)
        ):
            return
        if stage.matches.exclude(status__in=FINAL_STATUSES).exists():
            return
    _finalize(tournament, stages)


def _finalize(tournament: Tournament, stages: list[ManualTournamentStage]) -> None:
    """Начислить очки один раз после обеих сеток; полуфиналисты делят места."""
    results: dict[str, tuple[int, bool]] = {}
    main_size = next(
        len(s.member_tokens)
        for s in stages
        if s.kind == ManualTournamentStage.Kind.MAIN
    )
    for stage in stages:
        matches = list(stage.matches.order_by("-round_index", "round_order"))
        final = next(m for m in matches if m.next_match_id is None)
        final_round = final.round_index
        offset = (
            main_size if stage.kind == ManualTournamentStage.Kind.CONSOLATION else 0
        )
        places: dict[str, int] = {}
        winner = (
            f"t:{final.winner_team_id}"
            if final.winner_team_id
            else f"p:{final.winner_id}"
        )
        places[winner] = 1
        for match in matches:
            win = (
                f"t:{match.winner_team_id}"
                if match.winner_team_id
                else f"p:{match.winner_id}"
            )
            for side in (1, 2):
                token = match_entity_token(match, side)
                if token and token != win:
                    places[token] = min(
                        len(stage.member_tokens),
                        (1 << (final_round - match.round_index)) + 1,
                    )
        results.update(
            {token: (offset + place, bool(offset)) for token, place in places.items()}
        )
    _award_places(tournament, results, final)


def _award_places(
    tournament: Tournament, places: dict[str, tuple[int, bool]], final: Match
) -> None:
    """Записать итоговые места и начислить сезонные очки один раз."""
    from .completion_notify import complete_tournament_and_notify
    from .fan import _update_season_points
    from .withdraw import get_withdrawn_player_ids

    excluded = set(get_withdrawn_player_ids(tournament))
    for match in tournament.matches.filter(status=Match.MatchStatus.WALKOVER):
        if not match.is_no_show_walkover():
            continue
        for side, no_show in enumerate(match.get_no_show_sides(), 1):
            if no_show:
                team = getattr(match, f"team{side}")
                if team:
                    excluded.update([team.player1_id, team.player2_id])
                else:
                    excluded.add(getattr(match, f"player{side}_id"))
    entities = _entities(tournament)
    for token, (place, consolation) in places.items():
        entity = entities.get(token)
        if not entity:
            continue
        players = (
            [entity.team.player1, entity.team.player2]
            if entity.team
            else [entity.player]
        )
        for player in players:
            if not player:
                continue
            points = (
                0
                if player.pk in excluded
                else _round_robin_points_for_place(tournament, place)
            )
            _, created = TournamentPlayerResult.objects.get_or_create(
                tournament=tournament,
                player=player,
                defaults={
                    "place": place,
                    "fan_points": points,
                    "is_consolation": consolation,
                },
            )
            if created:
                _update_season_points(player, points, match=final)
    complete_tournament_and_notify(tournament)


def stage_context(tournament: Tournament) -> dict[str, Any]:
    """Собрать таблицы отбора и матчи обеих сеток для организатора и зрителя."""
    entities = _entities(tournament)
    stages = []
    knockouts_created = False
    for stage in tournament.manual_stages.all():
        knockouts_created = (
            knockouts_created or stage.kind != ManualTournamentStage.Kind.QUALIFIER
        )
        matches = list(
            stage.matches.select_related(
                "player1__user",
                "player2__user",
                "team1__player1__user",
                "team1__player2__user",
                "team2__player1__user",
                "team2__player2__user",
            ).order_by("round_index", "round_order")
        )
        members = [
            member
            for token in stage.member_tokens
            if token in entities
            if (member := entities[token].team or entities[token].player) is not None
        ]
        standings = (
            compute_standings_for_entities(tournament, members, matches)
            if stage.kind in (Kind.QUALIFIER, Kind.ROUND)
            else []
        )
        places = (
            _stage_places(stage) if stage.kind in (Kind.KNOCKOUT, Kind.PLACES) else {}
        )
        ranked = sorted(
            (
                (place, entities[token].name)
                for token, place in places.items()
                if token in entities
            ),
            key=lambda row: row[0],
        )
        stages.append(
            {
                "stage": stage,
                "matches": matches,
                "standings": standings,
                "ranked": ranked,
            }
        )
    tours: dict[str, list[dict[str, Any]]] = {}
    for item in stages:
        tours.setdefault(item["stage"].tour_name, []).append(item)
    return {
        "manual_tours": list(tours.items()),
        "manual_stage_views": stages,
        "custom_stages": bool(stages and stages[0]["stage"].kind in CUSTOM_KINDS),
        "all_stages_done": bool(stages)
        and not tournament.matches.exclude(status__in=FINAL_STATUSES).exists(),
        "stage_entities": list(entities.values()),
        "qualifiers_done": qualifiers_complete(tournament),
        "knockouts_created": knockouts_created,
        "has_stages": bool(stages),
        "stages_closed": tournament.status
        in (TournamentStatus.COMPLETED, TournamentStatus.CANCELLED),
    }


Kind = ManualTournamentStage.Kind
CUSTOM_KINDS = (Kind.ROUND, Kind.KNOCKOUT, Kind.PLACES)


def _classification(stage: ManualTournamentStage, *, generate: bool) -> dict[str, int]:
    """Переиграть дерево мест: победители и проигравшие продолжают в своих диапазонах."""
    matches = {(m.round_index, m.round_order): m for m in stage.matches.all()}
    bands = [list(stage.member_tokens)]
    round_index = 1
    entities = _entities(stage.tournament) if generate else {}
    withdrawn = set(stage.member_tokens) - entities.keys() if generate else set()
    if withdrawn:
        ids = [int(token.split(":")[1]) for token in withdrawn]
        if stage.tournament.is_doubles():
            for team in TournamentTeam.objects.filter(pk__in=ids).select_related(
                "player1"
            ):
                token = f"t:{team.pk}"
                entities[token] = PairingEntity(token, team.pk, "", 0, 0, team=team)
        else:
            for player in Player.objects.filter(pk__in=ids):
                token = f"p:{player.pk}"
                entities[token] = PairingEntity(
                    token, player.pk, "", 0, 0, player=player
                )
    while any(len(band) > 1 for band in bands):
        order = 0
        next_bands = []
        ready = True
        for band in bands:
            if len(band) <= 1:
                next_bands.append(band)
                continue
            winners = band[: len(band) % 2]
            losers = []
            for i in range(len(winners), len(band), 2):
                first, second = band[i : i + 2]
                order += 1
                match = matches.get((round_index, order))
                if match is None and generate:
                    match = _match(
                        stage, round_index, order, entities[first], entities[second]
                    )
                    if first in withdrawn or second in withdrawn:
                        side = (
                            2 if first in withdrawn and second not in withdrawn else 1
                        )
                        Match.objects.filter(pk=match.pk).update(
                            status=Match.MatchStatus.WALKOVER,
                            winner_id=getattr(match, f"player{side}_id"),
                            winner_team_id=getattr(match, f"team{side}_id"),
                            walkover_no_show_side1=first in withdrawn,
                            walkover_no_show_side2=second in withdrawn,
                            completed_datetime=timezone.now(),
                        )
                        match.refresh_from_db()
                if match is None or match.status not in FINAL_STATUSES:
                    ready = False
                    continue
                winner = (
                    f"t:{match.winner_team_id}"
                    if match.winner_team_id
                    else f"p:{match.winner_id}"
                )
                if winner not in (first, second):
                    ready = False
                    continue
                winners.append(winner)
                losers.append(second if first == winner else first)
            next_bands.extend([winners, losers])
        if not ready:
            return {}
        bands = next_bands
        round_index += 1
    return {band[0]: index for index, band in enumerate(bands, 1)}


@transaction.atomic
def add_stage(
    tournament: Tournament,
    name: str,
    kind: str,
    tokens: list[str],
    *,
    tour_name: str = "",
) -> None:
    """Создать самостоятельный этап для выбранных игроков или команд."""
    tournament = Tournament.objects.select_for_update().get(pk=tournament.pk)
    _ensure_active(tournament)
    if (
        tournament.matches.filter(manual_stage=None).exists()
        or tournament.manual_stages.exclude(kind__in=CUSTOM_KINDS).exists()
    ):
        raise ManualPairingError("Этот турнир уже начат по другому сценарию.")
    tour_name = tour_name.strip()
    if len(tour_name) > 80:
        raise ManualPairingError("Сократите название тура до 80 символов.")
    grids = tournament.manual_stages.filter(tour_name__iexact=tour_name)
    existing = grids.first()
    if existing:
        tour_name = existing.tour_name
    name = name.strip()
    if not name or len(name) > 80 or grids.filter(name__iexact=name).exists():
        raise ManualPairingError(
            "Введите название сетки до 80 символов, уникальное в этом туре."
        )
    entities = _entities(tournament)
    if (
        kind not in CUSTOM_KINDS
        or len(tokens) < 2
        or len(set(tokens)) != len(tokens)
        or not set(tokens) <= entities.keys()
    ):
        raise ManualPairingError(
            "Выберите формат и как минимум двух разных участников турнира."
        )
    for previous in tournament.manual_stages.all():
        if (
            set(tokens).intersection(previous.member_tokens)
            and previous.matches.exclude(status__in=FINAL_STATUSES).exists()
        ):
            raise ManualPairingError(
                f"Сначала завершите матчи этапа «{previous.name}» или выберите другой состав."
            )
    stage = tournament.manual_stages.create(
        kind=kind,
        name=name,
        tour_name=tour_name,
        member_tokens=tokens,
        order=tournament.manual_stages.count() + 1,
    )
    if kind == Kind.KNOCKOUT:
        _build_knockout(stage, entities)
    elif kind == Kind.PLACES:
        _classification(stage, generate=True)
    else:
        for ri, pairs in enumerate(_circle_schedule([entities[t] for t in tokens]), 1):
            for order, (first, second) in enumerate(pairs, 1):
                if first and second:
                    _match(stage, ri, order, first, second)


def _stage_places(stage: ManualTournamentStage) -> dict[str, int]:
    """Определить места внутри законченного этапа по сыгранным матчам."""
    matches = list(stage.matches.order_by("-round_index", "round_order"))
    if not matches or any(m.status not in FINAL_STATUSES for m in matches):
        return {}
    if stage.kind == Kind.PLACES:
        return _classification(stage, generate=False)
    if stage.kind == Kind.ROUND:
        entities = _entities(stage.tournament)
        members: list[Player | TournamentTeam] = []
        for token in stage.member_tokens:
            entity = entities.get(token)
            member = entity.team or entity.player if entity else None
            if member is not None:
                members.append(member)
        rows = compute_standings_for_entities(stage.tournament, members, matches)
        return {
            (f"t:{r['team'].pk}" if r["team"] else f"p:{r['player'].pk}"): r["place"]
            for r in rows
            if r["place"]
        }
    final = matches[0]
    if not (final.winner_id or final.winner_team_id):
        return {}
    places = {}
    for match in matches:
        winner = (
            f"t:{match.winner_team_id}"
            if match.winner_team_id
            else f"p:{match.winner_id}"
        )
        for side in (1, 2):
            token = match_entity_token(match, side)
            if token and token != winner:
                places[token] = 1 + (1 << (final.round_index - match.round_index))
        if match.pk == final.pk:
            places[winner] = 1
    return places


@transaction.atomic
def finish_stages(tournament: Tournament, stage_id: str) -> None:
    """Завершить турнир по выбранному итоговому этапу."""
    tournament = Tournament.objects.select_for_update().get(pk=tournament.pk)
    _ensure_active(tournament)
    if tournament.matches.exclude(status__in=FINAL_STATUSES).exists():
        raise ManualPairingError("Сначала завершите матчи всех этапов.")
    stage = tournament.manual_stages.filter(
        kind__in=CUSTOM_KINDS,
        pk=(
            int(stage_id)
            if stage_id.isascii() and stage_id.isdigit() and len(stage_id) < 19
            else 0
        ),
    ).first()
    if not stage or not (places := _stage_places(stage)):
        raise ManualPairingError(
            "Выберите завершённый этап для определения итоговых мест."
        )
    _award_places(
        tournament,
        {token: (place, False) for token, place in places.items()},
        stage.matches.latest("pk"),
    )


def combined_places(
    tournament: Tournament, sources: dict[str, str]
) -> tuple[dict[str, tuple[int, bool]], Match]:
    """Собрать места из сеток, отдавая приоритет верхним диапазонам."""
    if tournament.matches.exclude(status__in=FINAL_STATUSES).exists():
        raise ManualPairingError("Сначала завершите матчи всех сеток.")
    stages = {
        str(stage.pk): stage
        for stage in tournament.manual_stages.filter(kind__in=CUSTOM_KINDS)
    }
    if not sources or not sources.keys() <= stages.keys():
        raise ManualPairingError(
            "Укажите первое место хотя бы для одной сетки этого турнира."
        )
    ranges: list[tuple[int, ManualTournamentStage]] = []
    for key, value in sources.items():
        if not value.isascii() or not value.isdecimal() or len(value) > 6:
            raise ManualPairingError(
                "Начало диапазона должно быть положительным целым числом."
            )
        ranges.append((int(value), stages[key]))
    ranges.sort(key=lambda item: item[0])
    places: dict[str, tuple[int, bool]] = {}
    eligible = _entities(tournament)
    for first, stage in ranges:
        expected = len(places) + 1
        if first != expected:
            raise ManualPairingError(
                f"Для сетки «{stage.name}» укажите начало с места {expected}. Диапазоны должны идти подряд."
            )
        ranking = sorted(
            (rank, token)
            for token, rank in _stage_places(stage).items()
            if token not in places and token in eligible
        )
        if not ranking:
            raise ManualPairingError(
                f"В сетке «{stage.name}» нет результатов для добавления в зачёт."
            )
        previous = None
        place = first
        for index, (rank, token) in enumerate(ranking):
            if previous != rank:
                place = first + index
            places[token] = (place, False)
            previous = rank
    return places, ranges[0][1].matches.latest("pk")


def placement_signature(
    tournament: Tournament, places: dict[str, tuple[int, bool]]
) -> str:
    """Связать подтверждение с просмотренной расстановкой участников."""
    value = "|".join(f"{token}={place}" for token, (place, _) in sorted(places.items()))
    return str(
        Signer(salt="tournament-placements").signature(f"{tournament.pk}|{value}")
    )


@transaction.atomic
def finish_combined(
    tournament: Tournament, sources: dict[str, str], signature: str
) -> None:
    """Зафиксировать общую таблицу и однократно начислить сезонные очки."""
    tournament = Tournament.objects.select_for_update().get(pk=tournament.pk)
    _ensure_active(tournament)
    places, match = combined_places(tournament, sources)
    if signature != placement_signature(tournament, places):
        raise ManualPairingError("Проверьте актуальный общий зачёт перед публикацией.")
    _award_places(tournament, places, match)
