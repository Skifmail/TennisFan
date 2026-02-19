"""
Сервисы формирования парного спарринга 2×2.
Атомарные операции с select_for_update, проверки бизнес-логики.
"""

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, cast

from django.db import transaction
from django.utils import timezone

from apps.tournaments.models import Match

from .models import (
    DoublesJoinRequest,
    DoublesJoinRequestMember,
    DoublesJoinRequestStatus,
    DoublesMatchRequest,
    DoublesMatchRequestStatus,
    DoublesTeam,
    DoublesTeamMember,
    TeamSide,
)

if TYPE_CHECKING:
    from apps.users.models import Player

logger = logging.getLogger(__name__)


def create_doubles_request(
    *,
    created_by: "Player",
    city: str = "",
    preferred_gender: str = "",
    is_friendly: bool = False,
    description: str = "",
    partner: "Player | None" = None,
) -> DoublesMatchRequest:
    """Создать заявку на парный матч 2×2. Автор — капитан своей команды, опционально с партнёром."""
    with transaction.atomic():
        req = DoublesMatchRequest.objects.create(
            status=DoublesMatchRequestStatus.OPEN,
            created_by=created_by,
            city=city,
            preferred_gender=preferred_gender,
            is_friendly=is_friendly,
            description=description,
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
        logger.info(
            "Created doubles match request %s by player %s", req.pk, created_by.id
        )
        return cast(DoublesMatchRequest, req)


def create_join_request(
    *,
    match_request_id: int,
    created_by: "Player",
    target_side: str,
    players: list["Player"],
) -> DoublesJoinRequest:
    """
    Подать заявку на присоединение (1 или 2 игрока).
    Проверки: слоты в команде, игрок не автор, не в другой команде этой заявки, нет дубля в отклике.
    """
    if not players or len(players) > 2:
        raise ValueError("Укажите 1 или 2 игроков")
    if target_side not in (TeamSide.AUTHOR, TeamSide.OPPONENT):
        raise ValueError("Некорректная целевая команда")

    with transaction.atomic():
        req = DoublesMatchRequest.objects.select_for_update().get(pk=match_request_id)
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

        for p in players:
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

        jr = DoublesJoinRequest.objects.create(
            match_request=req,
            target_side=target_side,
            status=DoublesJoinRequestStatus.PENDING,
            created_by=created_by,
        )
        for i, p in enumerate(players, start=1):
            DoublesJoinRequestMember.objects.create(
                join_request=jr,
                player=p,
                order=i,
            )
        logger.info(
            "Join request %s created for match_request %s, target_side=%s, players=%s",
            jr.pk,
            match_request_id,
            target_side,
            [p.id for p in players],
        )
        return cast(DoublesJoinRequest, jr)


def accept_join_request(
    *,
    match_request_id: int,
    join_request_id: int,
    accepted_by: "Player",
) -> None:
    """Принять заявку на присоединение и добавить игроков в целевую команду. Только автор заявки."""
    with transaction.atomic():
        req = DoublesMatchRequest.objects.select_for_update().get(pk=match_request_id)
        if req.created_by_id != accepted_by.id:
            raise PermissionError("Только автор заявки может принимать отклики")
        if req.status not in (
            DoublesMatchRequestStatus.OPEN,
            DoublesMatchRequestStatus.FORMING,
        ):
            raise ValueError("Заявка не в статусе приёма игроков")

        jr = DoublesJoinRequest.objects.select_for_update().get(
            pk=join_request_id,
            match_request=req,
            status=DoublesJoinRequestStatus.PENDING,
        )
        team = req.teams.filter(side=jr.target_side).first()
        if team is None:
            if jr.target_side != TeamSide.OPPONENT:
                raise ValueError("Команда автора должна существовать")
            team = DoublesTeam.objects.create(match_request=req, side=TeamSide.OPPONENT)

        members_to_add = list(
            jr.members.order_by("order").values_list("player_id", flat=True)
        )
        if not members_to_add:
            raise ValueError("В заявке нет участников")
        current_count = team.members.count()
        if current_count + len(members_to_add) > 2:
            raise ValueError("В команде не может быть больше 2 человек")

        for player_id in members_to_add:
            DoublesTeamMember.objects.get_or_create(
                team=team,
                player_id=player_id,
                defaults={"is_captain": False},
            )
        jr.status = DoublesJoinRequestStatus.ACCEPTED
        jr.processed_at = timezone.now()
        jr.save(update_fields=["status", "processed_at", "updated_at"])

        _recompute_request_status(req)
        logger.info(
            "Join request %s accepted, match_request %s",
            join_request_id,
            match_request_id,
        )


def reject_join_request(
    *,
    match_request_id: int,
    join_request_id: int,
    rejected_by: "Player",
) -> None:
    """Отклонить заявку на присоединение. Только автор заявки."""
    with transaction.atomic():
        req = DoublesMatchRequest.objects.select_for_update().get(pk=match_request_id)
        if req.created_by_id != rejected_by.id:
            raise PermissionError("Только автор заявки может отклонять отклики")

        jr = DoublesJoinRequest.objects.select_for_update().get(
            pk=join_request_id,
            match_request=req,
            status=DoublesJoinRequestStatus.PENDING,
        )
        jr.status = DoublesJoinRequestStatus.REJECTED
        jr.processed_at = timezone.now()
        jr.save(update_fields=["status", "processed_at", "updated_at"])
        logger.info("Join request %s rejected", join_request_id)


def cancel_join_request(
    *,
    join_request_id: int,
    cancelled_by: "Player",
) -> None:
    """Отменить свою заявку на присоединение (только pending)."""
    with transaction.atomic():
        jr = DoublesJoinRequest.objects.select_for_update().get(pk=join_request_id)
        if jr.status != DoublesJoinRequestStatus.PENDING:
            raise ValueError("Можно отменить только ожидающую заявку")
        if jr.created_by_id != cancelled_by.id:
            raise PermissionError("Можно отменить только свою заявку")
        jr.status = DoublesJoinRequestStatus.CANCELLED
        jr.save(update_fields=["status", "updated_at"])
        logger.info(
            "Join request %s cancelled by player %s", join_request_id, cancelled_by.id
        )


def add_partner_to_author_team(
    *,
    match_request_id: int,
    player_id: int,
    added_by: "Player",
) -> None:
    """Добавить партнёра в команду автора. Только автор заявки, только в свою команду, не более 2 человек."""
    with transaction.atomic():
        req = DoublesMatchRequest.objects.select_for_update().get(pk=match_request_id)
        if req.created_by_id != added_by.id:
            raise PermissionError(
                "Только автор может добавлять партнёра в свою команду"
            )
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

        DoublesTeamMember.objects.create(
            team=author_team,
            player_id=player_id,
            is_captain=False,
        )
        _recompute_request_status(req)
        logger.info(
            "Partner %s added to author team (match_request %s)",
            player_id,
            match_request_id,
        )


def remove_team_member(
    *,
    match_request_id: int,
    team_side: str,
    player_id: int,
    removed_by: "Player",
) -> None:
    """Удалить участника из команды. Только автор заявки. Нельзя удалить автора из команды автора."""
    with transaction.atomic():
        req = DoublesMatchRequest.objects.select_for_update().get(pk=match_request_id)
        if req.created_by_id != removed_by.id:
            raise PermissionError("Только автор заявки может менять состав")
        if req.status not in (
            DoublesMatchRequestStatus.OPEN,
            DoublesMatchRequestStatus.FORMING,
            DoublesMatchRequestStatus.READY,
        ):
            raise ValueError("Состав нельзя менять в текущем статусе")

        team = req.teams.get(side=team_side)
        if team_side == TeamSide.AUTHOR and player_id == req.created_by_id:
            raise ValueError("Нельзя удалить автора из команды автора")

        member = DoublesTeamMember.objects.get(team=team, player_id=player_id)
        member.delete()
        _recompute_request_status(req)
        logger.info(
            "Removed player %s from team %s (match_request %s)",
            player_id,
            team_side,
            match_request_id,
        )


def confirm_match(match_request_id: int, confirmed_by: "Player") -> Match:
    """Подтвердить состав и создать матч. Только автор, только при status=ready."""
    with transaction.atomic():
        req = DoublesMatchRequest.objects.select_for_update().get(pk=match_request_id)
        if req.created_by_id != confirmed_by.id:
            raise PermissionError("Только автор может подтвердить матч")
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
            request_created_at=req.created_at,
        )
        req.status = DoublesMatchRequestStatus.CONFIRMED
        req.confirmed_at = timezone.now()
        req.match = match
        req.save(update_fields=["status", "confirmed_at", "match", "updated_at"])
        logger.info(
            "Doubles match request %s confirmed, match %s created",
            match_request_id,
            match.pk,
        )
        return match


