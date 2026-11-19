"""Регистрация с постоплатой, ограниченное окно оплаты и формирование сетки."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Literal, cast

from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q, QuerySet
from django.urls import reverse
from django.utils import timezone

from apps.core.telegram_notify import notify_tournament_postpayment_window_opened
from apps.notifications.services import create_notification
from apps.subscriptions.fancoin import TOURNAMENT_REGISTRATION_COST
from apps.subscriptions.models import FancoinTransaction
from apps.telegram_bot.notifications import send_to_user_by_user

from .cancel import cancel_tournament
from .entry_payment import (
    active_onsite_payments,
    get_paid_entry_user_ids,
    get_tournament_entry_amount,
    has_onsite_payment,
    has_received_entry_payment,
)
from .models import (
    Tournament,
    TournamentDuration,
    TournamentOnsitePayment,
    TournamentPostpaymentCallLog,
    TournamentPostpaymentInvoice,
    TournamentRegistrationCoverage,
    TournamentStatus,
)

if TYPE_CHECKING:
    from apps.users.models import User

PaymentStatusTone = Literal["success", "warning", "danger", "neutral"]

logger = logging.getLogger(__name__)


class PostpaymentWindowError(ValueError):
    """Недопустимый запуск окна оплаты без действующего календаря или набора."""


_SUBSCRIPTION_SLOT_COVERAGE = cast(
    TournamentRegistrationCoverage.CoverageType,
    TournamentRegistrationCoverage.CoverageType.SUBSCRIPTION_SLOT,
)
_ADMIN_GRANTED_COVERAGE = cast(
    TournamentRegistrationCoverage.CoverageType,
    TournamentRegistrationCoverage.CoverageType.ADMIN_GRANTED,
)

# Статусы, для которых админ может вручную подтвердить участие.
ADMIN_CONFIRMABLE_PAYMENT_STATUSES: frozenset[str] = frozenset(
    {
        "Ожидает оплату (₽)",
        "Не оплачен",
        "Постоплата (окно не открыто)",
        "Не оплатил (срок истёк)",
        "Инвойс отменён",
        "Без отметки оплаты",
        "Взнос возвращён (₽)",
    }
)

# Статусы, для которых можно повторно отправить ссылку на оплату.
ADMIN_RESENDABLE_PAYMENT_STATUSES: frozenset[str] = frozenset(
    {
        "Ожидает оплату (₽)",
    }
)


@dataclass(frozen=True)
class ParticipantPaymentStatus:
    """Строка статуса оплаты участника для админки и отчётов.

    Args:
        user_id (int): ID пользователя.
        player_id (int | None): ID профиля игрока.
        display_name (str): Имя для отображения.
        status (str): Краткий статус.
        details (str): Подробности.
        status_tone (PaymentStatusTone): Цветовая категория для админки.
        phone (str): Телефон участника для ``tel:``-ссылки.
        called_at (datetime | None): Время последнего отмеченного звонка.
        link_resent_at (datetime | None): Время последней повторной отправки ссылки.
    """

    user_id: int
    player_id: int | None
    display_name: str
    status: str
    details: str
    status_tone: PaymentStatusTone = "neutral"
    phone: str = ""
    called_at: datetime | None = None
    link_resent_at: datetime | None = None
    pending_invoice_id: int | None = None
    expected_onsite_id: int | None = None
    onsite_payment_id: int | None = None


def phone_to_tel_href(phone: str) -> str:
    """Преобразовать телефон в значение ``href`` для ссылки ``tel:``.

    Args:
        phone (str): Телефон в произвольном формате.

    Returns:
        str: Строка вида ``tel:+79001234567`` или пустая, если номер невалиден.
    """
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    if len(digits) != 11:
        return ""
    return f"tel:+{digits}"


def mark_postpayment_call(
    tournament: Tournament,
    user,
    *,
    called_by=None,
) -> TournamentPostpaymentCallLog:
    """Зафиксировать звонок администратора участнику по постоплате.

    Args:
        tournament (Tournament): Турнир.
        user: Пользователь участника.
        called_by: Администратор, который звонил (опционально).

    Returns:
        TournamentPostpaymentCallLog: Созданная или обновлённая отметка.
    """
    now = timezone.now()
    log, _created = TournamentPostpaymentCallLog.objects.update_or_create(
        tournament=tournament,
        user=user,
        defaults={
            "called_at": now,
            "called_by": called_by,
        },
    )
    return cast(TournamentPostpaymentCallLog, log)


def participant_payment_status_tone(status: str) -> PaymentStatusTone:
    """Определить цветовую категорию статуса оплаты для админки.

    Args:
        status (str): Текст статуса из ``build_participant_payment_statuses``.

    Returns:
        PaymentStatusTone: ``success`` — оплачено, ``danger`` — требует действия,
        ``warning`` — ожидание, ``neutral`` — прочее.
    """
    success_statuses = {
        "Покрыто FT (подписка)",
        "Клубный тариф",
        "Выдано администратором",
        "Оплачен взнос (₽)",
        "Оплачено постоплатой (₽)",
        "Оплачено на корте (₽)",
    }
    danger_statuses = {
        "Ожидает оплату (₽)",
        "Не оплачен",
        "Не оплатил (срок истёк)",
    }
    warning_statuses = {
        "Постоплата (окно не открыто)",
        "Инвойс отменён",
        "Без отметки оплаты",
        "Ожидается оплата на корте (₽)",
        "Взнос возвращён (₽)",
    }
    if status in success_statuses:
        return "success"
    if status in danger_statuses:
        return "danger"
    if status in warning_statuses:
        return "warning"
    return "neutral"


def tournament_allows_postpayment_registration(tournament: Tournament) -> bool:
    """Проверить, можно ли регистрироваться с постоплатой.

    Args:
        tournament (Tournament): Турнир для проверки.

    Returns:
        bool: ``True``, если постоплата разрешена и окно ещё не запущено.
    """
    return bool(
        tournament.allow_postpayment
        and not tournament.bracket_generated
        and tournament.postpayment_window_started_at is None
    )


def get_pending_postpayment_users(tournament: Tournament) -> list[User]:
    """Вернуть пользователей, которым нужно оплатить взнос.

    Args:
        tournament (Tournament): Турнир для анализа.

    Returns:
        list: Список пользователей без оплаты и без покрытия лимитом.
    """
    paid_user_ids = get_paid_entry_user_ids(tournament)
    covered_user_ids = set(
        tournament.registration_coverages.values_list("user_id", flat=True).distinct()
    )
    onsite_user_ids = set(
        active_onsite_payments(tournament).values_list("user_id", flat=True)
    )
    required_exclude_ids = paid_user_ids | covered_user_ids | onsite_user_ids
    return [
        user
        for user in _collect_tournament_participant_users(tournament)
        if user.pk not in required_exclude_ids
    ]


def get_pending_postpayment_invoices(
    tournament: Tournament | None = None,
) -> QuerySet[TournamentPostpaymentInvoice]:
    """Вернуть действующие онлайн-счета, исключив выбранную оплату на площадке.

    Сопоставление делается по турниру и пользователю одновременно: выбор оплаты
    на корте в одном турнире не отменяет долг того же игрока в другом.
    Исторические строки счетов не меняются и не удаляются.
    """
    onsite = TournamentOnsitePayment.objects.filter(
        tournament_id=OuterRef("tournament_id"),
        user_id=OuterRef("user_id"),
        status__in=[
            TournamentOnsitePayment.Status.EXPECTED,
            TournamentOnsitePayment.Status.RECEIVED,
        ],
    )
    invoices = TournamentPostpaymentInvoice.objects.filter(
        status=TournamentPostpaymentInvoice.Status.PENDING,
    ).filter(~Exists(onsite))
    if tournament is not None:
        invoices = invoices.filter(tournament=tournament)
    return cast(QuerySet[TournamentPostpaymentInvoice], invoices)


def _can_pay_entry_with_fancoin(tournament: Tournament) -> bool:
    """Проверить, можно ли покрыть взнос глобальными FT (как при регистрации).

    Args:
        tournament (Tournament): Турнир для проверки.

    Returns:
        bool: ``True``, если для турнира допустимо списание FT с подписки.
    """
    fee = Decimal(tournament.entry_fee or 0)
    has_entry_fee = fee > 0
    postpayment_enabled = bool(
        tournament.allow_postpayment and not tournament.is_one_day
    )
    return not (
        tournament.club_id
        or tournament.is_one_day
        or (has_entry_fee and not postpayment_enabled)
    )


def _user_already_settled_for_tournament(tournament: Tournament, user: User) -> bool:
    """Проверить, закрыта ли оплата участия пользователя без постоплаты.

    Args:
        tournament (Tournament): Турнир.
        user: Пользователь.

    Returns:
        bool: ``True``, если оплата или покрытие уже зафиксированы.
    """
    if tournament.registration_coverages.filter(user=user).exists():
        return True
    return has_received_entry_payment(tournament, user)


@transaction.atomic
def try_cover_registration_with_fancoin(tournament: Tournament, user: User) -> bool:
    """Списать FT и отметить покрытие регистрации, если хватает баланса.

    Args:
        tournament (Tournament): Турнир.
        user: Пользователь участника.

    Returns:
        bool: ``True``, если участие уже было или успешно покрыто FT.
    """
    tournament = Tournament.objects.select_for_update().get(pk=tournament.pk)
    if has_onsite_payment(tournament, user):
        return False
    if _user_already_settled_for_tournament(tournament, user):
        return True
    if not _can_pay_entry_with_fancoin(tournament):
        return False
    try:
        sub = user.subscription
    except Exception:
        return False
    if sub is None or not sub.has_fancoin(TOURNAMENT_REGISTRATION_COST):
        return False
    if not sub.spend_fancoin(
        TOURNAMENT_REGISTRATION_COST,
        reason=cast(
            FancoinTransaction.Reason,
            FancoinTransaction.Reason.TOURNAMENT_REGISTRATION,
        ),
        tournament=tournament,
    ):
        return False
    mark_registration_covered(
        tournament,
        user,
        _SUBSCRIPTION_SLOT_COVERAGE,
    )
    logger.info(
        "Postpayment: участие в турнире %s покрыто FT для user_id=%s",
        tournament.slug,
        user.pk,
    )
    return True


def _cancel_pending_postpayment_invoice(tournament: Tournament, user) -> int:
    """Отменить ожидающий инвойс постоплаты после покрытия FT.

    Args:
        tournament (Tournament): Турнир.
        user: Пользователь.

    Returns:
        int: Количество отменённых инвойсов (0 или 1).
    """
    return int(
        TournamentPostpaymentInvoice.objects.filter(
            tournament=tournament,
            user=user,
            status=TournamentPostpaymentInvoice.Status.PENDING,
        ).update(status=TournamentPostpaymentInvoice.Status.CANCELLED)
    )


def _send_fancoin_settled_notification(
    tournament: Tournament,
    user,
    *,
    had_payment_request: bool,
) -> None:
    """Уведомить участника, что взнос покрыт FT и рубли платить не нужно.

    Args:
        tournament (Tournament): Турнир.
        user: Пользователь участника.
        had_payment_request (bool): Был ли ранее инвойс/запрос оплаты в рублях.

    Returns:
        None: Функция отправляет сообщения в каналы уведомлений.
    """
    try:
        sub = user.subscription
        balance = sub.get_fancoin_balance() if sub else 0
    except Exception:
        balance = 0
    tournament_url = reverse("tournament_detail", kwargs={"slug": tournament.slug})
    if had_payment_request:
        intro = (
            "Ранее мы просили оплатить вступительный взнос в рублях, "
            "но на вашем балансе достаточно FT — дополнительная оплата не нужна."
        )
    else:
        intro = "Вступительный взнос покрыт с баланса подписки."
    text = (
        f"✅ <b>Участие в турнире подтверждено</b>\n\n"
        f"Турнир: «{tournament.name}»\n"
        f"{intro}\n"
        f"Списано: {TOURNAMENT_REGISTRATION_COST} FT\n"
        f"Остаток FT: {balance}\n\n"
        f"Страница турнира: {tournament_url}"
    )
    send_to_user_by_user(
        user, text, skip_email=True, event_key="tournament.fancoin_paid"
    )
    create_notification(
        user=user,
        message=(
            f"Участие в «{tournament.name}» подтверждено: списано "
            f"{TOURNAMENT_REGISTRATION_COST} FT. Оплата в рублях не требуется."
        ),
        url=tournament_url,
        event_key="tournament.fancoin_paid",
    )
    try:
        from apps.core.email_service import (
            send_tournament_entry_fancoin_confirmed_email,
        )

        send_tournament_entry_fancoin_confirmed_email(
            user,
            tournament,
            fancoin_spent=TOURNAMENT_REGISTRATION_COST,
            fancoin_balance=balance,
            had_payment_request=had_payment_request,
        )
    except Exception:
        logger.exception(
            "Не удалось отправить email о подтверждении участия FT: user_id=%s, tournament=%s",
            user.pk,
            tournament.slug,
        )


def try_settle_pending_users_with_fancoin(
    tournament: Tournament,
    *,
    users: list[User] | None = None,
) -> int:
    """Попытаться покрыть FT участников без оплаты взноса.

    Args:
        tournament (Tournament): Турнир.
        users (list | None): Список пользователей; если ``None`` — все ожидающие.

    Returns:
        int: Число пользователей, для которых выполнено новое покрытие FT.
    """
    if users is None:
        users = get_pending_postpayment_users(tournament)
    settled = 0
    for user in users:
        if has_onsite_payment(tournament, user):
            continue
        already_settled = _user_already_settled_for_tournament(tournament, user)
        had_pending_invoice = (
            get_pending_postpayment_invoices(tournament).filter(user=user).exists()
        )
        if try_cover_registration_with_fancoin(tournament, user):
            _cancel_pending_postpayment_invoice(tournament, user)
            if (
                not already_settled
                and tournament.registration_coverages.filter(user=user).exists()
            ):
                _send_fancoin_settled_notification(
                    tournament,
                    user,
                    had_payment_request=had_pending_invoice,
                )
                settled += 1
    return settled


def tournament_has_generated_matches(tournament: Tournament) -> bool:
    """Проверить, есть ли у турнира созданные матчи сетки.

    Args:
        tournament (Tournament): Турнир.

    Returns:
        bool: ``True``, если в турнире есть хотя бы один матч.
    """
    return bool(tournament.matches.exists())


def tournament_needs_fancoin_settlement(tournament: Tournament) -> bool:
    """Проверить, есть ли участники для списания FT по постоплате.

    Args:
        tournament (Tournament): Турнир.

    Returns:
        bool: ``True``, если остались непокрытые участники или pending-инвойсы.
    """
    if not tournament.allow_postpayment:
        return False
    if get_pending_postpayment_users(tournament):
        return True
    return bool(get_pending_postpayment_invoices(tournament).exists())


def _users_for_fancoin_settlement(tournament: Tournament) -> list[User]:
    """Собрать пользователей, для которых нужно попытаться списать FT.

    Args:
        tournament (Tournament): Турнир.

    Returns:
        list: Уникальные пользователи с pending-инвойсом или без покрытия взноса.
    """
    pending_invoices = list(
        get_pending_postpayment_invoices(tournament).select_related("user")
    )
    user_by_id = {invoice.user.pk: invoice.user for invoice in pending_invoices}
    for user in get_pending_postpayment_users(tournament):
        user_by_id.setdefault(user.pk, user)
    return list(user_by_id.values())


def _collect_tournament_ids_for_fancoin_settlement() -> set[int]:
    """Собрать ID турниров, где нужно периодически проверять списание FT.

    Returns:
        set[int]: ID турниров с постоплатой и незакрытыми участниками.
    """
    tournament_ids: set[int] = set(
        get_pending_postpayment_invoices()
        .filter(
            tournament__allow_postpayment=True,
        )
        .values_list("tournament_id", flat=True)
    )
    tournament_ids |= set(
        Tournament.objects.filter(
            allow_postpayment=True,
            postpayment_window_started_at__isnull=False,
        ).values_list("pk", flat=True)
    )
    # До открытия окна: участник мог купить подписку после регистрации с постоплатой.
    tournament_ids |= set(
        Tournament.objects.filter(
            allow_postpayment=True,
            bracket_generated=False,
            postpayment_window_started_at__isnull=True,
        ).values_list("pk", flat=True)
    )
    return tournament_ids


def try_settle_postpayment_for_user(user: User) -> int:
    """Списать FT по постоплате для всех турниров пользователя при появлении баланса.

    Вызывается после пополнения FT (покупка подписки и т.п.), чтобы не ждать cron.

    Args:
        user: Пользователь, у которого мог появиться баланс FT.

    Returns:
        int: Число турниров, где выполнено новое покрытие FT.
    """
    tournament_ids: set[int] = set(
        get_pending_postpayment_invoices()
        .filter(
            user=user,
            tournament__allow_postpayment=True,
        )
        .values_list("tournament_id", flat=True)
    )
    tournament_ids |= set(
        Tournament.objects.filter(
            allow_postpayment=True,
            bracket_generated=False,
        )
        .filter(
            Q(participants__user=user)
            | Q(teams__player1__user=user)
            | Q(teams__player2__user=user)
        )
        .values_list("pk", flat=True)
    )
    settled = 0
    for tournament in Tournament.objects.filter(pk__in=tournament_ids):
        if not tournament_needs_fancoin_settlement(tournament):
            continue
        pending_users = get_pending_postpayment_users(tournament)
        if not any(u.pk == user.pk for u in pending_users):
            continue
        with transaction.atomic():
            settled += try_settle_pending_users_with_fancoin(
                tournament,
                users=[user],
            )
    return settled


def settle_postpayment_with_available_fancoin() -> int:
    """Проверить FT у участников с постоплатой (cron, каждые 10 минут).

    Args:
        None: Параметры не требуются.

    Returns:
        int: Число участников, для которых выполнено покрытие FT в этом запуске.
    """
    tournament_ids = _collect_tournament_ids_for_fancoin_settlement()
    total = 0
    for tournament in Tournament.objects.filter(pk__in=tournament_ids):
        users = _users_for_fancoin_settlement(tournament)
        if not users:
            continue
        with transaction.atomic():
            covered = try_settle_pending_users_with_fancoin(
                tournament,
                users=users,
            )
        total += covered
    return total


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


def admin_confirm_postpayment_participation(
    tournament: Tournament,
    user: User,
) -> bool:
    """Подтвердить участие администратором: покрытие + отмена инвойса.

    Args:
        tournament (Tournament): Турнир.
        user: Пользователь участника.

    Returns:
        bool: ``True``, если участие подтверждено (или уже было подтверждено).
    """
    if has_onsite_payment(tournament, user):
        return False
    if _user_already_settled_for_tournament(tournament, user):
        _cancel_pending_postpayment_invoice(tournament, user)
        return True
    with transaction.atomic():
        mark_registration_covered(
            tournament,
            user,
            _ADMIN_GRANTED_COVERAGE,
        )
        cancelled = _cancel_pending_postpayment_invoice(tournament, user)
    logger.info(
        "Postpayment: участие подтверждено администратором для user_id=%s, "
        "tournament=%s, cancelled_invoices=%s",
        user.pk,
        tournament.slug,
        cancelled,
    )
    return True


def _site_base_url() -> str:
    """Вернуть абсолютный базовый URL сайта для ссылок в уведомлениях.

    Returns:
        str: Базовый URL без завершающего слэша.
    """
    from django.conf import settings

    base = (getattr(settings, "TELEGRAM_BOT_SITE_BASE_URL", None) or "").strip()
    if not base:
        base = (getattr(settings, "SITE_URL", None) or "").strip()
    if not base:
        domain = getattr(settings, "SITE_DOMAIN", "tennisfan.ru")
        base = f"https://{domain}"
    return base.rstrip("/")


def _payment_url(tournament: Tournament, invoice_id: int) -> str:
    """Собрать абсолютную ссылку на оплату постоплаты.

    Args:
        tournament (Tournament): Турнир.
        invoice_id (int): ID инвойса постоплаты.

    Returns:
        str: Абсолютный URL страницы ``payment_preview``.
    """
    path = (
        f"{reverse('payment_preview')}"
        f"?type=tournament&id={tournament.id}&invoice={invoice_id}"
    )
    return f"{_site_base_url()}{path}"


@transaction.atomic
def sync_postpayment_invoices_deadline(tournament: Tournament) -> int:
    """Пересчитать сроки действующих счетов по сохранённым настройкам окна и игры.

    Суммы, оплаченные и отменённые счета остаются неизменными. Несохранённые
    поля переданного объекта не влияют на расчёт; закрытый набор не открывается.

    Args:
        tournament (Tournament): Турнир с открытым окном постоплаты.

    Returns:
        int: Число обновлённых инвойсов.
    """
    tournament = Tournament.objects.select_for_update().get(pk=tournament.pk)
    if (
        not tournament.allow_postpayment
        or tournament.bracket_generated
        or tournament.status in (TournamentStatus.CANCELLED, TournamentStatus.COMPLETED)
    ):
        return 0
    new_due_at = tournament.get_postpayment_window_ends_at()
    if new_due_at is None:
        return 0
    now = timezone.now()
    updated = int(
        get_pending_postpayment_invoices(tournament).update(due_at=new_due_at)
    )
    # Если окно снова в будущем — вернуть EXPIRED→PENDING участникам, ещё в турнире.
    if new_due_at > now:
        participant_user_ids = {
            user.pk for user in get_pending_postpayment_users(tournament)
        }
        expired = TournamentPostpaymentInvoice.objects.filter(
            tournament=tournament,
            status=TournamentPostpaymentInvoice.Status.EXPIRED,
            user_id__in=participant_user_ids,
        ).select_related("user")
        reopen_ids: list[int] = []
        for invoice in expired:
            if invoice.user_id in participant_user_ids:
                reopen_ids.append(invoice.pk)
        if reopen_ids:
            updated += int(
                TournamentPostpaymentInvoice.objects.filter(pk__in=reopen_ids).update(
                    status=TournamentPostpaymentInvoice.Status.PENDING,
                    due_at=new_due_at,
                )
            )
    if updated:
        logger.info(
            "Postpayment: синхронизирован due_at для %s инвойсов турнира %s "
            "(deadline_hours=%s, due_at=%s)",
            updated,
            tournament.slug,
            tournament.get_postpayment_deadline_hours(),
            new_due_at,
        )
    return updated


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
    due_local = timezone.localtime(invoice.due_at).strftime("%d.%m.%Y %H:%M")
    amount_str = str(invoice.amount)
    text = (
        f"🎾 <b>Нужно оплатить участие в турнире</b>\n\n"
        f"Турнир: «{tournament.name}»\n"
        f"Сумма: {amount_str} ₽\n"
        f"Оплатить до: {due_local}\n\n"
        f"Ссылка на оплату: {payment_url}"
    )
    send_to_user_by_user(
        invoice.user, text, skip_email=True, event_key="tournament.payment_opened"
    )
    create_notification(
        user=invoice.user,
        message=(f"Оплатите участие в турнире «{tournament.name}» до {due_local}."),
        url=payment_url,
        event_key="tournament.payment_opened",
    )
    try:
        from apps.core.email_service import send_tournament_postpayment_opened_email

        send_tournament_postpayment_opened_email(
            invoice.user,
            tournament,
            amount=amount_str,
            due_at=due_local,
            payment_url=payment_url,
        )
    except Exception:
        logger.exception(
            "Postpayment opened email failed: user=%s tournament=%s",
            invoice.user_id,
            tournament.slug,
        )


def resend_postpayment_payment_link(
    tournament: Tournament,
    user: User,
) -> tuple[bool, str]:
    """Повторно отправить ссылку на оплату участнику с PENDING-инвойсом.

    Args:
        tournament (Tournament): Турнир.
        user: Пользователь участника.

    Returns:
        tuple[bool, str]: Успех и текст для сообщения администратору.
    """
    invoice = (
        get_pending_postpayment_invoices(tournament)
        .filter(
            user=user,
        )
        .select_related("user")
        .first()
    )
    if invoice is None:
        return False, "Нет активного инвойса постоплаты для повторной отправки."

    payment_url = _payment_url(tournament, invoice.id)
    due_local = timezone.localtime(invoice.due_at).strftime("%d.%m.%Y %H:%M")
    amount_str = str(invoice.amount)
    text = (
        f"🔔 <b>Напоминание: оплатите участие</b>\n\n"
        f"Турнир: «{tournament.name}»\n"
        f"Сумма: {amount_str} ₽\n"
        f"Оплатить до: {due_local}\n\n"
        f"Ссылка на оплату: {payment_url}"
    )
    send_to_user_by_user(
        invoice.user, text, skip_email=True, event_key="tournament.payment_link"
    )
    create_notification(
        user=invoice.user,
        message=(
            f"Напоминание: оплатите участие в турнире «{tournament.name}» "
            f"до {due_local}."
        ),
        url=payment_url,
        event_key="tournament.payment_link",
    )
    email_ok = False
    try:
        from apps.core.email_service import send_tournament_postpayment_resend_email

        email_ok = send_tournament_postpayment_resend_email(
            invoice.user,
            tournament,
            amount=amount_str,
            due_at=due_local,
            payment_url=payment_url,
        )
    except Exception:
        logger.exception(
            "Postpayment resend email failed: user=%s tournament=%s",
            getattr(user, "pk", None),
            tournament.slug,
        )
    now = timezone.now()
    invoice.payment_link_resent_at = now
    invoice.save(update_fields=["payment_link_resent_at"])
    logger.info(
        "Postpayment: повторно отправлена ссылка user=%s tournament=%s invoice=%s "
        "email=%s",
        getattr(user, "pk", None),
        tournament.slug,
        invoice.pk,
        email_ok,
    )
    if email_ok:
        return True, "Ссылка на оплату отправлена повторно (почта, Telegram, ЛК)."
    return (
        True,
        "Ссылка отправлена в Telegram и ЛК; письмо на почту не удалось "
        "(проверьте email участника).",
    )


def _collect_tournament_participant_users(tournament: Tournament) -> list[User]:
    """Собрать пользователей всех зарегистрированных участников турнира.

    Args:
        tournament (Tournament): Турнир.

    Returns:
        list: Уникальные пользователи участников (включая парные команды).
    """
    from django.contrib.auth import get_user_model

    user_ids: set[int] = set(
        int(uid)
        for uid in tournament.participants.values_list("user_id", flat=True).distinct()
    )
    if tournament.is_doubles():
        for user_id_1, user_id_2 in tournament.teams.values_list(
            "player1__user_id", "player2__user_id"
        ):
            if user_id_1:
                user_ids.add(int(user_id_1))
            if user_id_2:
                user_ids.add(int(user_id_2))
    user_model = get_user_model()
    return cast(
        "list[User]",
        list(
            user_model.objects.filter(id__in=user_ids)
            .select_related("player")
            .order_by("last_name", "first_name", "email")
        ),
    )


def build_participant_payment_statuses(
    tournament: Tournament,
) -> list[ParticipantPaymentStatus]:
    """Построить статусы оплаты всех участников турнира для админки.

    Args:
        tournament (Tournament): Турнир.

    Returns:
        list[ParticipantPaymentStatus]: Строки таблицы статусов.
    """
    coverages = {
        row.user_id: row
        for row in tournament.registration_coverages.select_related("user")
    }
    paid_user_ids = get_paid_entry_user_ids(tournament)
    entry_paid_ids = paid_user_ids & set(
        tournament.entry_payments.values_list("user_id", flat=True)
    )
    onsite_payments = {
        row.user_id: row
        for row in active_onsite_payments(tournament).select_related("received_by")
    }
    invoices = {
        row.user_id: row
        for row in tournament.postpayment_invoices.select_related("user")
    }
    call_times = {
        row.user_id: row.called_at for row in tournament.postpayment_call_logs.all()
    }
    coverage_labels = {
        TournamentRegistrationCoverage.CoverageType.SUBSCRIPTION_SLOT: (
            "Покрыто FT (подписка)"
        ),
        TournamentRegistrationCoverage.CoverageType.CLUB_PLAN_SLOT: ("Клубный тариф"),
        TournamentRegistrationCoverage.CoverageType.ADMIN_GRANTED: (
            "Выдано администратором"
        ),
    }
    invoice_status_labels = {
        TournamentPostpaymentInvoice.Status.PENDING: "Ожидает оплату (₽)",
        TournamentPostpaymentInvoice.Status.PAID: "Оплачено постоплатой (₽)",
        TournamentPostpaymentInvoice.Status.CANCELLED: "Инвойс отменён",
        TournamentPostpaymentInvoice.Status.EXPIRED: "Не оплатил (срок истёк)",
    }
    rows: list[ParticipantPaymentStatus] = []
    window_open = tournament.postpayment_window_started_at is not None
    for user in _collect_tournament_participant_users(tournament):
        display_name = user.get_full_name() or user.email or f"ID {user.pk}"
        coverage = coverages.get(user.pk)
        invoice = invoices.get(user.pk)
        onsite_payment = onsite_payments.get(user.pk)
        if coverage:
            status = coverage_labels.get(
                coverage.coverage_type,
                coverage.get_coverage_type_display(),
            )
            details_parts: list[str] = []
            if (
                coverage.coverage_type
                == TournamentRegistrationCoverage.CoverageType.SUBSCRIPTION_SLOT
            ):
                details_parts.append(f"Списано {TOURNAMENT_REGISTRATION_COST} FT")
                if (
                    invoice
                    and invoice.status == TournamentPostpaymentInvoice.Status.CANCELLED
                ):
                    details_parts.append("ранее отправлялось уведомление об оплате в ₽")
            if coverage.created_at:
                details_parts.append(
                    f"с {timezone.localtime(coverage.created_at).strftime('%d.%m.%Y %H:%M')}"
                )
            details = "; ".join(details_parts) if details_parts else "—"
        elif user.pk in entry_paid_ids:
            status = "Оплачен взнос (₽)"
            details = "Оплата онлайн подтверждена"
        elif onsite_payment is not None:
            details_parts = [f"Сумма: {onsite_payment.amount} ₽"]
            if onsite_payment.status == TournamentOnsitePayment.Status.RECEIVED:
                status = "Оплачено на корте (₽)"
                if onsite_payment.received_at is not None:
                    details_parts.append(
                        "получено: "
                        f"{timezone.localtime(onsite_payment.received_at).strftime('%d.%m.%Y %H:%M')}"
                    )
                if onsite_payment.received_by is not None:
                    details_parts.append(
                        "подтвердил: "
                        f"{onsite_payment.received_by.get_full_name() or onsite_payment.received_by.email}"
                    )
            else:
                status = "Ожидается оплата на корте (₽)"
                details_parts.append("получение денег ещё не подтверждено")
            details = "; ".join(details_parts)
        elif invoice:
            returned = (
                invoice.status == TournamentPostpaymentInvoice.Status.PAID
                and user.pk not in paid_user_ids
            )
            status = (
                "Взнос возвращён (₽)"
                if returned
                else invoice_status_labels.get(invoice.status, invoice.status)
            )
            details_parts = [f"Сумма: {invoice.amount} ₽"]
            if returned:
                details_parts.append(
                    "возврат выполнен; прежняя оплата больше не действует"
                )
            if invoice.due_at:
                details_parts.append(
                    f"до {timezone.localtime(invoice.due_at).strftime('%d.%m.%Y %H:%M')}"
                )
            if invoice.created_at and invoice.status in (
                TournamentPostpaymentInvoice.Status.PENDING,
                TournamentPostpaymentInvoice.Status.PAID,
            ):
                details_parts.append(
                    "уведомление: "
                    f"{timezone.localtime(invoice.created_at).strftime('%d.%m.%Y %H:%M')}"
                )
            if invoice.paid_at:
                details_parts.append(
                    f"оплачено: {timezone.localtime(invoice.paid_at).strftime('%d.%m.%Y %H:%M')}"
                )
            if invoice.reminder_1h_sent_at:
                details_parts.append(
                    "напоминание за 1 ч: "
                    f"{timezone.localtime(invoice.reminder_1h_sent_at).strftime('%d.%m.%Y %H:%M')}"
                )
            if invoice.payment_link_resent_at:
                details_parts.append(
                    "отправлено: "
                    f"{timezone.localtime(invoice.payment_link_resent_at).strftime('%d.%m.%Y %H:%M')}"
                )
            details = "; ".join(details_parts)
        elif window_open:
            status = "Не оплачен"
            details = "Окно постоплаты открыто, инвойс не создавался"
        elif tournament.allow_postpayment:
            status = "Постоплата (окно не открыто)"
            details = "Зарегистрирован без оплаты, ждёт дедлайна"
        else:
            status = "Без отметки оплаты"
            details = "—"
        rows.append(
            ParticipantPaymentStatus(
                user_id=user.pk,
                player_id=user.player.pk,
                display_name=display_name,
                status=status,
                details=details,
                status_tone=participant_payment_status_tone(status),
                phone=(getattr(user, "phone", "") or "").strip(),
                called_at=call_times.get(user.pk),
                link_resent_at=(
                    invoice.payment_link_resent_at if invoice is not None else None
                ),
                pending_invoice_id=(
                    invoice.pk
                    if invoice
                    and invoice.status == TournamentPostpaymentInvoice.Status.PENDING
                    and not coverage
                    and user.pk not in paid_user_ids
                    and not onsite_payment
                    else None
                ),
                expected_onsite_id=(
                    onsite_payment.pk
                    if onsite_payment
                    and onsite_payment.status == TournamentOnsitePayment.Status.EXPECTED
                    and not coverage
                    and user.pk not in paid_user_ids
                    else None
                ),
                onsite_payment_id=(
                    onsite_payment.pk if onsite_payment is not None else None
                ),
            )
        )
    return rows


def format_postpayment_open_summary(tournament: Tournament, invoice_count: int) -> str:
    """Сформировать текст итога запуска окна постоплаты для админки.

    Args:
        tournament (Tournament): Турнир.
        invoice_count (int): Число созданных инвойсов с уведомлением.

    Returns:
        str: Текст для сообщения администратору.
    """
    pending_rows = [
        row
        for row in build_participant_payment_statuses(tournament)
        if row.status == "Ожидает оплату (₽)"
    ]
    fancoin_rows = [
        row
        for row in build_participant_payment_statuses(tournament)
        if "FT" in row.status
    ]
    parts: list[str] = []
    if fancoin_rows:
        names = ", ".join(row.display_name for row in fancoin_rows)
        parts.append(f"покрыто FT ({len(fancoin_rows)}): {names}")
    if invoice_count:
        names = ", ".join(row.display_name for row in pending_rows)
        parts.append(f"уведомления об оплате в ₽ ({invoice_count}): {names or '—'}")
    if not parts:
        return "все участники уже оплачены или покрыты FT"
    return "; ".join(parts)


@transaction.atomic
def open_postpayment_window(tournament: Tournament) -> tuple[int, int]:
    """Запустить окно постоплаты и создать инвойсы.

    Перед созданием инвойсов пытается списать FT у участников, у которых
    появился баланс после регистрации с постоплатой, если формат оплаты
    это допускает. Однодневный взнос остаётся рублёвым со скидками.

    Args:
        tournament (Tournament): Турнир, для которого запускается постоплата.

    Returns:
        tuple[int, int]: (созданные инвойсы, участники с новым покрытием FT).

    Raises:
        PostpaymentWindowError: Набор закрыт или время однодневной игры уже наступило.
    """
    tournament = Tournament.objects.select_for_update().get(pk=tournament.pk)
    if tournament.postpayment_window_started_at is not None:
        return 0, 0
    if (
        not tournament.allow_postpayment
        or tournament.bracket_generated
        or tournament.status in (TournamentStatus.CANCELLED, TournamentStatus.COMPLETED)
        or tournament_has_generated_matches(tournament)
    ):
        raise PostpaymentWindowError(
            "Окно постоплаты недоступно: набор закрыт или постоплата отключена."
        )
    pending_users = get_pending_postpayment_users(tournament)
    if not pending_users:
        return 0, 0
    now = timezone.now()
    if (
        tournament.duration == TournamentDuration.SINGLE_DAY
        and tournament.start_after_fill
        and tournament.start_date is None
    ):
        # Как при генерации после набора: назначаем лишь отсутствующий день игры.
        tournament.start_date = timezone.localdate(now)
        tournament.save(update_fields=["start_date", "updated_at"])
    due_at = tournament.get_postpayment_window_ends_at(started_at=now)
    if due_at is None:
        raise PostpaymentWindowError(
            "Укажите дату и время начала однодневного турнира перед открытием постоплаты."
        )
    if due_at <= now:
        raise PostpaymentWindowError(
            "Окно постоплаты нельзя открыть: объявленное время начала игры уже наступило."
        )
    fancoin_settled = 0
    if pending_users:
        fancoin_settled = try_settle_pending_users_with_fancoin(
            tournament, users=pending_users
        )
    pending_users = get_pending_postpayment_users(tournament)
    if not pending_users:
        return 0, fancoin_settled
    created_count = 0
    for user in pending_users:
        invoice, created = TournamentPostpaymentInvoice.objects.get_or_create(
            tournament=tournament,
            user=user,
            defaults={
                "amount": (
                    get_tournament_entry_amount(tournament, user)
                    if tournament.duration == TournamentDuration.SINGLE_DAY
                    else Decimal(tournament.entry_fee or 0)
                ),
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
    return created_count, fancoin_settled


def send_1h_reminders() -> int:
    """Отправить напоминания за 1 час до окончания постоплаты.

    Args:
        None: Параметры не требуются.

    Returns:
        int: Количество отправленных напоминаний.
    """
    now = timezone.now()
    one_hour_later = now + timedelta(hours=1)
    invoices = (
        get_pending_postpayment_invoices()
        .select_related("tournament", "user")
        .filter(
            reminder_1h_sent_at__isnull=True,
            due_at__lte=one_hour_later,
            due_at__gte=now,
        )
    )
    total = 0
    for invoice in invoices:
        payment_url = _payment_url(invoice.tournament, invoice.id)
        due_local = timezone.localtime(invoice.due_at).strftime("%d.%m.%Y %H:%M")
        text = (
            f"⏰ <b>Напоминание об оплате</b>\n\n"
            f"До конца срока оплаты турнира «{invoice.tournament.name}» остался 1 час.\n"
            f"Ссылка: {payment_url}"
        )
        send_to_user_by_user(
            invoice.user, text, skip_email=True, event_key="tournament.payment_reminder"
        )
        create_notification(
            user=invoice.user,
            message=(
                f"Напоминание: до оплаты турнира «{invoice.tournament.name}» "
                f"остался 1 час (до {due_local})."
            ),
            url=payment_url,
            event_key="tournament.payment_reminder",
        )
        try:
            from apps.core.email_service import (
                send_tournament_postpayment_1h_reminder_email,
            )

            send_tournament_postpayment_1h_reminder_email(
                invoice.user,
                invoice.tournament,
                due_at=due_local,
                payment_url=payment_url,
            )
        except Exception:
            logger.exception(
                "Postpayment 1h email failed: user=%s tournament=%s",
                invoice.user_id,
                invoice.tournament.slug,
            )
        invoice.reminder_1h_sent_at = now
        invoice.save(update_fields=["reminder_1h_sent_at"])
        total += 1
    return total


@transaction.atomic
def _remove_unpaid_players(tournament: Tournament) -> int:
    """Удалить неоплативших игроков из турнира.

    Args:
        tournament (Tournament): Турнир.

    Returns:
        int: Количество удалённых пользователей.
    """
    tournament = Tournament.objects.select_for_update().get(pk=tournament.pk)
    now = timezone.now()
    unpaid_user_ids = [user.pk for user in get_pending_postpayment_users(tournament)]
    expired = get_pending_postpayment_invoices(tournament).filter(
        user_id__in=unpaid_user_ids,
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
        teams = (
            tournament.teams.select_for_update()
            .filter(
                Q(player1__user_id__in=removed_user_ids)
                | Q(player2__user_id__in=removed_user_ids)
            )
            .select_related("player1", "player2")
        )
        for team in teams:
            remaining_players = [
                player
                for player in (team.player1, team.player2)
                if player is not None and player.user_id not in removed_user_ids
            ]
            if remaining_players:
                team.player1 = remaining_players[0]
                team.player2 = None
                team.save(update_fields=["player1", "player2"])
            else:
                team.delete()
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    for user in user_model.objects.filter(id__in=removed_user_ids):
        send_to_user_by_user(
            user,
            f"⚠️ Вы удалены из турнира «{tournament.name}» из-за неоплаты вступительного взноса в установленный срок.",
            event_key="tournament.unpaid_removed",
        )
    return len(removed_user_ids)


def _generate_after_postpayment(tournament: Tournament) -> tuple[bool, str]:
    """Запустить общий сценарий формирования структуры после проверки оплаты."""
    from .views import _tournament_manual_generate

    ok, msg, _ = _tournament_manual_generate(tournament)
    return ok, msg


@transaction.atomic
def finalize_postpayment_window(tournament: Tournament) -> tuple[bool, str]:
    """Завершить окно постоплаты и сформировать сетку.

    Участники бесплатного турнира без выставленного счёта не создают долг.
    Уже выставленные неоплаченные счета сохраняют силу при обнулении взноса.

    Args:
        tournament (Tournament): Турнир.

    Returns:
        tuple[bool, str]: Результат выполнения (успех, сообщение).
    """
    tournament = Tournament.objects.select_for_update().get(pk=tournament.pk)
    if tournament.status in (TournamentStatus.COMPLETED, TournamentStatus.CANCELLED):
        return False, "Завершённый или отменённый турнир нельзя запустить."
    if tournament.bracket_generated and tournament_has_generated_matches(tournament):
        return True, "Сетка уже сформирована."
    if tournament.postpayment_window_started_at is None:
        return False, "Окно постоплаты ещё не открыто."
    if tournament.bracket_generated and not tournament_has_generated_matches(
        tournament
    ):
        if tournament.manual_pairing_has_started():
            return True, "Турнир запущен в ручном режиме — пары составляет организатор."
        logger.warning(
            "Турнир %s: флаг bracket_generated без матчей — сбрасываем и формируем сетку",
            tournament.slug,
        )
        tournament.bracket_generated = False
        tournament.save(update_fields=["bracket_generated", "updated_at"])
    _remove_unpaid_players(tournament)
    pending_users = get_pending_postpayment_users(tournament)
    if pending_users and (
        (tournament.entry_fee or 0) > 0
        or get_pending_postpayment_invoices(tournament)
        .filter(
            user_id__in=[user.pk for user in pending_users],
        )
        .exists()
    ):
        return False, "Окно постоплаты ещё не завершено: участники ожидают оплату."
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
    )
    total = int(stats["total"] or 0)
    paid = int(stats["paid"] or 0)
    pending = get_pending_postpayment_invoices(tournament).count()
    return {
        "total": total,
        "paid": paid,
        "pending": pending,
        "completed": total > 0 and pending == 0,
    }
