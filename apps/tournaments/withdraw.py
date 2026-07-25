"""
Снятие участника кругового турнира после старта: walkover оставшихся матчей, возврат FT.

Вызывается из:
- apps/tournaments/views.py — tournament_manage_withdraw_participant
- apps/tournaments/round_robin.py — get_withdrawn_player_ids при finalize
- tests/integration/test_tournament_withdraw.py
Аналогов нет: cancel.py отменяет весь турнир; remove_participant только до сетки.
"""

from __future__ import annotations

import logging
import secrets
from typing import TYPE_CHECKING

from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.subscriptions.fancoin import TOURNAMENT_REGISTRATION_COST
from apps.subscriptions.models import FancoinTransaction
from apps.telegram_bot.notifications import send_to_user_by_user
from apps.users.models import Notification, Player

from .models import (
    Match,
    Tournament,
    TournamentEntryPayment,
    TournamentEntryRefundRequest,
    TournamentRegistrationCoverage,
    TournamentStatus,
    TournamentTeam,
    TournamentWithdrawal,
)

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser

logger = logging.getLogger(__name__)

ROUND_ROBIN_FORMAT = "round_robin"


def _is_round_robin(tournament: Tournament) -> bool:
    """Проверить, что турнир в круговом формате."""
    return getattr(tournament, "format", None) == ROUND_ROBIN_FORMAT


def get_withdrawn_player_ids(tournament: Tournament) -> set[int]:
    """Вернуть id игроков, снятых с турнира (включая членов снятых команд).

    Args:
        tournament (Tournament): Турнир.

    Returns:
        set[int]: Идентификаторы снятых игроков.
    """
    ids: set[int] = set(
        TournamentWithdrawal.objects.filter(
            tournament=tournament,
            player_id__isnull=False,
        ).values_list("player_id", flat=True)
    )
    for team in TournamentTeam.objects.filter(
        pk__in=TournamentWithdrawal.objects.filter(
            tournament=tournament,
            team_id__isnull=False,
        ).values_list("team_id", flat=True)
    ).only("player1_id", "player2_id"):
        if team.player1_id:
            ids.add(team.player1_id)
        if team.player2_id:
            ids.add(team.player2_id)
    return ids


def get_withdrawn_team_ids(tournament: Tournament) -> set[int]:
    """Вернуть id команд, снятых с турнира.

    Args:
        tournament (Tournament): Турнир.

    Returns:
        set[int]: Идентификаторы снятых команд.
    """
    return set(
        TournamentWithdrawal.objects.filter(
            tournament=tournament,
            team_id__isnull=False,
        ).values_list("team_id", flat=True)
    )


def is_player_withdrawn(tournament: Tournament, player: Player) -> bool:
    """Проверить, снят ли игрок с турнира.

    Args:
        tournament (Tournament): Турнир.
        player (Player): Игрок.

    Returns:
        bool: ``True``, если игрок или его команда сняты.
    """
    if TournamentWithdrawal.objects.filter(
        tournament=tournament, player=player
    ).exists():
        return True
    if tournament.is_doubles():
        return bool(
            TournamentWithdrawal.objects.filter(
                tournament=tournament,
                team__isnull=False,
            )
            .filter(Q(team__player1=player) | Q(team__player2=player))
            .exists()
        )
    return False


def _finished_matches_qs(
    tournament: Tournament,
    *,
    player: Player | None = None,
    team: TournamentTeam | None = None,
):
    """QuerySet завершённых матчей участника/команды."""
    qs = tournament.matches.filter(
        status__in=[Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER],
    )
    if team is not None:
        return qs.filter(Q(team1=team) | Q(team2=team))
    assert player is not None
    return qs.filter(Q(player1=player) | Q(player2=player))


def _scheduled_matches_qs(
    tournament: Tournament,
    *,
    player: Player | None = None,
    team: TournamentTeam | None = None,
):
    """QuerySet незавершённых матчей участника/команды."""
    qs = tournament.matches.filter(status=Match.MatchStatus.SCHEDULED)
    if team is not None:
        return qs.filter(Q(team1=team) | Q(team2=team))
    assert player is not None
    return qs.filter(Q(player1=player) | Q(player2=player))


def _already_refunded_withdrawal_ft(user, tournament: Tournament) -> bool:
    """Проверить, что FT за снятие с этого турнира уже возвращались."""
    return bool(
        FancoinTransaction.objects.filter(
            user=user,
            tournament=tournament,
            reason=FancoinTransaction.Reason.TOURNAMENT_WITHDRAWAL,
            direction=FancoinTransaction.Direction.REFUND,
        ).exists()
    )