def cancel_match_request(match_request_id: int, cancelled_by: "Player") -> None:
    """Отменить заявку на парный матч. Только автор."""
    with transaction.atomic():
        req = DoublesMatchRequest.objects.select_for_update().get(pk=match_request_id)
        if req.created_by_id != cancelled_by.id:
            raise PermissionError("Только автор может отменить заявку")
        if req.status == DoublesMatchRequestStatus.CONFIRMED:
            raise ValueError("Подтверждённую заявку отменить нельзя")
        if req.status == DoublesMatchRequestStatus.CANCELLED:
            return
        req.status = DoublesMatchRequestStatus.CANCELLED
        req.save(update_fields=["status", "updated_at"])
        logger.info("Doubles match request %s cancelled", match_request_id)


def _recompute_request_status(req: DoublesMatchRequest) -> None:
    author_team = req.teams.filter(side=TeamSide.AUTHOR).first()
    opponent_team = req.teams.filter(side=TeamSide.OPPONENT).first()
    a_full = author_team is not None and author_team.members.count() == 2
    o_full = opponent_team is not None and opponent_team.members.count() == 2
    if a_full and o_full:
        req.status = DoublesMatchRequestStatus.READY
    else:
        req.status = DoublesMatchRequestStatus.FORMING
    req.save(update_fields=["status", "updated_at"])


