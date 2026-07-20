"""
Отмена турнира: статус «Отменён», возврат лимитов регистраций участникам.
"""

import logging

from apps.core.email_service import send_tournament_cancelled_email
from apps.subscriptions.fancoin import TOURNAMENT_REGISTRATION_COST
from apps.subscriptions.models import FancoinTransaction
from apps.telegram_bot.notifications import send_to_user_by_user
from apps.users.models import Notification

from .models import (
    Tournament,
    TournamentPostpaymentInvoice,
    TournamentRegistrationCoverage,
    TournamentStatus,
)

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


def _already_compensated_ft_on_cancel(user, tournament: Tournament) -> bool:
    """Проверить, что за отмену этого турнира FT уже начислялись.

    Args:
        user: Пользователь.
        tournament (Tournament): Турнир.

    Returns:
        bool: ``True``, если возврат/компенсация уже есть в журнале.
    """
    return bool(
        FancoinTransaction.objects.filter(
            user=user,
            tournament=tournament,
            reason=FancoinTransaction.Reason.TOURNAMENT_CANCEL,
            direction=FancoinTransaction.Direction.REFUND,
        ).exists()
    )


def _compensate_paid_postpayment_with_ft(user, tournament: Tournament) -> bool:
    """Начислить FT вместо возврата ₽ за оплаченную постоплату при отмене.

    Args:
        user: Пользователь с оплаченным инвойсом постоплаты.
        tournament (Tournament): Отменяемый турнир.

    Returns:
        bool: ``True``, если FT начислены.
    """
    if _already_compensated_ft_on_cancel(user, tournament):
        return False
    try:
        sub = getattr(user, "subscription", None)
        if not sub:
            logger.warning(
                "No subscription to compensate paid postpayment: user=%s tournament=%s",
                getattr(user, "pk", None),
                tournament.slug,
            )
            return False
        sub.refund_fancoin(
            TOURNAMENT_REGISTRATION_COST,
            reason=FancoinTransaction.Reason.TOURNAMENT_CANCEL,
            tournament=tournament,
        )
        return True
    except Exception as e:
        logger.warning(
            "Could not compensate paid postpayment with FT for user %s: %s",
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


def _users_with_paid_postpayment(tournament: Tournament) -> list:
    """Пользователи с оплаченной постоплатой в ₽.

    Args:
        tournament (Tournament): Турнир.

    Returns:
        list: Пользователи с инвойсом в статусе ``PAID``.
    """
    return [
        invoice.user
        for invoice in tournament.postpayment_invoices.filter(
            status=TournamentPostpaymentInvoice.Status.PAID,
        ).select_related("user")
    ]


def _notify_cancelled(
    user,
    tournament: Tournament,
    *,
    message: str,
    refunded_ft: int,
    url: str | None,
) -> None:
    """Отправить уведомления об отмене турнира одному пользователю.

    Args:
        user: Получатель.
        tournament (Tournament): Турнир.
        message (str): Текст уведомления.
        refunded_ft (int): Сколько FT начислено/возвращено.
        url (str | None): Ссылка на турнир.
    """
    send_tournament_cancelled_email(
        user,
        tournament,
        refunded_ft=refunded_ft,
        reason=message,
    )
    send_to_user_by_user(user, message, skip_email=True)
    Notification.objects.create(
        user=user,
        message=message,
        url=url or "",
    )


def cancel_tournament(
    tournament: Tournament,
    *,
    notify_message: str | None = None,
) -> bool:
    """
    Отменить турнир: установить статус «Отменён», вернуть FT тем, у кого
    участие было покрыто подпиской, компенсировать оплаченную постоплату
    начислением FT, снять покрытия и отправить уведомления.

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
    paid_postpayment_users = _users_with_paid_postpayment(tournament)

    tournament.status = TournamentStatus.CANCELLED
    tournament.save(update_fields=["status", "updated_at"])

    url = None
    try:
        from django.urls import reverse

        url = reverse("tournament_detail", args=[tournament.slug])
    except Exception:
        pass

    ft_message = notify_message or (
        f"Турнир «{tournament.name}» отменён из-за недостаточного количества участников. "
        f"Баланс FT восстановлен (+{TOURNAMENT_REGISTRATION_COST})."
    )
    paid_message = notify_message or (
        f"Турнир «{tournament.name}» отменён из-за недостаточного количества участников. "
        f"Вместо возврата оплаты начислено +{TOURNAMENT_REGISTRATION_COST} FT."
    )
    other_message = notify_message or (
        f"Турнир «{tournament.name}» отменён из-за недостаточного количества участников."
    )

    refunded_user_ids: set[int] = set()

    # Возвращаем FT тем, у кого реально было покрытие подпиской.
    for user in covered_users:
        if _refund_subscription_coverage(user, tournament):
            refunded_user_ids.add(user.pk)
        _notify_cancelled(
            user,
            tournament,
            message=ft_message,
            refunded_ft=TOURNAMENT_REGISTRATION_COST,
            url=url,
        )

    # Оплатившим постоплату в ₽ — начисляем FT вместо возврата денег.
    compensated = 0
    for user in paid_postpayment_users:
        if user.pk in refunded_user_ids:
            continue
        compensated_now = _compensate_paid_postpayment_with_ft(user, tournament)
        if compensated_now or _already_compensated_ft_on_cancel(user, tournament):
            # Уже компенсировали ранее (повторная отмена) — не шлём «без возврата».
            refunded_user_ids.add(user.pk)
        if compensated_now:
            compensated += 1
            _notify_cancelled(
                user,
                tournament,
                message=paid_message,
                refunded_ft=TOURNAMENT_REGISTRATION_COST,
                url=url,
            )

    # Уведомить остальных участников без возврата FT.
    if tournament.is_doubles():
        users_done: set[int] = set(refunded_user_ids)
        for team in tournament.teams.select_related("player1__user", "player2__user"):
            for player in (team.player1, team.player2):
                if player is None:
                    continue
                u = getattr(player, "user", None)
                if u and u.pk not in users_done:
                    users_done.add(u.pk)
                    _notify_cancelled(
                        u,
                        tournament,
                        message=other_message,
                        refunded_ft=0,
                        url=url,
                    )
    else:
        for player in tournament.participants.select_related("user").only("user_id"):
            user = getattr(player, "user", None)
            if user and user.pk not in refunded_user_ids:
                _notify_cancelled(
                    user,
                    tournament,
                    message=other_message,
                    refunded_ft=0,
                    url=url,
                )

    # Покрытия FT больше недействительны — FT уже на балансе.
    deleted, _ = tournament.registration_coverages.filter(
        coverage_type=TournamentRegistrationCoverage.CoverageType.SUBSCRIPTION_SLOT,
    ).delete()

    logger.info(
        "Cancelled tournament %s (slug=%s), refunded FT to %s coverage users, "
        "compensated %s paid postpayment users, removed %s subscription coverages",
        tournament.name,
        tournament.slug,
        len(covered_users),
        compensated,
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
