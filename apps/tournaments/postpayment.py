"""Сервис постоплаты для многодневных турниров."""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone

from apps.core.telegram_notify import notify_tournament_postpayment_window_opened
from apps.telegram_bot.notifications import send_to_user_by_user
from apps.users.models import Notification

from .cancel import cancel_tournament
from .models import (
    Tournament,
    TournamentPostpaymentInvoice,
    TournamentRegistrationCoverage,
)

logger = logging.getLogger(__name__)


def tournament_allows_postpayment_registration(tournament: Tournament) -> bool:
    """Проверить, можно ли регистрироваться с постоплатой.

    Args:
        tournament (Tournament): Турнир для проверки.

    Returns:
        bool: ``True``, если постоплата разрешена и окно ещё не запущено.
    """
    return bool(
        tournament.allow_postpayment
        and not tournament.is_one_day
        and not tournament.bracket_generated
        and tournament.postpayment_window_started_at is None
    )


def get_pending_postpayment_users(tournament: Tournament) -> list:
    """Вернуть пользователей, которым нужно оплатить взнос.

    Args:
        tournament (Tournament): Турнир для анализа.

    Returns:
        list: Список пользователей без оплаты и без покрытия лимитом.
    """
    paid_user_ids = set(
        tournament.entry_payments.values_list("user_id", flat=True).distinct()
    )
    paid_user_ids |= set(
        tournament.postpayment_invoices.filter(
            status=TournamentPostpaymentInvoice.Status.PAID
        ).values_list("user_id", flat=True)
    )
    covered_user_ids = set(
        tournament.registration_coverages.values_list("user_id", flat=True).distinct()
    )
    required_exclude_ids = paid_user_ids | covered_user_ids
    user_ids: set[int] = set(
        int(uid)
        for uid in tournament.participants.exclude(user_id__in=required_exclude_ids)
        .values_list("user_id", flat=True)
        .distinct()
    )
    if tournament.is_doubles():
        team_user_ids = (
            tournament.teams.filter(player2__isnull=False)
            .values_list("player1__user_id", "player2__user_id")
            .distinct()
        )
        for user_id_1, user_id_2 in team_user_ids:
            if user_id_1 not in required_exclude_ids:
                user_ids.add(int(user_id_1))
            if user_id_2 not in required_exclude_ids:
                user_ids.add(int(user_id_2))
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    return list(user_model.objects.filter(id__in=user_ids))


def mark_registration_covered(
    tournament: Tournament,
    user,
    coverage_type: TournamentRegistrationCoverage.CoverageType,
) -> None:
    """Отметить, что регистрация игрока покрыта лимитом/админом.

    Args:
        tournament (Tournament): Турнир.
        user: Пользователь игрока.
        coverage_type (TournamentRegistrationCoverage.CoverageType): Тип покрытия.

    Returns:
        None: Функция сохраняет отметку в базе.
    """
    TournamentRegistrationCoverage.objects.get_or_create(
        tournament=tournament,
        user=user,
        defaults={"coverage_type": coverage_type},
    )


def _payment_url(tournament: Tournament, invoice_id: int) -> str:
    return f"{reverse('payment_preview')}?type=tournament&id={tournament.id}&invoice={invoice_id}"


def _send_postpayment_opened_notification(
    tournament: Tournament,
    invoice: TournamentPostpaymentInvoice,
) -> None:
    """Отправить пользователю уведомление о старте постоплаты.

    Args:
        tournament (Tournament): Турнир.
        invoice (TournamentPostpaymentInvoice): Инвойс игрока.

    Returns:
        None: Функция отправляет сообщения в каналы уведомлений.
    """
    payment_url = _payment_url(tournament, invoice.id)
    text = (
        f"🎾 <b>Нужно оплатить участие в турнире</b>\n\n"
        f"Турнир: «{tournament.name}»\n"
        f"Сумма: {invoice.amount} ₽\n"
        f"Оплатить до: {timezone.localtime(invoice.due_at).strftime('%d.%m.%Y %H:%M')}\n\n"
        f"Ссылка на оплату: {payment_url}"
    )
    send_to_user_by_user(invoice.user, text)
    Notification.objects.create(
        user=invoice.user,
        message=f"Оплатите участие в турнире «{tournament.name}» до {timezone.localtime(invoice.due_at).strftime('%d.%m.%Y %H:%M')}.",
        url=payment_url,
    )


@transaction.atomic
def open_postpayment_window(tournament: Tournament) -> int:
    """Запустить окно постоплаты и создать инвойсы.

    Args:
        tournament (Tournament): Турнир, для которого запускается постоплата.

    Returns:
        int: Количество созданных инвойсов.
    """
    tournament = Tournament.objects.select_for_update().get(pk=tournament.pk)
    if tournament.postpayment_window_started_at is not None:
        return 0
    pending_users = get_pending_postpayment_users(tournament)
    if not pending_users:
        return 0
    now = timezone.now()
    due_at = now + timedelta(hours=int(tournament.postpayment_deadline_hours or 12))
    created_count = 0
    for user in pending_users:
        invoice, created = TournamentPostpaymentInvoice.objects.get_or_create(
            tournament=tournament,
            user=user,
            defaults={
                "amount": Decimal(tournament.entry_fee or 0),
                "due_at": due_at,
                "status": TournamentPostpaymentInvoice.Status.PENDING,
            },
        )
        if created:
            created_count += 1
        _send_postpayment_opened_notification(tournament, invoice)
    tournament.postpayment_window_started_at = now
    tournament.save(update_fields=["postpayment_window_started_at", "updated_at"])
    notify_tournament_postpayment_window_opened(tournament, created_count)
    return created_count


