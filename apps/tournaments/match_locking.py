"""Единый порядок блокировок исходной заявки и матча для игровых действий."""

from __future__ import annotations

from apps.sparring.models import DoublesMatchRequest
from apps.users.models import Player

from .models import Match, Tournament, TournamentTeam
from .score_validation import ProposalValidationError


def lock_match(
    match_id: int, *, additional_request_id: int | None = None
) -> Match | None:
    """Заблокировать заявку серии до матча, не образуя цикл с игроками и подписками.

    Порядок: исходная заявка, матч, составы/игроки, подписки. Вызывающий сервис
    должен держать atomic(). Если администратор успел изменить связь между
    чтением контекста и блокировкой, действие отказывается от старого контекста.

    Args:
        match_id: Идентификатор исходного матча.
        additional_request_id: Прежняя или новая заявка серии для общей блокировки.

    Returns:
        Заблокированный матч или None, если запись не найдена.

    Raises:
        ProposalValidationError: Контекст матча изменился или ручной этап потерял турнир.
    """
    context = (
        Match.objects.filter(pk=match_id)
        .values("sparring_team_request_id", "tournament_id", "manual_stage_id")
        .first()
    )
    if context is None:
        return None
    if context["manual_stage_id"]:
        if context["tournament_id"] is None:
            raise ProposalValidationError("У матча ручного этапа нет турнира.")
        Tournament.objects.select_for_update().get(pk=context["tournament_id"])
    request_id = context["sparring_team_request_id"]
    request_ids = {request_id, additional_request_id} - {None}
    list(
        DoublesMatchRequest.objects.select_for_update(no_key=True)
        .filter(pk__in=request_ids)
        .order_by("pk")
    )
    match: Match | None = (
        Match.objects.select_for_update(no_key=True).filter(pk=match_id).first()
    )
    if match is not None and match.sparring_team_request_id != request_id:
        raise ProposalValidationError("Контекст матча изменился. Повторите действие.")
    return match


def lock_match_participants(*matches: Match) -> None:
    """Заблокировать следующие матчи, свежие составы и игроков до чтения рейтинга.

    При правке результата учитываются старый и новый составы. Исходные матчи уже
    должны быть заблокированы. Слоты следующего раунда блокируются до игроков:
    результат следующего матча не должен ждать игрока, удерживаемого его предком.
    NO KEY UPDATE не мешает созданию FK-уведомлений.
    Кеш связанных объектов сбрасывается: его данные могли устареть до ожидания.
    """
    current_ids = {match.pk for match in matches}
    successor_fields = ("next_match", "loser_next_match")
    successor_ids = (
        {
            getattr(match, f"{field}_id")
            for match in matches
            for field in successor_fields
        }
        - {None}
        - current_ids
    )
    for successor_id in sorted(successor_ids):
        lock_match(successor_id)
    for match in matches:
        for field in successor_fields:
            match._state.fields_cache.pop(field, None)
    player_fields = ("player1", "player2", "partner1", "partner2", "winner")
    team_fields = ("team1", "team2", "winner_team")
    team_ids = {
        getattr(match, f"{field}_id") for match in matches for field in team_fields
    } - {None}
    teams = list(
        TournamentTeam.objects.select_for_update(no_key=True)
        .filter(pk__in=team_ids)
        .order_by("pk")
    )
    player_ids = {
        getattr(match, f"{field}_id") for match in matches for field in player_fields
    } | {
        player_id for team in teams for player_id in (team.player1_id, team.player2_id)
    }
    list(
        Player.objects.select_for_update(no_key=True)
        .filter(pk__in=player_ids - {None})
        .order_by("pk")
    )
    for match in matches:
        for field in (*team_fields, *player_fields):
            match._state.fields_cache.pop(field, None)