def _refund_subscription_coverage(user, tournament: Tournament) -> bool:
    """Вернуть FT по покрытию подписки при снятии без сыгранных матчей."""
    if _already_refunded_withdrawal_ft(user, tournament):
        return True
    try:
        sub = getattr(user, "subscription", None)
        if not sub:
            return False
        sub.refund_fancoin(
            TOURNAMENT_REGISTRATION_COST,
            reason=FancoinTransaction.Reason.TOURNAMENT_WITHDRAWAL,
            tournament=tournament,
        )
        return True
    except Exception as e:
        logger.warning(
            "Could not refund FT on withdrawal for user %s: %s",
            getattr(user, "pk", None),
            e,
        )
        return False


def _restore_club_plan_slot(user, tournament: Tournament) -> bool:
    """Вернуть слот клубного тарифа, если регистрация была им покрыта."""
    if not tournament.club_id:
        return False
    coverage = tournament.registration_coverages.filter(
        user=user,
        coverage_type=TournamentRegistrationCoverage.CoverageType.CLUB_PLAN_SLOT,
    ).first()
    if not coverage:
        return False
    try:
        from apps.clubs.models import ClubMember
        from apps.clubs.plan_services import restore_member_tournament_limit

        member = ClubMember.objects.filter(
            club_id=tournament.club_id, user=user
        ).first()
        if not member:
            return False
        restored = restore_member_tournament_limit(member)
        coverage.delete()
        return restored
    except Exception as e:
        logger.warning(
            "Could not restore club plan slot on withdrawal for user %s: %s",
            getattr(user, "pk", None),
            e,
        )
        return False


def _create_entry_refund_request(
    user, tournament: Tournament
) -> TournamentEntryRefundRequest | None:
    """Создать заявку на возврат ₽-взноса, если он был оплачен."""
    entry_fee = getattr(tournament, "entry_fee", None) or 0
    if not entry_fee or float(entry_fee) <= 0:
        return None
    if not TournamentEntryPayment.objects.filter(
        tournament=tournament, user=user
    ).exists():
        return None
    refund_ref = "REF-" + secrets.token_urlsafe(8).upper()[:10]
    while TournamentEntryRefundRequest.objects.filter(refund_ref=refund_ref).exists():
        refund_ref = "REF-" + secrets.token_urlsafe(8).upper()[:10]
    refund: TournamentEntryRefundRequest = TournamentEntryRefundRequest.objects.create(
        tournament=tournament,
        user=user,
        amount=entry_fee,
        refund_ref=refund_ref,
    )
    return refund


def _apply_withdrawal_walkover(
    match: Match,
    winner: Player,
    *,
    winner_team: TournamentTeam | None = None,
) -> None:
    """Оформить тех. победу сопернику при снятии участника (без счёта 6:0)."""
    is_doubles = bool(match.team1_id and match.team2_id)
    if is_doubles and winner_team is not None:
        match.winner_team = winner_team
        match.winner = winner_team.player1
    else:
        match.winner = winner
    match.status = Match.MatchStatus.WALKOVER
    match.completed_datetime = timezone.now()
    update_fields = ["winner", "status", "completed_datetime"]
    if is_doubles:
        update_fields.append("winner_team")
    match.save(update_fields=update_fields)
    match.result_proposals.filter(status=Match.ProposalStatus.PENDING).update(
        status=Match.ProposalStatus.REJECTED
    )


def _match_round_label(match: Match) -> str:
    """Краткое название тура/раунда матча."""
    return (match.round_name or "").strip() or f"Тур {match.round_index or '—'}"


def _opponent_label_for_player(match: Match, player: Player) -> str:
    """Подпись соперника относительно снятого игрока."""
    other = match.player2 if match.player1_id == player.pk else match.player1
    return str(other) if other else "соперник"


def _opponent_label_for_team(match: Match, team: TournamentTeam) -> str:
    """Подпись соперничающей команды относительно снятой."""
    other = match.team2 if match.team1_id == team.pk else match.team1
    return str(other) if other else "соперник"


def _match_line_for_player(match: Match, player: Player) -> str:
    """Строка матча для уведомления снятого игрока."""
    return f"{_match_round_label(match)}: против {_opponent_label_for_player(match, player)}"


