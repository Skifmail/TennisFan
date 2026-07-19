"""
Отмена турнира: статус «Отменён», возврат лимитов регистраций участникам.
"""

import logging

from apps.core.email_service import send_tournament_cancelled_email
from apps.subscriptions.fancoin import TOURNAMENT_REGISTRATION_COST
from apps.subscriptions.models import FancoinTransaction
from apps.telegram_bot.notifications import send_to_user_by_user
from apps.users.models import Notification

from .models import Tournament, TournamentRegistrationCoverage, TournamentStatus

logger = logging.getLogger(__name__)


def _refund_subscription_coverage(user, tournament: Tournament) -> bool:
    """Вернуть FT пользователю с покрытием подписки при отмене турнира.

    Args:
        user: Пользователь участника.
        tournament (Tournament): Отменяемый турнир.

    Returns:
        bool: ``True``, если возврат FT выполнен (или безлимит).
    """
    try:
        sub = getattr(user, "subscription", None)
        if not sub:
            return False
        sub.refund_fancoin(
            TOURNAMENT_REGISTRATION_COST,
            reason=FancoinTransaction.Reason.TOURNAMENT_CANCEL,
            tournament=tournament,
        )
        return True
    except Exception as e:
        logger.warning(
            "Could not refund subscription for user %s: %s",
            getattr(user, "pk", None),
            e,
        )
        return False


def _users_with_subscription_coverage(tournament: Tournament) -> list:
    """Пользователи с покрытием FT (слот подписки) по турниру.

    Args:
        tournament (Tournament): Турнир.

    Returns:
        list: Пользователи с ``SUBSCRIPTION_SLOT`` покрытием.
    """
    return [
        row.user
        for row in tournament.registration_coverages.filter(
            coverage_type=TournamentRegistrationCoverage.CoverageType.SUBSCRIPTION_SLOT,
        ).select_related("user")
    ]


def cancel_tournament(
    tournament: Tournament,
    *,
    notify_message: str | None = None,
) -> bool:
    """
    Отменить турнир: установить статус «Отменён», вернуть FT тем, у кого
    участие было покрыто подпиской, снять эти покрытия и отправить уведомления.

    Args:
        tournament (Tournament): Турнир для отмены.
        notify_message (str | None): Текст уведомления участникам.

    Returns:
        bool: ``True`` при успехе.
    """
    if tournament.status == TournamentStatus.CANCELLED:
        logger.info("Tournament %s already cancelled", tournament.slug)
        return True

    covered_users = _users_with_subscription_coverage(tournament)

    tournament.status = TournamentStatus.CANCELLED
    tournament.save(update_fields=["status", "updated_at"])

    url = None
    try:
        from django.urls import reverse

        url = reverse("tournament_detail", args=[tournament.slug])
    except Exception:
        pass

    message = notify_message or (
        f"Турнир «{tournament.name}» отменён из-за недостаточного количества участников. "
        f"Баланс FT восстановлен (+{TOURNAMENT_REGISTRATION_COST})."
    )

    # Возвращаем FT только тем, у кого реально было покрытие подпиской.
    for user in covered_users:
        _refund_subscription_coverage(user, tournament)
        send_tournament_cancelled_email(
            user,
            tournament,
            refunded_ft=TOURNAMENT_REGISTRATION_COST,
            reason=message,
        )
        send_to_user_by_user(user, message)
        Notification.objects.create(
            user=user,
            message=message,
            url=url or "",
        )

    # Уведомить остальных участников без возврата FT (оплата ₽ / админ / без покрытия).
    covered_ids = {u.pk for u in covered_users}
    other_message = notify_message or (
        f"Турнир «{tournament.name}» отменён из-за недостаточного количества участников."
    )
    if tournament.is_doubles():
        users_done: set[int] = set(covered_ids)
        for team in tournament.teams.select_related("player1__user", "player2__user"):
            for player in (team.player1, team.player2):
                if player is None:
                    continue
                u = getattr(player, "user", None)
                if u and u.pk not in users_done:
                    users_done.add(u.pk)
                    send_tournament_cancelled_email(
                        u,
                        tournament,
                        refunded_ft=0,
                        reason=other_message,
                    )
                    send_to_user_by_user(u, other_message)
                    Notification.objects.create(
                        user=u,
                        message=other_message,
                        url=url or "",
                    )
    else:
        for player in tournament.participants.select_related("user").only("user_id"):
            user = getattr(player, "user", None)
            if user and user.pk not in covered_ids:
                send_tournament_cancelled_email(
                    user,
                    tournament,
                    refunded_ft=0,
                    reason=other_message,
                )
                send_to_user_by_user(user, other_message)
                Notification.objects.create(
                    user=user,
                    message=other_message,
                    url=url or "",
                )

    # Покрытия FT больше недействительны — FT уже на балансе.
    deleted, _ = tournament.registration_coverages.filter(
        coverage_type=TournamentRegistrationCoverage.CoverageType.SUBSCRIPTION_SLOT,
    ).delete()

    logger.info(
        "Cancelled tournament %s (slug=%s), refunded FT to %s users, "
        "removed %s subscription coverages",
        tournament.name,
        tournament.slug,
        len(covered_users),
        deleted,
    )
    return True


def restore_tournament_after_cancellation(tournament: Tournament) -> int:
    """Согласовать регистрации после возврата статуса с «Отменён».

    Сбрасывает устаревшие покрытия FT (если остались после старой отмены),
    заново пытается списать FT у участников с достаточным балансом.

    Args:
        tournament (Tournament): Турнир со статусом не «Отменён».

    Returns:
        int: Число участников, у которых заново списаны FT.
    """
    from .postpayment import (
        sync_postpayment_invoices_deadline,
        try_settle_pending_users_with_fancoin,
    )

    stale_deleted, _ = tournament.registration_coverages.filter(
        coverage_type=TournamentRegistrationCoverage.CoverageType.SUBSCRIPTION_SLOT,
    ).delete()
    if stale_deleted:
        logger.info(
            "Restore tournament %s: removed %s stale FT coverages",
            tournament.slug,
            stale_deleted,
        )
    if tournament.postpayment_window_started_at and tournament.allow_postpayment:
        sync_postpayment_invoices_deadline(tournament)
    settled = try_settle_pending_users_with_fancoin(tournament)
    logger.info(
        "Restore tournament %s: re-settled FT for %s participants",
        tournament.slug,
        settled,
    )
    return settled
