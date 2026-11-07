"""Круговой отбор и две олимпийские сетки в обычном ручном турнире."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone

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
    TournamentPlayerResult,
    TournamentStatus,
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
        round_name=f"{stage.name} · круг {round_index}",
        is_consolation=stage.kind == ManualTournamentStage.Kind.CONSOLATION,
        deadline=(
            max(
                _deadline(stage.tournament, round_index),
                timezone.now()
                + timedelta(days=stage.tournament.match_days_per_round or 7),
            )
            if first and second
            else None
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

        target.deadline = max(
            _deadline(tournament, target.round_index),
            timezone.now() + timedelta(days=tournament.match_days_per_round or 7),
        )
        Match.objects.filter(pk=target.pk).update(deadline=target.deadline)
        notify_telegram_match_created(Match, target, created=True)


def result_is_locked(match: Match) -> bool:
    """Запретить правку результата, на котором уже основан следующий этап."""
    stage = match.manual_stage
    if stage is None or match.status not in FINAL_STATUSES:
        return False
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
        for token, local_place in places.items():
            entity = entities.get(token)
            if not entity:
                continue
            players = (
                [entity.team.player1, entity.team.player2]
                if entity.team
                else [entity.player]
            )
            place = offset + local_place
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
                        "is_consolation": bool(offset),
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
            if stage.kind == ManualTournamentStage.Kind.QUALIFIER
            else []
        )
        stages.append({"stage": stage, "matches": matches, "standings": standings})
    return {
        "manual_stage_views": stages,
        "stage_entities": list(entities.values()),
        "qualifiers_done": qualifiers_complete(tournament),
        "knockouts_created": knockouts_created,
        "has_stages": bool(stages),
        "stages_closed": tournament.status
        in (TournamentStatus.COMPLETED, TournamentStatus.CANCELLED),
    }