def _match_line_for_team(match: Match, team: TournamentTeam) -> str:
    """Строка матча для уведомления снятой команды."""
    return (
        f"{_match_round_label(match)}: против {_opponent_label_for_team(match, team)}"
    )


def _opponent_users_from_match(
    match: Match,
    *,
    withdrawn_player: Player | None = None,
    withdrawn_team: TournamentTeam | None = None,
) -> list:
    """Пользователи стороны-соперника (кому присуждена тех. победа)."""
    users = []
    if withdrawn_team is not None and match.team1_id and match.team2_id:
        winner_team = (
            match.team2 if match.team1_id == withdrawn_team.pk else match.team1
        )
        if winner_team is None:
            return []
        for p in (winner_team.player1, winner_team.player2):
            if p and not getattr(p, "is_bye", False) and p.user_id:
                users.append(p.user)
        return users
    if withdrawn_player is not None:
        winner = (
            match.player2 if match.player1_id == withdrawn_player.pk else match.player1
        )
        if winner and not getattr(winner, "is_bye", False) and winner.user_id:
            users.append(winner.user)
    return users


def _absolute_url(path: str) -> str:
    """Собрать абсолютный URL из path reverse."""
    if not path:
        return ""
    try:
        from django.conf import settings

        base = (getattr(settings, "TELEGRAM_BOT_SITE_BASE_URL", "") or "").rstrip("/")
        if not base:
            domain = getattr(settings, "SITE_DOMAIN", "tennisfan.ru")
            base = f"https://{domain}"
        return f"{base}{path}"
    except Exception:
        return path


def _truncate_notification_message(text: str, *, max_len: int = 255) -> str:
    """Обрезать текст под Notification.message (CharField max_length=255)."""
    text = text.strip()
    if len(text) <= max_len:
        return text
    if max_len <= 1:
        return text[:max_len]
    return text[: max_len - 1].rstrip() + "…"


def _notify_withdrawn_user(
    user,
    tournament: Tournament,
    *,
    match_lines: list[str],
    refunded_ft: bool,
    refund_request: TournamentEntryRefundRequest | None,
) -> None:
    """Уведомить снятого: ЛК, Telegram и email."""
    from apps.core.email_service import send_tournament_participant_withdrawn_email

    try:
        url = reverse("tournament_detail", args=[tournament.slug])
    except Exception:
        url = ""

    lines_block = ""
    if match_lines:
        lines_block = (
            " Несыгранные матчи закрыты со статусом «Без игры» "
            "(техническая победа соперникам): " + "; ".join(match_lines) + "."
        )
    else:
        lines_block = " Несыгранных матчей на момент снятия не осталось."

    if refunded_ft:
        refund_part = f" На ваш баланс возвращено {TOURNAMENT_REGISTRATION_COST} FT."
        refund_part_lk = refund_part
    elif refund_request is not None:
        refund_part = (
            " Взнос был оплачен — для возврата средств обратитесь к администратору."
        )
        refund_part_lk = " Для возврата взноса обратитесь к администратору."
    else:
        refund_part = ""
        refund_part_lk = ""

    # Полный текст — в Telegram/email; в ЛК — короткий (max 255).
    message = f"Вы сняты с турнира «{tournament.name}».{lines_block}{refund_part}"
    message_lk = _truncate_notification_message(
        f"Вы сняты с турнира «{tournament.name}». "
        f"Несыгранные матчи закрыты без игры.{refund_part_lk}"
    )
    Notification.objects.create(user=user, message=message_lk, url=url)
    try:
        send_to_user_by_user(user, message, skip_email=True)
    except Exception as e:
        logger.warning("TG notify withdrawal failed for user %s: %s", user.pk, e)
    try:
        send_tournament_participant_withdrawn_email(
            user,
            tournament,
            match_lines=match_lines,
            refunded_ft=TOURNAMENT_REGISTRATION_COST if refunded_ft else 0,
            has_entry_refund=refund_request is not None,
        )
    except Exception as e:
        logger.warning("Email notify withdrawal failed for user %s: %s", user.pk, e)