def send_1h_reminders() -> int:
    """Отправить напоминания за 1 час до окончания постоплаты.

    Args:
        None: Параметры не требуются.

    Returns:
        int: Количество отправленных напоминаний.
    """
    now = timezone.now()
    one_hour_later = now + timedelta(hours=1)
    invoices = TournamentPostpaymentInvoice.objects.select_related(
        "tournament", "user"
    ).filter(
        status=TournamentPostpaymentInvoice.Status.PENDING,
        reminder_1h_sent_at__isnull=True,
        due_at__lte=one_hour_later,
        due_at__gte=now,
    )
    total = 0
    for invoice in invoices:
        payment_url = _payment_url(invoice.tournament, invoice.id)
        text = (
            f"⏰ <b>Напоминание об оплате</b>\n\n"
            f"До конца срока оплаты турнира «{invoice.tournament.name}» остался 1 час.\n"
            f"Ссылка: {payment_url}"
        )
        send_to_user_by_user(invoice.user, text)
        invoice.reminder_1h_sent_at = now
        invoice.save(update_fields=["reminder_1h_sent_at"])
        total += 1
    return total


def _remove_unpaid_players(tournament: Tournament) -> int:
    """Удалить неоплативших игроков из турнира.

    Args:
        tournament (Tournament): Турнир.

    Returns:
        int: Количество удалённых пользователей.
    """
    now = timezone.now()
    expired = TournamentPostpaymentInvoice.objects.filter(
        tournament=tournament,
        status=TournamentPostpaymentInvoice.Status.PENDING,
        due_at__lte=now,
    )
    removed_user_ids = list(expired.values_list("user_id", flat=True))
    expired.update(status=TournamentPostpaymentInvoice.Status.EXPIRED)
    if not removed_user_ids:
        return 0
    tournament.participants.remove(
        *tournament.participants.filter(user_id__in=removed_user_ids)
    )
    if tournament.is_doubles():
        tournament.teams.filter(
            Q(player1__user_id__in=removed_user_ids)
            | Q(player2__user_id__in=removed_user_ids)
        ).delete()
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    for user in user_model.objects.filter(id__in=removed_user_ids):
        send_to_user_by_user(
            user,
            f"⚠️ Вы удалены из турнира «{tournament.name}» из-за неоплаты вступительного взноса в установленный срок.",
        )
    return len(removed_user_ids)


def _generate_after_postpayment(tournament: Tournament) -> tuple[bool, str]:
    from .views import _tournament_manual_generate

    ok, msg, _ = _tournament_manual_generate(tournament)
    return ok, msg


@transaction.atomic
def finalize_postpayment_window(tournament: Tournament) -> tuple[bool, str]:
    """Завершить окно постоплаты и сформировать сетку.

    Args:
        tournament (Tournament): Турнир.

    Returns:
        tuple[bool, str]: Результат выполнения (успех, сообщение).
    """
    tournament = Tournament.objects.select_for_update().get(pk=tournament.pk)
    if tournament.bracket_generated:
        return True, "Сетка уже сформирована."
    _remove_unpaid_players(tournament)
    if tournament.is_doubles():
        current_count = tournament.full_teams_count()
        min_required = tournament.min_teams
    else:
        current_count = tournament.participants.count()
        min_required = tournament.min_participants
    if min_required and current_count < min_required:
        cancel_tournament(tournament)
        return False, "Турнир отменён: после постоплаты недостаточно участников."
    ok, msg = _generate_after_postpayment(tournament)
    if ok:
        TournamentPostpaymentInvoice.objects.filter(
            tournament=tournament,
            status=TournamentPostpaymentInvoice.Status.PENDING,
        ).update(status=TournamentPostpaymentInvoice.Status.CANCELLED)
    return ok, msg


def get_postpayment_progress(tournament: Tournament) -> dict[str, int | bool]:
    """Вернуть прогресс оплаты по открытому окну постоплаты.

    Args:
        tournament (Tournament): Турнир.

    Returns:
        dict[str, int | bool]: Агрегированные показатели по оплатам.
    """
    stats = tournament.postpayment_invoices.aggregate(
        total=Count("id"),
        paid=Count("id", filter=Q(status=TournamentPostpaymentInvoice.Status.PAID)),
        pending=Count(
            "id", filter=Q(status=TournamentPostpaymentInvoice.Status.PENDING)
        ),
    )
    total = int(stats["total"] or 0)
    paid = int(stats["paid"] or 0)
    pending = int(stats["pending"] or 0)
    return {
        "total": total,
        "paid": paid,
        "pending": pending,
        "completed": total > 0 and pending == 0,
    }
