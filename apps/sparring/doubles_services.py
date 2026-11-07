"""
Сервисы формирования парного спарринга 2×2.
Атомарные операции с select_for_update, проверки бизнес-логики.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, cast

from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from loguru import logger

from apps.notifications.services import create_notification
from apps.tournaments.models import Match
from apps.users.onboarding import ensure_tennis_participants

from .models import (
    DoublesJoinRequest,
    DoublesJoinRequestMember,
    DoublesJoinRequestStatus,
    DoublesMatchKind,
    DoublesMatchRequest,
    DoublesMatchRequestStatus,
    DoublesTeam,
    DoublesTeamMember,
    TeamSide,
)

if TYPE_CHECKING:
    from apps.users.models import Player


def _normalize_team_side(target_side: str) -> str:
    """Нормализует значение стороны команды, включая старые краткие формы."""
    normalized = (target_side or "").strip().lower()
    aliases = {
        TeamSide.AUTHOR: TeamSide.AUTHOR,
        TeamSide.OPPONENT: TeamSide.OPPONENT,
        "a": TeamSide.AUTHOR,
        "o": TeamSide.OPPONENT,
    }
    result = aliases.get(normalized)
    if result is None:
        raise ValueError("Некорректная целевая команда")
    return cast(str, result)


def _lock_request(match_request_id: int) -> DoublesMatchRequest:
    """Заблокировать свежую заявку до чтения откликов, команд и игроков."""
    request: DoublesMatchRequest | None = (
        DoublesMatchRequest.objects.select_for_update()
        .filter(pk=match_request_id)
        .first()
    )
    if request is None:
        raise ValueError("Заявка не найдена.")
    return request


def _assert_unconfirmed(request: DoublesMatchRequest) -> None:
    """Не менять контекст созданных матчей даже после ручной смены статуса заявки."""
    if (
        request.confirmed_at is not None
        or request.match_id is not None
        or request.series_matches.exists()
    ):
        raise ValueError(
            "Матчи уже созданы. Нельзя повторно формировать серию, менять состав или удалять её заявку."
        )


def _pending_join(
    request: DoublesMatchRequest, join_request_id: int
) -> DoublesJoinRequest:
    """Обработать старую кнопку как бизнес-отказ, а не техническую ошибку БД."""
    join: DoublesJoinRequest | None = (
        DoublesJoinRequest.objects.select_for_update()
        .filter(
            pk=join_request_id,
            match_request=request,
            status=DoublesJoinRequestStatus.PENDING,
        )
        .first()
    )
    if join is None:
        raise ValueError("Отклик уже обработан или не найден.")
    return join


def _validate_join_ids(player_ids: list[int], submitter_id: int) -> None:
    """В отклике должны быть один или два разных игрока, включая заявителя."""
    if not player_ids or len(player_ids) > 2:
        raise ValueError("Укажите 1 или 2 игроков.")
    if len(set(player_ids)) != len(player_ids):
        raise ValueError("Участники отклика должны быть разными игроками.")
    if submitter_id not in player_ids:
        raise ValueError("Заявитель должен входить в состав отклика.")


def _load_players(player_ids: list[int], sport: str) -> tuple[Player, ...]:
    """Прочитать активных игроков под блокировками в едином порядке по идентификатору."""
    from apps.users.models import Player

    players = tuple(
        Player.objects.select_for_update(of=("self",))
        .filter(pk__in=player_ids, is_bye=False, user__is_active=True)
        .order_by("pk")
    )
    if len(players) != len(set(player_ids)):
        raise ValueError("Один из участников больше недоступен.")
    ensure_tennis_participants(players, sport)
    return players


def _roster_ids(
    author_team: DoublesTeam, opponent_team: DoublesTeam
) -> tuple[list[int], list[int]]:
    """Проверить четыре разных участника и сохранить порядок капитанов обеих сторон."""
    author_ids = list(
        author_team.members.order_by("-is_captain", "pk").values_list(
            "player_id", flat=True
        )
    )
    opponent_ids = list(
        opponent_team.members.order_by("-is_captain", "pk").values_list(
            "player_id", flat=True
        )
    )
    if len(author_ids) != 2 or len(opponent_ids) != 2:
        raise ValueError("Обе команды должны быть по 2 человека.")
    if len(set(author_ids + opponent_ids)) != 4:
        raise ValueError("Один игрок не может входить в обе команды.")
    if author_team.match_request.created_by_id not in author_ids:
        raise ValueError("Автор должен входить в свою команду.")
    return author_ids, opponent_ids


def _notify_join_accepted(
    request: DoublesMatchRequest, join: DoublesJoinRequest
) -> None:
    """Сохранить уведомления всех участников отклика вместе с принятием состава."""
    from apps.telegram_bot.notifications import notify_doubles_join_accepted

    context = (
        "командный спарринг"
        if request.kind == DoublesMatchKind.TEAM
        else "парный матч 2×2"
    )
    message = f"Ваш отклик на {context} №{request.pk} принят. Ожидайте формирования матчей автором заявки."
    for member in join.members.select_related("player__user").order_by("order"):
        create_notification(
            user=member.player.user,
            message=message,
            url=reverse("doubles_detail", args=[request.pk]),
            event_key="sparring.doubles_joined",
        )
    notify_doubles_join_accepted(join)


def create_doubles_request(
    *,
    created_by: Player,
    city: str = "",
    preferred_gender: str = "",
    is_friendly: bool = False,
    description: str = "",
    partner: Player | None = None,
    preferred_days: str = "",
    preferred_time: str = "",
    desired_level: str = "",
    desired_age_min: int | None = None,
    desired_age_max: int | None = None,
    preferred_location: str = "",
    kind: str = "classic",
    sport: str = "tennis",
) -> DoublesMatchRequest:
    """Создать заявку на парный матч 2×2. Автор — капитан своей команды, опционально с партнёром."""
    ensure_tennis_participants(
        (created_by, partner) if partner else (created_by,), sport or "tennis"
    )
    with transaction.atomic():
        req: DoublesMatchRequest = DoublesMatchRequest.objects.create(
            status=DoublesMatchRequestStatus.OPEN,
            created_by=created_by,
            city=city,
            preferred_gender=preferred_gender,
            is_friendly=is_friendly,
            description=description,
            preferred_days=preferred_days,
            preferred_time=preferred_time,
            desired_level=desired_level,
            desired_age_min=desired_age_min,
            desired_age_max=desired_age_max,
            preferred_location=preferred_location,
            kind=kind,
            sport=sport or "tennis",
        )
        author_team = DoublesTeam.objects.create(
            match_request=req,
            side=TeamSide.AUTHOR,
        )
        DoublesTeamMember.objects.create(
            team=author_team,
            player=created_by,
            is_captain=True,
        )
        if partner and partner.id != created_by.id:
            DoublesTeamMember.objects.create(
                team=author_team,
                player=partner,
                is_captain=False,
            )
        req.status = DoublesMatchRequestStatus.FORMING
        req.save(update_fields=["status"])
        logger.info("Создана парная заявка {}: author_id={}", req.pk, created_by.id)
        return req


def create_join_request(
    *,
    match_request_id: int,
    created_by: Player,
    target_side: str,
    players: list[Player],
) -> DoublesJoinRequest:
    """
    Подать заявку на присоединение (1 или 2 игрока).
    Проверки: слоты в команде, игрок не автор, не в другой команде этой заявки, нет дубля в отклике.
    """
    if not players or len(players) > 2:
        raise ValueError("Укажите 1 или 2 игроков")
    target_side = _normalize_team_side(target_side)

    with transaction.atomic():
        req = _lock_request(match_request_id)
        _assert_unconfirmed(req)
        player_ids = [player.pk for player in players]
        _validate_join_ids(player_ids, created_by.pk)
        current_status: str = req.status
        if current_status not in (
            DoublesMatchRequestStatus.OPEN,
            DoublesMatchRequestStatus.FORMING,
        ):
            raise ValueError("Заявка не принимает отклики")
        if req.created_by_id == created_by.id and target_side == TeamSide.AUTHOR:
            raise ValueError("Автор уже в команде автора")

        team = req.teams.filter(side=target_side).first()
        if team is None and target_side != TeamSide.OPPONENT:
            raise ValueError("Команда автора должна существовать")
        current_in_team = team.members.count() if team else 0
        if current_in_team + len(players) > 2:
            raise ValueError("В команде не может быть больше 2 человек")

        fresh_players = _load_players(player_ids, req.sport)
        for p in fresh_players:
            if p.id == req.created_by_id and target_side == TeamSide.AUTHOR:
                raise ValueError("Автор уже в команде")
            if DoublesTeamMember.objects.filter(
                team__match_request=req, player=p
            ).exists():
                raise ValueError(f"Игрок {p} уже в одной из команд этой заявки")
            if DoublesJoinRequest.objects.filter(
                match_request=req,
                status=DoublesJoinRequestStatus.PENDING,
                members__player=p,
            ).exists():
                raise ValueError(f"У игрока {p} уже есть ожидающая заявка на эту игру")

        jr: DoublesJoinRequest = DoublesJoinRequest.objects.create(
            match_request=req,
            target_side=target_side,
            status=DoublesJoinRequestStatus.PENDING,
            created_by=created_by,
        )
        for i, player_id in enumerate(player_ids, start=1):
            DoublesJoinRequestMember.objects.create(
                join_request=jr,
                player_id=player_id,
                order=i,
            )
        logger.info(
            "Создан отклик {}: request_id={}, side={}, player_ids={}",
            jr.pk,
            match_request_id,
            target_side,
            [p.id for p in players],
        )
        return jr


def accept_join_request(
    *,
    match_request_id: int,
    join_request_id: int,
    accepted_by: Player,
) -> None:
    """Принять заявку на присоединение и добавить игроков в целевую команду. Только автор заявки."""
    with transaction.atomic():
        req = _lock_request(match_request_id)
        if req.created_by_id != accepted_by.id:
            raise PermissionError("Только автор заявки может принимать отклики")
        _assert_unconfirmed(req)
        if req.status not in (
            DoublesMatchRequestStatus.OPEN,
            DoublesMatchRequestStatus.FORMING,
        ):
            raise ValueError("Заявка не в статусе приёма игроков")

        jr = _pending_join(req, join_request_id)
        target_side = _normalize_team_side(jr.target_side)
        team = req.teams.filter(side=target_side).first()
        if team is None:
            if target_side != TeamSide.OPPONENT:
                raise ValueError("Команда автора должна существовать")
            team = DoublesTeam.objects.create(match_request=req, side=TeamSide.OPPONENT)

        members_to_add = list(
            jr.members.order_by("order").values_list("player_id", flat=True)
        )
        _validate_join_ids(members_to_add, jr.created_by_id)
        current_count = team.members.count()
        if current_count + len(members_to_add) > 2:
            raise ValueError("В команде не может быть больше 2 человек")

        _load_players(members_to_add, req.sport)
        if DoublesTeamMember.objects.filter(
            team__match_request=req, player_id__in=members_to_add
        ).exists():
            raise ValueError("Один из участников уже входит в команду этой заявки.")
        for player_id in members_to_add:
            DoublesTeamMember.objects.create(
                team=team, player_id=player_id, is_captain=False
            )
        jr.status = DoublesJoinRequestStatus.ACCEPTED
        jr.processed_at = timezone.now()
        jr.save(update_fields=["status", "processed_at", "updated_at"])

        _recompute_request_status(req)
        _notify_join_accepted(req, jr)
        logger.info(
            "Принят отклик {}: request_id={}",
            join_request_id,
            match_request_id,
        )


def reject_join_request(
    *,
    match_request_id: int,
    join_request_id: int,
    rejected_by: Player,
) -> None:
    """Отклонить заявку на присоединение. Только автор заявки."""
    with transaction.atomic():
        req = _lock_request(match_request_id)
        if req.created_by_id != rejected_by.id:
            raise PermissionError("Только автор заявки может отклонять отклики")

        jr = _pending_join(req, join_request_id)
        jr.status = DoublesJoinRequestStatus.REJECTED
        jr.processed_at = timezone.now()
        jr.save(update_fields=["status", "processed_at", "updated_at"])
        logger.info("Отклонён отклик {}", join_request_id)


def cancel_join_request(
    *,
    join_request_id: int,
    cancelled_by: Player,
) -> None:
    """Отменить свою заявку на присоединение (только pending)."""
    with transaction.atomic():
        request_id = (
            DoublesJoinRequest.objects.filter(pk=join_request_id)
            .values_list("match_request_id", flat=True)
            .first()
        )
        if request_id is None:
            raise ValueError("Отклик не найден.")
        req = _lock_request(request_id)
        jr = _pending_join(req, join_request_id)
        if jr.status != DoublesJoinRequestStatus.PENDING:
            raise ValueError("Можно отменить только ожидающую заявку")
        if jr.created_by_id != cancelled_by.id:
            raise PermissionError("Можно отменить только свою заявку")
        jr.status = DoublesJoinRequestStatus.CANCELLED
        jr.save(update_fields=["status", "updated_at"])
        logger.info("Отменён отклик {}: player_id={}", join_request_id, cancelled_by.id)


def add_partner_to_author_team(
    *,
    match_request_id: int,
    player_id: int,
    added_by: Player,
) -> None:
    """Добавить партнёра в команду автора. Только автор заявки, только в свою команду, не более 2 человек."""
    with transaction.atomic():
        req = _lock_request(match_request_id)
        if req.created_by_id != added_by.id:
            raise PermissionError(
                "Только автор может добавлять партнёра в свою команду"
            )
        _assert_unconfirmed(req)
        current_status: str = req.status
        if current_status not in (
            DoublesMatchRequestStatus.OPEN,
            DoublesMatchRequestStatus.FORMING,
        ):
            raise ValueError("Нельзя менять состав в текущем статусе")

        author_team = req.teams.get(side=TeamSide.AUTHOR)
        if author_team.members.count() >= 2:
            raise ValueError("В команде уже 2 человека")
        if player_id == req.created_by_id:
            raise ValueError("Вы уже в команде")
        if DoublesTeamMember.objects.filter(
            team__match_request=req, player_id=player_id
        ).exists():
            raise ValueError("Этот игрок уже в одной из команд")

        _load_players([player_id], req.sport)
        DoublesTeamMember.objects.create(
            team=author_team,
            player_id=player_id,
            is_captain=False,
        )
        _recompute_request_status(req)
        logger.info(
            "Партнёр {} добавлен в команду автора: request_id={}",
            player_id,
            match_request_id,
        )


def remove_team_member(
    *,
    match_request_id: int,
    team_side: str,
    player_id: int,
    removed_by: Player,
) -> None:
    """Удалить участника из команды. Только автор заявки. Нельзя удалить автора из команды автора."""
    with transaction.atomic():
        req = _lock_request(match_request_id)
        if req.created_by_id != removed_by.id:
            raise PermissionError("Только автор заявки может менять состав")
        _assert_unconfirmed(req)
        if req.status not in (
            DoublesMatchRequestStatus.OPEN,
            DoublesMatchRequestStatus.FORMING,
            DoublesMatchRequestStatus.READY,
        ):
            raise ValueError("Состав нельзя менять в текущем статусе")

        team_side = _normalize_team_side(team_side)
        team = req.teams.filter(side=team_side).first()
        if team is None:
            raise ValueError("Команда не найдена.")
        if team_side == TeamSide.AUTHOR and player_id == req.created_by_id:
            raise ValueError("Нельзя удалить автора из команды автора")

        member = DoublesTeamMember.objects.filter(
            team=team, player_id=player_id
        ).first()
        if member is None:
            raise ValueError("Участник уже удалён из команды.")
        member.delete()
        _recompute_request_status(req)
        logger.info(
            "Участник {} удалён из команды {}: request_id={}",
            player_id,
            team_side,
            match_request_id,
        )


def confirm_match(match_request_id: int, confirmed_by: Player) -> Match:
    """Подтвердить состав и создать матч. Только автор, только при status=ready."""
    with transaction.atomic():
        req = _lock_request(match_request_id)
        if req.created_by_id != confirmed_by.id:
            raise PermissionError("Только автор может подтвердить матч")
        _assert_unconfirmed(req)
        if req.kind != DoublesMatchKind.CLASSIC:
            raise ValueError(
                "Для командного спарринга нужно сформировать всю серию матчей."
            )
        if req.status != DoublesMatchRequestStatus.READY:
            raise ValueError(
                "Матч можно подтвердить только при полных составах (ready)"
            )

        author_team = req.teams.get(side=TeamSide.AUTHOR)
        opponent_team = req.teams.get(side=TeamSide.OPPONENT)
        if author_team.members.count() != 2 or opponent_team.members.count() != 2:
            raise ValueError("Обе команды должны быть по 2 человека")

        match = _create_doubles_match_from_teams(
            author_team=author_team,
            opponent_team=opponent_team,
            is_friendly=req.is_friendly,
            team_request=req,
        )
        req.status = DoublesMatchRequestStatus.CONFIRMED
        req.confirmed_at = timezone.now()
        req.match = match
        req.save(update_fields=["status", "confirmed_at", "match", "updated_at"])
        logger.info(
            "Подтверждена парная заявка {}: match_id={}",
            match_request_id,
            match.pk,
        )
        return match


def cancel_match_request(match_request_id: int, cancelled_by: Player) -> None:
    """Отменить заявку на парный матч. Только автор."""
    with transaction.atomic():
        req = _lock_request(match_request_id)
        if req.created_by_id != cancelled_by.id:
            raise PermissionError("Только автор может отменить заявку")
        _assert_unconfirmed(req)
        if req.status == DoublesMatchRequestStatus.CONFIRMED:
            raise ValueError("Подтверждённую заявку отменить нельзя")
        if req.status == DoublesMatchRequestStatus.CANCELLED:
            return
        req.status = DoublesMatchRequestStatus.CANCELLED
        req.save(update_fields=["status", "updated_at"])
        logger.info("Отменена парная заявка {}", match_request_id)


def delete_match_request(match_request_id: int, deleted_by: Player) -> str:
    """Удалить заявку на парный или командный спарринг. Только автор.

    Команды и отклики удаляются вместе с неподтверждённой заявкой. Если матчи
    уже созданы, удаление запрещено: их состав, режим и контекст списания сохраняются.

    Args:
        match_request_id: Идентификатор заявки.
        deleted_by: Игрок, который удаляет заявку.

    Returns:
        str: Тип заявки (``classic`` или ``team``) до удаления.

    Raises:
        ValueError: Если заявки нет или матчи уже созданы.
        PermissionError: Если удаляет не автор.
    """
    with transaction.atomic():
        req = _lock_request(match_request_id)
        if req.created_by_id != deleted_by.id:
            raise PermissionError("Только автор может удалить заявку")
        _assert_unconfirmed(req)
        kind = str(req.kind)
        req.delete()
        logger.info(
            "Удалена парная заявка {}: player_id={}",
            match_request_id,
            deleted_by.id,
        )
        return kind


def _recompute_request_status(req: DoublesMatchRequest) -> None:
    """Пересчитать готовность только для непротиворечивого, ещё не созданного состава."""
    _assert_unconfirmed(req)
    author_team = req.teams.filter(side=TeamSide.AUTHOR).first()
    opponent_team = req.teams.filter(side=TeamSide.OPPONENT).first()
    a_full = author_team is not None and author_team.members.count() == 2
    o_full = opponent_team is not None and opponent_team.members.count() == 2
    old_status: str = req.status
    if a_full and o_full:
        assert author_team is not None and opponent_team is not None
        _roster_ids(author_team, opponent_team)
        req.status = DoublesMatchRequestStatus.READY
    else:
        req.status = DoublesMatchRequestStatus.FORMING
    req.save(update_fields=["status", "updated_at"])
    if (
        old_status != req.status
        and req.status == DoublesMatchRequestStatus.READY
        and req.kind == DoublesMatchKind.TEAM
    ):
        _notify_team_sparring_ready(req)


def _create_doubles_match_from_teams(
    *,
    author_team: DoublesTeam,
    opponent_team: DoublesTeam,
    is_friendly: bool,
    team_request: DoublesMatchRequest | None = None,
) -> Match:
    """Создать Match (2×2) из двух DoublesTeam. Использует player1/partner1 и player2/partner2."""
    a_members, o_members = _roster_ids(author_team, opponent_team)
    players = {
        player.pk: player
        for player in _load_players(
            a_members + o_members, team_request.sport if team_request else "tennis"
        )
    }
    p1, p1_partner = players[a_members[0]], players[a_members[1]]
    p2, p2_partner = players[o_members[0]], players[o_members[1]]

    rating_status = (
        Match.RatingCalcStatus.NOT_APPLICABLE
        if is_friendly
        else Match.RatingCalcStatus.PENDING
    )

    match: Match = Match.objects.create(
        tournament=None,
        match_type=Match.MatchType.SPARRING,
        sparring_response=None,
        sparring_team_request=team_request,
        player1=p1,
        player2=p2,
        partner1=p1_partner,
        partner2=p2_partner,
        status=Match.MatchStatus.SCHEDULED,
        deadline=timezone.now() + timedelta(days=7),
        rating_status=rating_status,
        sport=team_request.sport if team_request is not None else "tennis",
    )
    return match


def _create_team_sparring_singles(
    *,
    author_team: DoublesTeam,
    opponent_team: DoublesTeam,
    is_friendly: bool,
    team_request: DoublesMatchRequest,
) -> list[Match]:
    """
    Создать одиночные матчи для командного спарринга.

    Каждый игрок команды A играет с каждым игроком команды B (4 матча 1×1).
    """
    a_ids, o_ids = _roster_ids(author_team, opponent_team)
    players = {
        player.pk: player for player in _load_players(a_ids + o_ids, team_request.sport)
    }

    rating_status = (
        Match.RatingCalcStatus.NOT_APPLICABLE
        if is_friendly
        else Match.RatingCalcStatus.PENDING
    )

    pairs: list[tuple[int, int]] = [
        (a_ids[0], o_ids[0]),
        (a_ids[0], o_ids[1]),
        (a_ids[1], o_ids[0]),
        (a_ids[1], o_ids[1]),
    ]

    matches: list[Match] = []
    for p1_id, p2_id in pairs:
        match = Match.objects.create(
            tournament=None,
            match_type=Match.MatchType.SPARRING,
            sparring_response=None,
            sparring_team_request=team_request,
            player1=players[p1_id],
            player2=players[p2_id],
            status=Match.MatchStatus.SCHEDULED,
            deadline=timezone.now() + timedelta(days=7),
            rating_status=rating_status,
            sport=team_request.sport,
        )
        matches.append(match)
    return matches


def confirm_team_sparring_series(
    match_request_id: int, confirmed_by: Player
) -> list[Match]:
    """
    Подтвердить состав командного спарринга и создать серию матчей.

    Создаются 4 одиночных матча (каждый против каждого из другой команды)
    и один парный матч 2×2 между командами.
    """
    with transaction.atomic():
        req = _lock_request(match_request_id)
        if req.created_by_id != confirmed_by.id:
            raise PermissionError("Только автор может сформировать матчи")
        _assert_unconfirmed(req)
        if req.kind != DoublesMatchKind.TEAM:
            raise ValueError("Эта заявка не является командным спаррингом")
        if req.status != DoublesMatchRequestStatus.READY:
            raise ValueError(
                "Матчи можно сформировать только при полных составах (ready)"
            )

        author_team = req.teams.get(side=TeamSide.AUTHOR)
        opponent_team = req.teams.get(side=TeamSide.OPPONENT)
        if author_team.members.count() != 2 or opponent_team.members.count() != 2:
            raise ValueError("Обе команды должны быть по 2 человека")

        singles_matches = _create_team_sparring_singles(
            author_team=author_team,
            opponent_team=opponent_team,
            is_friendly=req.is_friendly,
            team_request=req,
        )
        doubles_match = _create_doubles_match_from_teams(
            author_team=author_team,
            opponent_team=opponent_team,
            is_friendly=req.is_friendly,
            team_request=req,
        )

        req.status = DoublesMatchRequestStatus.CONFIRMED
        req.confirmed_at = timezone.now()
        req.match = doubles_match
        req.save(update_fields=["status", "confirmed_at", "match", "updated_at"])
        logger.info(
            "Подтверждена командная заявка {}: создано {} матчей "
            "({} одиночных, парный match_id={})",
            match_request_id,
            len(singles_matches) + 1,
            len(singles_matches),
            doubles_match.pk,
        )
        return [*singles_matches, doubles_match]


def _notify_team_sparring_ready(req: DoublesMatchRequest) -> None:
    """Уведомить автора командного спарринга о готовности команд (4 участника)."""
    from apps.telegram_bot.notifications import notify_team_sparring_ready

    create_notification(
        user=req.created_by.user,
        message=(
            "Командный спарринг: все заявки одобрены, команды сформированы. "
            "Нажмите «Сформировать матчи», чтобы создать одиночные и парный матчи."
        ),
        url=reverse("doubles_detail", args=[req.pk]),
        event_key="sparring.team_ready",
    )
    notify_team_sparring_ready(req)