def _notify_opponents(
    tournament: Tournament,
    *,
    withdrawn_label: str,
    closed_matches: list[Match],
    withdrawn_player: Player | None = None,
    withdrawn_team: TournamentTeam | None = None,
) -> None:
    """Уведомить соперников: ЛК, Telegram и email о закрытии матча «Без игры»."""
    from apps.core.email_service import send_tournament_opponent_match_closed_email

    try:
        tournament_path = reverse("tournament_detail", args=[tournament.slug])
    except Exception:
        tournament_path = ""

    for match in closed_matches:
        if withdrawn_team is not None:
            match_line = _match_line_for_team(match, withdrawn_team)
        elif withdrawn_player is not None:
            match_line = _match_line_for_player(match, withdrawn_player)
        else:
            match_line = _match_round_label(match)

        try:
            match_path = reverse("match_detail", args=[match.pk])
        except Exception:
            match_path = ""

        message = (
            f"Участник {withdrawn_label} снят с турнира «{tournament.name}». "
            f"Ваш матч с ним закрыт со статусом «Без игры» — вам присуждена "
            f"техническая победа ({match_line})."
        )
        message_lk = _truncate_notification_message(
            f"{withdrawn_label} снят с турнира «{tournament.name}». "
            f"Матч закрыт без игры — вам тех. победа."
        )
        opponent_users = _opponent_users_from_match(
            match,
            withdrawn_player=withdrawn_player,
            withdrawn_team=withdrawn_team,
        )
        for user in opponent_users:
            Notification.objects.create(
                user=user, message=message_lk, url=match_path or tournament_path
            )
            try:
                send_to_user_by_user(user, message, skip_email=True)
            except Exception as e:
                logger.warning(
                    "TG notify opponent on withdrawal failed user=%s: %s",
                    user.pk,
                    e,
                )
            try:
                send_tournament_opponent_match_closed_email(
                    user,
                    tournament,
                    withdrawn_label=withdrawn_label,
                    match_line=match_line,
                    match_url=_absolute_url(match_path),
                )
            except Exception as e:
                logger.warning(
                    "Email notify opponent on withdrawal failed user=%s: %s",
                    user.pk,
                    e,
                )


def _process_user_refunds(
    user,
    tournament: Tournament,
    *,
    finished_count: int,
) -> tuple[bool, TournamentEntryRefundRequest | None]:
    """Обработать возвраты для одного пользователя при снятии.

    Returns:
        tuple: (fancoin_refunded, entry_refund_request_or_none).
    """
    if finished_count > 0:
        return False, None

    ft_refunded = False
    coverage = tournament.registration_coverages.filter(user=user).first()
    if coverage and coverage.coverage_type == (
        TournamentRegistrationCoverage.CoverageType.SUBSCRIPTION_SLOT
    ):
        if _refund_subscription_coverage(user, tournament):
            ft_refunded = True
            coverage.delete()
    elif coverage and coverage.coverage_type == (
        TournamentRegistrationCoverage.CoverageType.CLUB_PLAN_SLOT
    ):
        _restore_club_plan_slot(user, tournament)

    refund_request = _create_entry_refund_request(user, tournament)
    return ft_refunded, refund_request


def _walkover_scheduled_for_player(
    tournament: Tournament, player: Player
) -> list[Match]:
    """Закрыть scheduled-матчи игрока тех. победой сопернику.

    Returns:
        list[Match]: Закрытые матчи.
    """
    closed: list[Match] = []
    matches = list(
        _scheduled_matches_qs(tournament, player=player).select_related(
            "player1__user", "player2__user"
        )
    )
    for match in matches:
        winner = match.player2 if match.player1_id == player.pk else match.player1
        if winner is None or getattr(winner, "is_bye", False):
            continue
        _apply_withdrawal_walkover(match, winner)
        closed.append(match)
    return closed


def _walkover_scheduled_for_team(
    tournament: Tournament, team: TournamentTeam
) -> list[Match]:
    """Закрыть scheduled-матчи команды тех. победой сопернику.

    Returns:
        list[Match]: Закрытые матчи.
    """
    closed: list[Match] = []
    matches = list(
        _scheduled_matches_qs(tournament, team=team).select_related(
            "team1__player1__user",
            "team1__player2__user",
            "team2__player1__user",
            "team2__player2__user",
            "player1__user",
            "player2__user",
        )
    )
    for match in matches:
        winner_team = match.team2 if match.team1_id == team.pk else match.team1
        if winner_team is None:
            continue
        winner = winner_team.player1
        if winner is None or getattr(winner, "is_bye", False):
            continue
        _apply_withdrawal_walkover(match, winner, winner_team=winner_team)
        closed.append(match)
    return closed