def _create_doubles_match_from_teams(
    *,
    author_team: DoublesTeam,
    opponent_team: DoublesTeam,
    is_friendly: bool,
    request_created_at,
) -> Match:
    """Создать Match (2×2) из двух DoublesTeam. Использует player1/partner1 и player2/partner2."""
    a_members = list(
        author_team.members.order_by("-is_captain").values_list("player_id", flat=True)
    )
    o_members = list(
        opponent_team.members.order_by("-is_captain").values_list(
            "player_id", flat=True
        )
    )
    if len(a_members) != 2 or len(o_members) != 2:
        raise ValueError("Обе команды должны быть по 2 человека")

    from apps.users.models import Player

    p1 = Player.objects.get(pk=a_members[0])
    p1_partner = Player.objects.get(pk=a_members[1])
    p2 = Player.objects.get(pk=o_members[0])
    p2_partner = Player.objects.get(pk=o_members[1])

    rating_status = (
        Match.RatingCalcStatus.NOT_APPLICABLE
        if is_friendly
        else Match.RatingCalcStatus.PENDING
    )

    match = Match.objects.create(
        tournament=None,
        match_type=Match.MatchType.SPARRING,
        sparring_response=None,
        player1=p1,
        player2=p2,
        partner1=p1_partner,
        partner2=p2_partner,
        status=Match.MatchStatus.SCHEDULED,
        deadline=timezone.now() + timedelta(days=7),
        rating_status=rating_status,
    )
    return cast(Match, match)
