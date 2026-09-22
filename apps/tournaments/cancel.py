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


class TournamentLifecycleError(ValueError):
    """Ошибка продления регистрации или возобновления набора."""


def _aware_deadline(value):
    """Привести дедлайн к aware datetime в текущей TZ.

    Args:
        value: datetime (naive или aware).

    Returns:
        datetime: Aware datetime.
    """
    from django.utils import timezone

    if timezone.is_naive(value):
        return timezone.make_aware(value, timezone.get_current_timezone())
    return value


def _validate_registration_window(
    *,
    registration_deadline,
    start_date,
) -> tuple:
    """Проверить окно регистрации и вернуть нормализованные значения.

    Args:
        registration_deadline: Новый дедлайн регистрации.
        start_date: Дата начала турнира.

    Returns:
        tuple: ``(deadline, start_date)``.

    Raises:
        TournamentLifecycleError: Если даты некорректны.
    """
    from django.utils import timezone

    deadline = _aware_deadline(registration_deadline)
    now = timezone.now()
    if deadline <= now:
        raise TournamentLifecycleError("Дедлайн регистрации должен быть в будущем.")
    if start_date is None:
        raise TournamentLifecycleError("Укажите дату начала турнира.")
    deadline_date = timezone.localtime(deadline).date()
    if deadline_date > start_date:
        raise TournamentLifecycleError(
            "Дедлайн регистрации не может быть позже даты начала турнира."
        )
    return deadline, start_date


def _sync_end_date(tournament: Tournament, start_date) -> None:
    """Подтянуть end_date, если старт уехал вперёд.

    Args:
        tournament (Tournament): Турнир.
        start_date: Новая дата начала.
    """
    if tournament.end_date is not None and tournament.end_date < start_date:
        tournament.end_date = start_date
    if tournament.is_one_day:
        tournament.end_date = start_date


def extend_registration(
    tournament: Tournament,
    *,
    registration_deadline,
    start_date=None,
) -> Tournament:
    """Продлить дедлайн регистрации (и при необходимости сдвинуть старт).

    Сбрасывает ``insufficient_participants_notified_at`` через ``Tournament.save()``,
    если новый дедлайн в будущем.

    Args:
        tournament (Tournament): Турнир без сформированной сетки.
        registration_deadline: Новый дедлайн регистрации.
        start_date: Новая дата начала (опционально).

    Returns:
        Tournament: Обновлённый турнир.

    Raises:
        TournamentLifecycleError: При неверном статусе или датах.
    """
    if tournament.bracket_generated:
        raise TournamentLifecycleError(
            "Нельзя продлить регистрацию: сетка уже сформирована."
        )
    if tournament.status == TournamentStatus.CANCELLED:
        raise TournamentLifecycleError("Отменённый турнир нужно сначала возобновить.")
    if tournament.status == TournamentStatus.COMPLETED:
        raise TournamentLifecycleError("Завершённый турнир нельзя изменить.")

    new_start = start_date if start_date is not None else tournament.start_date
    deadline, new_start = _validate_registration_window(
        registration_deadline=registration_deadline,
        start_date=new_start,
    )
    tournament.registration_deadline = deadline
    tournament.start_date = new_start
    _sync_end_date(tournament, new_start)
    tournament.save()
    logger.info(
        "Extended registration for %s until %s (start=%s)",
        tournament.slug,
        deadline,
        new_start,
    )
    return tournament


def reopen_tournament(
    tournament: Tournament,
    *,
    start_date,
    registration_deadline,
    notify: bool = True,
) -> Tournament:
    """Возобновить набор после отмены (без сетки).

    Ставит статус ``upcoming``, обновляет даты; ``Tournament.save()`` вызывает
    ``restore_tournament_after_cancellation`` для повторного списания FT.

    Args:
        tournament (Tournament): Отменённый турнир без сетки.
        start_date: Новая дата начала.
        registration_deadline: Новый дедлайн регистрации.
        notify (bool): Уведомить участников о повторном открытии набора.

    Returns:
        Tournament: Возобновлённый турнир.

    Raises:
        TournamentLifecycleError: При неверном статусе или датах.
    """
    if tournament.status != TournamentStatus.CANCELLED:
        raise TournamentLifecycleError("Возобновить можно только отменённый турнир.")
    if tournament.bracket_generated:
        raise TournamentLifecycleError(
            "Нельзя возобновить турнир со сформированной сеткой."
        )

    deadline, new_start = _validate_registration_window(
        registration_deadline=registration_deadline,
        start_date=start_date,
    )
    tournament.status = TournamentStatus.UPCOMING
    tournament.start_date = new_start
    tournament.registration_deadline = deadline
    tournament.insufficient_participants_notified_at = None
    _sync_end_date(tournament, new_start)
    tournament.save()

    if notify:
        _notify_tournament_reopened(tournament)

    logger.info(
        "Reopened tournament %s (start=%s, deadline=%s)",
        tournament.slug,
        new_start,
        deadline,
    )
    return tournament


def _notify_tournament_reopened(tournament: Tournament) -> None:
    """Уведомить участников, что набор снова открыт.

    Args:
        tournament (Tournament): Возобновлённый турнир.
    """
    from django.urls import reverse

    url = None
    try:
        url = reverse("tournament_detail", args=[tournament.slug])
    except Exception:
        pass

    message = (
        f"Набор на турнир «{tournament.name}» снова открыт. "
        "Вы можете остаться в списке участников или снять заявку на странице турнира."
    )
    users: list = []
    if tournament.is_doubles():
        seen: set[int] = set()
        for team in tournament.teams.select_related("player1__user", "player2__user"):
            for player in (team.player1, team.player2):
                if player is None:
                    continue
                u = getattr(player, "user", None)
                if u and u.pk not in seen:
                    seen.add(u.pk)
                    users.append(u)
    else:
        for player in tournament.participants.select_related("user").only("user_id"):
            u = getattr(player, "user", None)
            if u:
                users.append(u)

    for user in users:
        try:
            Notification.objects.create(
                user=user,
                title="Набор на турнир возобновлён",
                message=message,
                url=url or "",
            )
        except Exception as exc:
            logger.warning(
                "Failed in-app reopen notify for user %s: %s",
                getattr(user, "pk", None),
                exc,
            )
        try:
            send_to_user_by_user(user, message)
        except Exception as exc:
            logger.warning(
                "Failed telegram reopen notify for user %s: %s",
                getattr(user, "pk", None),
                exc,
            )