@transaction.atomic
def withdraw_participant(
    tournament: Tournament,
    *,
    withdrawn_by: AbstractBaseUser | None = None,
    player: Player | None = None,
    team: TournamentTeam | None = None,
) -> tuple[bool, str]:
    """Снять участника/команду с кругового турнира после старта сетки.

    Игрок остаётся в списке; scheduled-матчи → статус «Без игры» соперникам.
    FT возвращаются только если до снятия не было завершённых матчей.

    Args:
        tournament (Tournament): Турнир.
        withdrawn_by: Пользователь, выполнивший снятие.
        player (Player | None): Игрок (одиночный турнир).
        team (TournamentTeam | None): Команда (парный турнир).

    Returns:
        tuple[bool, str]: Успех и сообщение для UI.
    """
    if not _is_round_robin(tournament):
        return False, "Снятие после старта доступно только для кругового турнира."
    if tournament.status in (TournamentStatus.CANCELLED, TournamentStatus.COMPLETED):
        return (
            False,
            "Нельзя снимать участников из отменённого или завершённого турнира.",
        )
    if not tournament.bracket_generated:
        return False, "Сетка ещё не сформирована — используйте удаление участника."
    if (player is None) == (team is None):
        return False, "Укажите игрока или команду."

    if team is not None:
        if team.tournament_id != tournament.pk:
            return False, "Команда не принадлежит этому турниру."
        if TournamentWithdrawal.objects.filter(
            tournament=tournament, team=team
        ).exists():
            return False, "Команда уже снята с турнира."
        finished_count = _finished_matches_qs(tournament, team=team).count()
        label = str(team)
        withdrawal = TournamentWithdrawal.objects.create(
            tournament=tournament,
            team=team,
            withdrawn_by=withdrawn_by,
        )
        closed_matches = _walkover_scheduled_for_team(tournament, team)
        match_lines = [_match_line_for_team(m, team) for m in closed_matches]
        users = []
        for p in (team.player1, team.player2):
            if p and p.user_id:
                users.append(p.user)
        any_ft = False
        for user in users:
            ft_ok, refund_req = _process_user_refunds(
                user, tournament, finished_count=finished_count
            )
            if ft_ok:
                any_ft = True
            _notify_withdrawn_user(
                user,
                tournament,
                match_lines=match_lines,
                refunded_ft=ft_ok,
                refund_request=refund_req,
            )
        if any_ft:
            withdrawal.fancoin_refunded = True
            withdrawal.save(update_fields=["fancoin_refunded"])
        _notify_opponents(
            tournament,
            withdrawn_label=label,
            closed_matches=closed_matches,
            withdrawn_team=team,
        )
        walkovers = len(closed_matches)
    else:
        assert player is not None
        if not tournament.participants.filter(pk=player.pk).exists():
            return False, "Участник не найден в турнире."
        if TournamentWithdrawal.objects.filter(
            tournament=tournament, player=player
        ).exists():
            return False, "Участник уже снят с турнира."
        finished_count = _finished_matches_qs(tournament, player=player).count()
        label = str(player)
        withdrawal = TournamentWithdrawal.objects.create(
            tournament=tournament,
            player=player,
            withdrawn_by=withdrawn_by,
        )
        closed_matches = _walkover_scheduled_for_player(tournament, player)
        match_lines = [_match_line_for_player(m, player) for m in closed_matches]
        user = player.user
        ft_ok, refund_req = _process_user_refunds(
            user, tournament, finished_count=finished_count
        )
        if ft_ok:
            withdrawal.fancoin_refunded = True
            withdrawal.save(update_fields=["fancoin_refunded"])
        _notify_withdrawn_user(
            user,
            tournament,
            match_lines=match_lines,
            refunded_ft=ft_ok,
            refund_request=refund_req,
        )
        _notify_opponents(
            tournament,
            withdrawn_label=label,
            closed_matches=closed_matches,
            withdrawn_player=player,
        )
        walkovers = len(closed_matches)

    from .round_robin import check_and_finalize_if_complete

    check_and_finalize_if_complete(tournament)

    ft_note = ""
    if finished_count == 0 and getattr(withdrawal, "fancoin_refunded", False):
        ft_note = f" FT возвращены (+{TOURNAMENT_REGISTRATION_COST})."
    elif finished_count > 0:
        ft_note = " FT не возвращены (есть сыгранные матчи)."
    return (
        True,
        f"Участник снят. Матчей закрыто со статусом «Без игры»: {walkovers}.{ft_note}",
    )
