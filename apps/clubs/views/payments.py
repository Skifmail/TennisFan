import csv
import json
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.core.consent_utils import record_club_consent
from apps.payments.models import PaymentRecord, SavedPaymentMethod
from apps.payments.yookassa_client import (
    create_payment_with_credentials,
    get_payment_details_with_credentials,
    get_payment_status_with_credentials,
    test_yookassa_credentials,
)

from ..finance_services import (
    calculate_balance_payment_breakdown,
    cancel_reserved_balance,
    confirm_reserved_balance,
    get_member_balance,
    reserve_member_balance,
)
from ..forms import (
    ClubMembershipFeeSettingsForm,
    ClubPaymentSettingsForm,
    MarkFeePaidForm,
)
from ..models import (
    Club,
    ClubFeePayment,
    ClubFeePaymentPending,
    ClubLegalDocument,
    ClubMember,
    ClubMemberBalanceTransaction,
    ClubMemberPlan,
    ClubMemberPlanStatus,
    ClubMembershipFee,
    ClubMemberStatus,
    ClubPlayerPlan,
    FeePaymentMethod,
    FeePeriod,
)
from ..notifications import send_fee_paid_notification
from ..payment_utils import decrypt_secret, encrypt_secret
from ..plan_services import (
    get_member_active_plan,
    get_member_plan_limits,
    purchase_member_plan,
)
from ..services import (
    club_has_published_offer,
    club_is_operational,
    get_current_period_label,
    get_fee_expiring_soon_text,
    get_fee_status_for_member,
    user_can_manage_fees,
)
from .helpers import (
    _get_club_payment_settings,
    _get_current_club_member,
    logger,
)


@login_required
@require_GET
def my_fees(request: HttpRequest) -> HttpResponse:
    """Отдельная страница взносов больше не используется; перенаправляет в профиль клуба."""
    member = _get_current_club_member(request)
    if not member:
        return redirect("clubs:register_choice")

    player = getattr(request.user, "player", None)
    if player is not None:
        return redirect(
            "clubs:player_profile",
            slug=member.club.slug,
            player_id=player.pk,
        )
    return redirect("clubs:my_finance")


def _get_member_payment_rows(member: ClubMember) -> list[dict[str, Any]]:
    """Собирает объединённую историю платежей участника клуба.

    Включает отменённые резервы баланса (статус ``cancelled``): неуспешная
    попытка оплаты с частичным покрытием балансом, средства возвращены.
    """
    club_plan_payments = list(
        PaymentRecord.objects.filter(
            user=member.user,
            payment_type=PaymentRecord.PaymentType.CLUB_PLAN,
            metadata__club_id=member.club_id,
        ).order_by("-paid_at")
    )
    club_fee_payment_records = list(
        PaymentRecord.objects.filter(
            user=member.user,
            payment_type=PaymentRecord.PaymentType.CLUB_FEE,
            metadata__club_id=member.club_id,
        ).order_by("-paid_at")
    )
    recorded_fee_refs = {
        str(payment.metadata.get("fee_payment_ref") or "").strip()
        for payment in club_fee_payment_records
    }
    fee_payments = list(
        ClubFeePayment.objects.filter(member=member)
        .select_related("fee")
        .exclude(payment_ref__in=recorded_fee_refs)
        .order_by("-paid_at")
    )
    balance_transactions = list(
        ClubMemberBalanceTransaction.objects.filter(
            member=member,
            status__in=[
                ClubMemberBalanceTransaction.Status.COMPLETED,
                ClubMemberBalanceTransaction.Status.CANCELLED,
            ],
        ).order_by("-created_at")
    )

    failure_reason_labels = {
        "no_club_payment_settings": "Клуб отключил YooKassa или не настроил платёжные реквизиты.",
        "club_yookassa_disabled": "Клуб отключил YooKassa, поэтому автосписание было выключено.",
        "secret_decrypt_failed": "Не удалось использовать сохранённые платёжные реквизиты клуба.",
        "balance_reserve_failed": "Не удалось зарезервировать средства на балансе.",
    }

    payment_rows: list[dict[str, Any]] = []
    for payment in club_plan_payments:
        balance_amount = str(payment.metadata.get("balance_amount") or "0")
        external_amount = str(payment.metadata.get("external_amount") or payment.amount)
        total_amount = str(payment.metadata.get("total_amount") or payment.amount)
        failure_reason = failure_reason_labels.get(
            str(payment.metadata.get("failure_reason") or ""),
            str(payment.metadata.get("failure_reason") or ""),
        )
        payment_rows.append(
            {
                "kind": "club_plan",
                "paid_at": payment.paid_at,
                "title": payment.item_label or "Клубный тариф",
                "subtitle": "Оплата тарифа клуба",
                "amount": payment.amount,
                "currency": payment.currency or "RUB",
                "method": ("Автосписание" if payment.is_recurring else "Онлайн-оплата"),
                "reference": payment.yookassa_payment_id or "Не указан",
                "status": "Успешно" if payment.status == "succeeded" else "Неуспешно",
                "details": {
                    "Тип платежа": "Клубный тариф",
                    "Наименование": payment.item_label or "Клубный тариф",
                    "Дата": payment.paid_at.strftime("%d.%m.%Y %H:%M"),
                    "Сумма всего": f"{total_amount} {payment.currency or 'RUB'}",
                    "Списано с баланса": f"{balance_amount} {payment.currency or 'RUB'}",
                    "Списано с карты": f"{external_amount} {payment.currency or 'RUB'}",
                    "Способ": (
                        "Автосписание" if payment.is_recurring else "Онлайн-оплата"
                    ),
                    "Автосписание включено": (
                        "Да" if payment.autopay_enabled else "Нет"
                    ),
                    "Статус": (
                        "Успешно" if payment.status == "succeeded" else "Неуспешно"
                    ),
                    "ID платежа": payment.yookassa_payment_id or "Не указан",
                    **(
                        {"Причина": failure_reason}
                        if payment.status != "succeeded" and failure_reason
                        else {}
                    ),
                },
            }
        )

    for payment in club_fee_payment_records:
        balance_amount = str(payment.metadata.get("balance_amount") or "0")
        external_amount = str(payment.metadata.get("external_amount") or payment.amount)
        total_amount = str(payment.metadata.get("total_amount") or payment.amount)
        failure_reason = failure_reason_labels.get(
            str(payment.metadata.get("failure_reason") or ""),
            str(payment.metadata.get("failure_reason") or ""),
        )
        payment_rows.append(
            {
                "kind": "club_fee",
                "paid_at": payment.paid_at,
                "title": "Членский взнос",
                "subtitle": str(
                    payment.metadata.get("period_label") or "Оплата взноса"
                ),
                "amount": payment.amount,
                "currency": payment.currency or "RUB",
                "method": ("Автосписание" if payment.is_recurring else "Онлайн-оплата"),
                "reference": payment.yookassa_payment_id or "Не указан",
                "status": "Успешно" if payment.status == "succeeded" else "Неуспешно",
                "details": {
                    "Тип платежа": "Членский взнос",
                    "Период": str(payment.metadata.get("period_label") or "Не указан"),
                    "Дата": payment.paid_at.strftime("%d.%m.%Y %H:%M"),
                    "Сумма всего": f"{total_amount} {payment.currency or 'RUB'}",
                    "Списано с баланса": f"{balance_amount} {payment.currency or 'RUB'}",
                    "Списано с карты": f"{external_amount} {payment.currency or 'RUB'}",
                    "Способ": (
                        "Автосписание" if payment.is_recurring else "Онлайн-оплата"
                    ),
                    "Статус": (
                        "Успешно" if payment.status == "succeeded" else "Неуспешно"
                    ),
                    "ID платежа": payment.yookassa_payment_id or "Не указан",
                    **(
                        {"Причина": failure_reason}
                        if payment.status != "succeeded" and failure_reason
                        else {}
                    ),
                },
            }
        )

    for payment in fee_payments:
        payment_rows.append(
            {
                "kind": "club_fee",
                "paid_at": payment.paid_at,
                "title": "Членский взнос",
                "subtitle": payment.period_label,
                "amount": payment.amount,
                "currency": payment.fee.currency,
                "method": (
                    "Онлайн-оплата"
                    if payment.method == FeePaymentMethod.ONLINE
                    else payment.get_method_display()
                ),
                "reference": payment.payment_ref or "Не указан",
                "status": "Успешно",
                "details": {
                    "Тип платежа": "Членский взнос",
                    "Период": payment.period_label,
                    "Дата": payment.paid_at.strftime("%d.%m.%Y %H:%M"),
                    "Сумма": f"{payment.amount} {payment.fee.currency}",
                    "Способ": (
                        "Онлайн-оплата"
                        if payment.method == FeePaymentMethod.ONLINE
                        else payment.get_method_display()
                    ),
                    "Описание": payment.fee.description or "Без описания",
                    "Статус": "Успешно",
                    "ID платежа": payment.payment_ref or "Не указан",
                },
            }
        )

    for transaction_obj in balance_transactions:
        is_credit = (
            transaction_obj.direction == ClubMemberBalanceTransaction.Direction.CREDIT
        )
        if transaction_obj.status == ClubMemberBalanceTransaction.Status.CANCELLED:
            paid_at = transaction_obj.completed_at or transaction_obj.created_at
            payment_rows.append(
                {
                    "kind": "club_balance",
                    "paid_at": paid_at,
                    "title": "Неуспешная попытка оплаты",
                    "subtitle": transaction_obj.description
                    or "Резерв под оплату отменён",
                    "amount": Decimal("0.00"),
                    "currency": "RUB",
                    "method": transaction_obj.get_source_display(),
                    "reference": transaction_obj.reference or "Не указан",
                    "status": "Отменено · средства возвращены на баланс",
                    "details": {
                        "Тип операции": transaction_obj.get_source_display(),
                        "Суть": (
                            "Онлайн-оплата не создана или не завершена; "
                            "зарезервированная сумма возвращена на баланс."
                        ),
                        "Дата": paid_at.strftime("%d.%m.%Y %H:%M"),
                        "Резервировалось": f"{transaction_obj.amount} RUB",
                        "Итог по балансу": "0 ₽ (возврат)",
                        "Описание": transaction_obj.description or "Без описания",
                        "Статус в журнале": transaction_obj.get_status_display(),
                        "Ссылка": transaction_obj.reference or "Не указана",
                    },
                }
            )
            continue

        amount_prefix = "+" if is_credit else "-"
        payment_rows.append(
            {
                "kind": "club_balance",
                "paid_at": transaction_obj.completed_at or transaction_obj.created_at,
                "title": "Баланс клуба",
                "subtitle": transaction_obj.description or "Операция по балансу",
                "amount": f"{amount_prefix}{transaction_obj.amount}",
                "currency": "RUB",
                "method": transaction_obj.get_source_display(),
                "reference": transaction_obj.reference or "Не указан",
                "status": ("Зачислено" if is_credit else "Списано"),
                "details": {
                    "Тип операции": transaction_obj.get_source_display(),
                    "Направление": transaction_obj.get_direction_display(),
                    "Дата": (
                        (
                            transaction_obj.completed_at or transaction_obj.created_at
                        ).strftime("%d.%m.%Y %H:%M")
                    ),
                    "Сумма": f"{amount_prefix}{transaction_obj.amount} RUB",
                    "Описание": transaction_obj.description or "Без описания",
                    "Статус": transaction_obj.get_status_display(),
                    "Ссылка": transaction_obj.reference or "Не указана",
                },
            }
        )

    payment_rows.sort(key=lambda item: item["paid_at"], reverse=True)
    return payment_rows


def _club_payment_split_from_metadata(
    record: PaymentRecord,
) -> dict[str, Decimal] | None:
    """Возвращает части смешанной оплаты (баланс + ЮKassa) из metadata записи.

    Args:
        record: Запись журнала с полем ``metadata`` (ключи ``balance_amount`` и т.д.).

    Returns:
        Словарь с ключами ``balance``, ``external``, ``total`` или ``None``,
        если оплата целиком прошла через ЮKassa без списания баланса.
    """
    meta: dict[str, Any] = dict(record.metadata or {})

    def _parse_money(value: Any) -> Decimal:
        if value is None:
            return Decimal("0.00")
        try:
            return Decimal(str(value).replace(",", ".").strip() or "0").quantize(
                Decimal("0.01")
            )
        except Exception:
            return Decimal("0.00")

    balance_part = _parse_money(meta.get("balance_amount"))
    if balance_part <= Decimal("0"):
        return None

    total_part = _parse_money(meta.get("total_amount"))
    if total_part <= Decimal("0"):
        total_part = _parse_money(record.amount)

    external_part = _parse_money(meta.get("external_amount"))
    if external_part <= Decimal("0") and total_part > balance_part:
        external_part = (total_part - balance_part).quantize(Decimal("0.01"))
    if external_part < Decimal("0"):
        external_part = Decimal("0.00")

    return {
        "balance": balance_part,
        "external": external_part,
        "total": total_part,
    }


def _get_club_cashbox_rows(club: Club) -> list[dict[str, Any]]:
    """Собирает общую историю поступлений клуба."""
    from apps.tournaments.models import Tournament

    tournament_ids = list(
        Tournament.objects.filter(club=club).values_list("id", flat=True)
    )
    records = list(
        PaymentRecord.objects.filter(
            status="succeeded",
            payment_type__in=[
                PaymentRecord.PaymentType.CLUB_PLAN,
                PaymentRecord.PaymentType.CLUB_FEE,
            ],
            metadata__club_id=club.id,
        ).order_by("-paid_at")
    )
    if tournament_ids:
        records.extend(
            list(
                PaymentRecord.objects.filter(
                    status="succeeded",
                    payment_type=PaymentRecord.PaymentType.TOURNAMENT,
                    item_id__in=[str(item_id) for item_id in tournament_ids],
                ).order_by("-paid_at")
            )
        )

    balance_topups = list(
        ClubMemberBalanceTransaction.objects.filter(
            club=club,
            status=ClubMemberBalanceTransaction.Status.COMPLETED,
            direction=ClubMemberBalanceTransaction.Direction.CREDIT,
            source=ClubMemberBalanceTransaction.Source.MANUAL,
        )
        .select_related("member__user")
        .order_by("-completed_at", "-created_at")
    )

    rows: list[dict[str, Any]] = []
    for record in records:
        member_name = getattr(record.user, "get_full_name", lambda: "")() or getattr(
            record.user, "email", "Не указан"
        )
        payment_split = _club_payment_split_from_metadata(record)
        if record.payment_type == PaymentRecord.PaymentType.CLUB_PLAN:
            subtitle = "Оплата тарифа клуба"
            if payment_split:
                method = "Баланс + ЮKassa"
                details = {
                    "Плательщик": member_name,
                    "Тип": "Тариф клуба",
                    "Основание": record.item_label or record.get_payment_type_display(),
                    "Детали": subtitle,
                    "Сумма всего": f"{payment_split['total']} {record.currency or 'RUB'}",
                    "ЮKassa (поступило на счёт)": (
                        f"{payment_split['external']} {record.currency or 'RUB'}"
                    ),
                    "С баланса игрока (внутренний зачёт)": (
                        f"{payment_split['balance']} {record.currency or 'RUB'}"
                    ),
                    "Дата": record.paid_at.strftime("%d.%m.%Y %H:%M"),
                    "Способ": method,
                    "ID платежа ЮKassa": record.yookassa_payment_id or "Не указан",
                }
            else:
                method = "Автосписание" if record.is_recurring else "Онлайн-оплата"
                details = {
                    "Плательщик": member_name,
                    "Тип": "Тариф клуба",
                    "Основание": record.item_label or record.get_payment_type_display(),
                    "Детали": subtitle,
                    "Сумма": f"{record.amount} {record.currency or 'RUB'}",
                    "Дата": record.paid_at.strftime("%d.%m.%Y %H:%M"),
                    "Способ": method,
                    "ID платежа": record.yookassa_payment_id or "Не указан",
                }
        elif record.payment_type == PaymentRecord.PaymentType.CLUB_FEE:
            subtitle = str(record.metadata.get("period_label") or "Оплата взноса")
            if payment_split:
                method = "Баланс + ЮKassa"
                details = {
                    "Плательщик": member_name,
                    "Тип": "Членский взнос",
                    "Основание": record.item_label or record.get_payment_type_display(),
                    "Период": subtitle,
                    "Сумма всего": f"{payment_split['total']} {record.currency or 'RUB'}",
                    "ЮKassa (поступило на счёт)": (
                        f"{payment_split['external']} {record.currency or 'RUB'}"
                    ),
                    "С баланса игрока (внутренний зачёт)": (
                        f"{payment_split['balance']} {record.currency or 'RUB'}"
                    ),
                    "Дата": record.paid_at.strftime("%d.%m.%Y %H:%M"),
                    "Способ": method,
                    "ID платежа ЮKassa": record.yookassa_payment_id or "Не указан",
                }
            else:
                method = "Автосписание" if record.is_recurring else "Онлайн-оплата"
                details = {
                    "Плательщик": member_name,
                    "Тип": "Членский взнос",
                    "Основание": record.item_label or record.get_payment_type_display(),
                    "Период": subtitle,
                    "Сумма": f"{record.amount} {record.currency or 'RUB'}",
                    "Дата": record.paid_at.strftime("%d.%m.%Y %H:%M"),
                    "Способ": method,
                    "ID платежа": record.yookassa_payment_id or "Не указан",
                }
        else:
            subtitle = "Оплата турнира"
            method = "Онлайн-оплата"
            details = {
                "Плательщик": member_name,
                "Тип": "Турнир",
                "Основание": record.item_label or record.get_payment_type_display(),
                "Детали": subtitle,
                "Сумма": f"{record.amount} {record.currency or 'RUB'}",
                "Дата": record.paid_at.strftime("%d.%m.%Y %H:%M"),
                "Способ": method,
                "ID платежа": record.yookassa_payment_id or "Не указан",
            }

        rows.append(
            {
                "paid_at": record.paid_at,
                "member_name": member_name,
                "title": record.item_label or record.get_payment_type_display(),
                "subtitle": subtitle,
                "amount": record.amount,
                "currency": record.currency or "RUB",
                "method": method,
                "payment_channel": "online",
                "operation_kind": (
                    "club_plan"
                    if record.payment_type == PaymentRecord.PaymentType.CLUB_PLAN
                    else (
                        "club_fee"
                        if record.payment_type == PaymentRecord.PaymentType.CLUB_FEE
                        else "tournament"
                    )
                ),
                "reference": record.yookassa_payment_id or "Не указан",
                "status": "Успешно",
                "details": details,
                "cashbox_split": payment_split,
            }
        )

    for transaction in balance_topups:
        rows.append(
            {
                "paid_at": transaction.completed_at or transaction.created_at,
                "member_name": transaction.member.user.get_full_name()
                or transaction.member.user.email,
                "title": "Пополнение баланса клуба",
                "subtitle": transaction.description or "Ручное пополнение",
                "amount": transaction.amount,
                "currency": "RUB",
                "method": "Ручная операция",
                "payment_channel": "manual",
                "operation_kind": "balance_adjustment",
                "reference": transaction.reference or "Не указана",
                "status": "Зачислено",
                "cashbox_split": None,
                "details": {
                    "Плательщик": transaction.member.user.get_full_name()
                    or transaction.member.user.email,
                    "Тип": "Пополнение баланса",
                    "Основание": "Ручная корректировка",
                    "Детали": transaction.description or "Ручное пополнение",
                    "Сумма": f"{transaction.amount} RUB",
                    "Дата": (
                        transaction.completed_at or transaction.created_at
                    ).strftime("%d.%m.%Y %H:%M"),
                    "Способ": "Ручная операция",
                    "Ссылка": transaction.reference or "Не указана",
                },
            }
        )

    rows.sort(key=lambda item: item["paid_at"], reverse=True)
    return rows


def _filter_club_cashbox_rows(
    rows: list[dict[str, Any]],
    *,
    source_filter: str,
    operation_filter: str,
    month_filter: str,
    year_filter: str,
    search_query: str,
) -> list[dict[str, Any]]:
    """Фильтрует строки кассы клуба по выбранным параметрам."""
    filtered_rows = rows

    normalized_query = search_query.strip().casefold()
    if normalized_query:
        filtered_rows = [
            row
            for row in filtered_rows
            if normalized_query in str(row.get("member_name") or "").casefold()
        ]

    if source_filter in {"online", "manual"}:
        filtered_rows = [
            row for row in filtered_rows if row.get("payment_channel") == source_filter
        ]

    if operation_filter in {
        "balance_adjustment",
        "club_fee",
        "club_plan",
        "tournament",
    }:
        filtered_rows = [
            row
            for row in filtered_rows
            if row.get("operation_kind") == operation_filter
        ]

    if month_filter.isdigit():
        month_number = int(month_filter)
        if 1 <= month_number <= 12:
            filtered_rows = [
                row for row in filtered_rows if row["paid_at"].month == month_number
            ]

    if year_filter.isdigit():
        year_number = int(year_filter)
        filtered_rows = [
            row for row in filtered_rows if row["paid_at"].year == year_number
        ]

    return filtered_rows


def _export_club_cashbox_csv(
    club: Club,
    rows: list[dict[str, Any]],
    *,
    source_filter: str,
    operation_filter: str,
    month_filter: str,
    year_filter: str,
) -> HttpResponse:
    """Выгружает отфильтрованную историю кассы клуба в CSV."""
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="club-cashbox-{club.slug}-{timezone.now():%Y%m%d-%H%M}.csv"'
    )
    response.write("\ufeff")

    writer = csv.writer(response)
    writer.writerow(
        [
            "Дата",
            "Игрок",
            "Категория",
            "Канал",
            "Название",
            "Описание",
            "Сумма всего",
            "Валюта",
            "ЮKassa",
            "С баланса",
            "Способ",
            "Статус",
            "Ссылка",
        ]
    )

    operation_labels = {
        "balance_adjustment": "Ручная корректировка баланса",
        "club_fee": "Членский взнос",
        "club_plan": "Тариф клуба",
        "tournament": "Турнир",
    }
    channel_labels = {
        "manual": "Ручная операция",
        "online": "Онлайн",
    }

    for row in rows:
        split = row.get("cashbox_split")
        writer.writerow(
            [
                row["paid_at"].strftime("%d.%m.%Y %H:%M"),
                row["member_name"],
                operation_labels.get(str(row.get("operation_kind") or ""), "Операция"),
                channel_labels.get(str(row.get("payment_channel") or ""), "Не указан"),
                row["title"],
                row["subtitle"],
                row["amount"],
                row["currency"],
                split["external"] if split else "",
                split["balance"] if split else "",
                row["method"],
                row["status"],
                row["reference"],
            ]
        )

    return response


@login_required
@require_GET
def my_finance(request: HttpRequest) -> HttpResponse:
    """Объединённая страница финансов клуба: тариф, взносы и платежи игрока."""
    member = _get_current_club_member(request)
    if not member:
        messages.info(request, "Вы не состоите в клубе.")
        return redirect("clubs:register_choice")

    club = member.club
    payment_connected = _get_club_payment_settings(club) is not None
    member_plan = get_member_active_plan(member)
    plan_limits = get_member_plan_limits(member)
    fee = ClubMembershipFee.objects.filter(club=club).order_by("-id").first()
    fee_status = get_fee_status_for_member(club, member) if fee else None
    fee_expiring_text = (
        get_fee_expiring_soon_text(fee) if fee and fee_status == "expiring_soon" else ""
    )

    subscription_usage_percent = 0
    if (
        plan_limits
        and plan_limits.monthly_tournaments_limit
        and plan_limits.monthly_tournaments_limit > 0
    ):
        subscription_usage_percent = min(
            100,
            int(
                (plan_limits.tournaments_used / plan_limits.monthly_tournaments_limit)
                * 100
            ),
        )

    club_plan_autopay_card = (
        SavedPaymentMethod.objects.filter(
            user=request.user,
            club=club,
            is_active=True,
            is_default_for_club_plans=True,
        )
        .order_by("-created_at")
        .first()
    )
    club_fee_autopay_card = (
        SavedPaymentMethod.objects.filter(
            user=request.user,
            club=club,
            is_active=True,
            is_default_for_club_fees=True,
        )
        .order_by("-created_at")
        .first()
    )
    payments_all = _get_member_payment_rows(member)
    member_balance = get_member_balance(member)

    return render(
        request,
        "clubs/my_finance.html",
        {
            "club": club,
            "is_club_panel": True,
            "member_plan": member_plan,
            "plan_limits": plan_limits,
            "fee": fee,
            "fee_status": fee_status,
            "fee_expiring_text": fee_expiring_text,
            "subscription_usage_percent": subscription_usage_percent,
            "club_plan_autopay_card": club_plan_autopay_card,
            "club_fee_autopay_card": club_fee_autopay_card,
            "club_payment_connected": payment_connected,
            "payments": payments_all[:10],
            "payments_total": len(payments_all),
            "member_balance": member_balance,
        },
    )


@login_required
@require_GET
def my_payments(request: HttpRequest) -> HttpResponse:
    """Полная история клубных платежей игрока с пагинацией и деталями."""
    member = _get_current_club_member(request)
    if not member:
        messages.info(request, "Вы не состоите в клубе.")
        return redirect("clubs:register_choice")

    payments_all = _get_member_payment_rows(member)
    paginator = Paginator(payments_all, 20)
    payments_page = paginator.get_page(request.GET.get("page", 1))
    return render(
        request,
        "clubs/my_payments.html",
        {
            "club": member.club,
            "payments_page": payments_page,
            "payments_total": len(payments_all),
            "is_club_panel": True,
        },
    )


@login_required
@require_GET
def club_cashbox_history(request: HttpRequest, slug: str) -> HttpResponse:
    """Общая история поступлений клуба для раздела «Моя касса»."""
    club = get_object_or_404(Club, slug=slug)
    if not user_can_manage_fees(request.user, club):
        return redirect("clubs:dashboard", slug=slug)

    rows = _get_club_cashbox_rows(club)
    source_filter = str(request.GET.get("source") or "").strip().lower()
    operation_filter = str(request.GET.get("operation") or "").strip().lower()
    month_filter = str(request.GET.get("month") or "").strip()
    year_filter = str(request.GET.get("year") or "").strip()
    search_query = str(request.GET.get("q") or "").strip()

    filtered_rows = _filter_club_cashbox_rows(
        rows,
        source_filter=source_filter,
        operation_filter=operation_filter,
        month_filter=month_filter,
        year_filter=year_filter,
        search_query=search_query,
    )

    if str(request.GET.get("export") or "").strip().lower() == "csv":
        return _export_club_cashbox_csv(
            club,
            filtered_rows,
            source_filter=source_filter,
            operation_filter=operation_filter,
            month_filter=month_filter,
            year_filter=year_filter,
        )

    available_years = sorted({row["paid_at"].year for row in rows}, reverse=True)
    available_months = sorted({row["paid_at"].month for row in rows})
    return render(
        request,
        "clubs/club_cashbox_history.html",
        {
            "club": club,
            "payments": filtered_rows,
            "payments_total": len(rows),
            "filtered_total": len(filtered_rows),
            "filters": {
                "source": source_filter,
                "operation": operation_filter,
                "month": month_filter,
                "year": year_filter,
                "q": search_query,
            },
            "available_years": available_years,
            "available_months": available_months,
            "is_club_panel": True,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def fees_settings(request: HttpRequest, slug: str) -> HttpResponse:
    """Настройка взноса клуба (только admin)."""
    club = get_object_or_404(Club, slug=slug)
    if not user_can_manage_fees(request.user, club):
        messages.error(request, "Настраивать взносы может только администратор.")
        return redirect("clubs:dashboard", slug=slug)
    if not club_is_operational(club):
        messages.error(
            request,
            "Клуб приостановлен. Продлите подписку для возобновления доступа.",
        )
        return redirect("clubs:club_public_detail", slug=slug)
    fee = ClubMembershipFee.objects.filter(club=club).order_by("-id").first()
    if request.method == "POST":
        form = ClubMembershipFeeSettingsForm(request.POST, instance=fee)
        if form.is_valid():
            if fee:
                fee = form.save()
            else:
                fee = form.save(commit=False)
                fee.club = club
                fee.save()
            messages.success(request, "Настройки взноса сохранены.")
            return redirect("clubs:fees_settings", slug=slug)
    else:
        form = ClubMembershipFeeSettingsForm(instance=fee)
    recent_payments = (
        ClubFeePayment.objects.filter(club=club)
        .select_related("member", "member__user", "fee")
        .order_by("-paid_at")[:8]
    )
    return render(
        request,
        "clubs/fees_settings.html",
        {
            "club": club,
            "form": form,
            "fee": fee,
            "recent_payments": recent_payments,
            "is_club_panel": True,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def payment_settings(request: HttpRequest, slug: str) -> HttpResponse:
    """Настройка подключения платёжного провайдера клуба."""
    club = get_object_or_404(Club, slug=slug)
    if not user_can_manage_fees(request.user, club):
        messages.error(request, "Настраивать платежи клуба может только администратор.")
        return redirect("clubs:dashboard", slug=slug)
    if not club_is_operational(club):
        messages.error(
            request,
            "Клуб приостановлен. Продлите подписку для возобновления доступа.",
        )
        return redirect("clubs:club_public_detail", slug=slug)

    latest_fee = ClubMembershipFee.objects.filter(club=club).order_by("-id").first()
    payment_fee = _get_club_payment_settings(club)
    fee = payment_fee or latest_fee
    connection_test_ok: bool | None = None
    connection_test_message = ""
    if request.method == "POST":
        action = (request.POST.get("action") or "save").strip()
        if action == "disconnect":
            if payment_fee:
                timestamp = int(timezone.now().timestamp())
                affected_member_plans = list(
                    ClubMemberPlan.objects.filter(
                        club_member__club=club,
                        status=ClubMemberPlanStatus.ACTIVE,
                        auto_renew=True,
                    ).select_related("club_member__user", "club_member__club", "plan")
                )
                affected_methods = list(
                    SavedPaymentMethod.objects.filter(
                        club=club,
                        is_active=True,
                    ).filter(is_default_for_club_plans=True)
                )
                affected_methods.extend(
                    list(
                        SavedPaymentMethod.objects.filter(
                            club=club,
                            is_active=True,
                            is_default_for_club_fees=True,
                        ).exclude(pk__in=[method.pk for method in affected_methods])
                    )
                )

                for member_plan in affected_member_plans:
                    PaymentRecord.objects.update_or_create(
                        user=member_plan.club_member.user,
                        yookassa_payment_id=(
                            f"disabled-club-plan-{member_plan.pk}-{timestamp}"
                        ),
                        defaults={
                            "payment_type": PaymentRecord.PaymentType.CLUB_PLAN,
                            "item_id": str(member_plan.plan_id),
                            "item_label": (
                                f"{member_plan.club_member.club.name}: {member_plan.plan.name}"
                            ),
                            "amount": member_plan.plan.monthly_fee,
                            "status": "failed",
                            "is_recurring": True,
                            "autopay_enabled": False,
                            "metadata": {
                                "club_id": member_plan.club_member.club_id,
                                "club_member_plan_id": member_plan.pk,
                                "balance_amount": "0.00",
                                "external_amount": f"{member_plan.plan.monthly_fee:.2f}",
                                "total_amount": f"{member_plan.plan.monthly_fee:.2f}",
                                "failure_reason": "club_yookassa_disabled",
                            },
                        },
                    )

                for method in affected_methods:
                    had_plan_autopay = method.is_default_for_club_plans
                    had_fee_autopay = method.is_default_for_club_fees
                    if had_plan_autopay:
                        method.deactivate_for_club_plans()
                    if had_fee_autopay:
                        method.deactivate_for_club_fees()
                        PaymentRecord.objects.update_or_create(
                            user=method.user,
                            yookassa_payment_id=(
                                f"disabled-club-fee-{method.user_id}-{method.pk}-{timestamp}"
                            ),
                            defaults={
                                "payment_type": PaymentRecord.PaymentType.CLUB_FEE,
                                "item_id": str(payment_fee.id),
                                "item_label": "Членский взнос клуба",
                                "amount": payment_fee.amount,
                                "status": "failed",
                                "is_recurring": True,
                                "autopay_enabled": False,
                                "metadata": {
                                    "club_id": club.id,
                                    "period_label": "",
                                    "balance_amount": "0.00",
                                    "external_amount": f"{payment_fee.amount:.2f}",
                                    "total_amount": f"{payment_fee.amount:.2f}",
                                    "failure_reason": "club_yookassa_disabled",
                                },
                            },
                        )

                if affected_member_plans:
                    ClubMemberPlan.objects.filter(
                        pk__in=[member_plan.pk for member_plan in affected_member_plans]
                    ).update(auto_renew=False)

                payment_fee.payment_provider = ""
                payment_fee.payment_shop_id = ""
                payment_fee.payment_api_key = ""
                payment_fee.save(
                    update_fields=[
                        "payment_provider",
                        "payment_shop_id",
                        "payment_api_key",
                    ]
                )
                messages.success(
                    request,
                    "Подключение YooKassa отключено. Ключи удалены, клубные автосписания выключены.",
                )
            else:
                messages.info(request, "У клуба нет сохранённых реквизитов YooKassa.")
            return redirect("clubs:payment_settings", slug=slug)

        form = ClubPaymentSettingsForm(request.POST, instance=fee)
        if form.is_valid():
            new_secret = (form.cleaned_data.get("new_secret_key") or "").strip()
            target_fee = form.save(commit=False)
            target_fee.club = club
            target_fee.amount = target_fee.amount or Decimal("0")
            target_fee.currency = target_fee.currency or "RUB"
            target_fee.period = target_fee.period or FeePeriod.MONTHLY
            target_fee.period_start_day = target_fee.period_start_day or 1

            if action == "test":
                shop_id = (
                    (target_fee.payment_shop_id or "")
                    or (fee.payment_shop_id if fee else "")
                ).strip()
                secret = ""
                if new_secret:
                    secret = new_secret
                elif fee and fee.payment_api_key:
                    try:
                        secret = decrypt_secret(fee.payment_api_key)
                    except Exception as exc:
                        logger.warning(
                            "Не удалось расшифровать клубный ключ YooKassa для теста: %s",
                            exc,
                        )
                        secret = ""

                if not shop_id or not secret:
                    connection_test_ok = False
                    connection_test_message = (
                        "Для теста связи укажите Shop ID и Secret Key YooKassa."
                    )
                    messages.error(request, connection_test_message)
                else:
                    target_fee.payment_shop_id = shop_id
                    connection_test_ok, connection_test_message = (
                        test_yookassa_credentials(
                            shop_id,
                            secret,
                        )
                    )
                    if connection_test_ok:
                        messages.success(request, connection_test_message)
                    else:
                        messages.error(request, connection_test_message)
                fee = target_fee
                form = ClubPaymentSettingsForm(instance=target_fee)
            else:
                ld = ClubLegalDocument.objects.filter(club=club).first()
                if not ld or not ld.is_published:
                    messages.error(
                        request,
                        "Для подключения платежей необходимо опубликовать оферту клуба.",
                    )
                    return redirect("clubs:payment_settings", slug=slug)
                fee = target_fee
                fee.save()
                if new_secret:
                    fee.payment_api_key = encrypt_secret(new_secret)
                    fee.save(update_fields=["payment_api_key"])
                messages.success(request, "Платёжные реквизиты клуба сохранены.")
                return redirect("clubs:payment_settings", slug=slug)
    else:
        form = ClubPaymentSettingsForm(instance=fee)

    persisted_payment_settings = _get_club_payment_settings(club)
    display_fee = persisted_payment_settings or fee

    return render(
        request,
        "clubs/payment_settings.html",
        {
            "club": club,
            "form": form,
            "fee": fee,
            "is_club_panel": True,
            "payment_connected": bool(persisted_payment_settings),
            "masked_shop_id": (
                f"{display_fee.payment_shop_id[:3]}***{display_fee.payment_shop_id[-2:]}"
                if display_fee
                and display_fee.payment_shop_id
                and len(display_fee.payment_shop_id) >= 5
                else (
                    display_fee.payment_shop_id
                    if display_fee and display_fee.payment_shop_id
                    else ""
                )
            ),
            "connection_test_ok": connection_test_ok,
            "connection_test_message": connection_test_message,
            "webhook_url": (
                f"{str(getattr(settings, 'SITE_URL', '')).rstrip('/')}"
                f"{reverse('clubs:club_payment_webhook')}"
            ),
        },
    )


@login_required
@require_GET
def fees_payments(request: HttpRequest, slug: str) -> HttpResponse:
    """Список оплат взносов (только admin)."""
    club = get_object_or_404(Club, slug=slug)
    if not user_can_manage_fees(request.user, club):
        return redirect("clubs:dashboard", slug=slug)
    period_filter = request.GET.get("period", "")
    qs = (
        ClubFeePayment.objects.filter(club=club)
        .select_related("member", "member__user", "fee")
        .order_by("-paid_at")
    )
    if period_filter:
        qs = qs.filter(period_label=period_filter)
    fee = ClubMembershipFee.objects.filter(club=club, is_active=True).first()
    mark_form = None
    if fee:
        mark_form = MarkFeePaidForm(club=club, fee=fee)
    return render(
        request,
        "clubs/fees_payments.html",
        {
            "club": club,
            "payments": qs[:100],
            "fee": fee,
            "mark_form": mark_form,
            "is_club_panel": True,
        },
    )


@login_required
@require_POST
def fees_mark_paid(request: HttpRequest, slug: str) -> HttpResponse:
    """Ручная отметка об оплате взноса (только admin)."""
    club = get_object_or_404(Club, slug=slug)
    if not user_can_manage_fees(request.user, club):
        return redirect("clubs:dashboard", slug=slug)
    fee = ClubMembershipFee.objects.filter(club=club, is_active=True).first()
    if not fee:
        messages.error(request, "Сначала настройте взносы.")
        return redirect("clubs:fees_payments", slug=slug)
    form = MarkFeePaidForm(request.POST, club=club, fee=fee)
    if form.is_valid():
        ClubFeePayment.objects.create(
            club=club,
            member=form.cleaned_data["member"],
            fee=fee,
            amount=form.cleaned_data["amount"],
            period_label=form.cleaned_data["period_label"],
            paid_at=timezone.now(),
            method=FeePaymentMethod.MANUAL,
            marked_by=request.user,
        )
        messages.success(request, "Оплата отмечена.")
    else:
        for error in form.errors.values():
            messages.error(request, "; ".join(error))
    return redirect("clubs:fees_payments", slug=slug)


@login_required
@require_POST
def my_fees_pay(request: HttpRequest) -> HttpResponse:
    """Инициация оплаты членского взноса игроком через ЮKassa."""
    member = _get_current_club_member(request)
    if not member:
        messages.error(request, "Клуб не найден.")
        return redirect("clubs:register_choice")

    club = member.club
    fee = (
        ClubMembershipFee.objects.filter(club=club, is_active=True)
        .order_by("-id")
        .first()
    )
    if fee is None:
        messages.error(request, "Онлайн-оплата взносов не настроена в этом клубе.")
        return redirect("clubs:my_plan")

    breakdown = calculate_balance_payment_breakdown(member, fee.amount)
    payment_settings = _get_club_payment_settings(club)
    if payment_settings is None and breakdown.external_amount_due > 0:
        messages.error(request, "Онлайн-оплата взносов не настроена в этом клубе.")
        return redirect("clubs:my_plan")

    next_url = (request.POST.get("next") or "").strip()
    raw_offer = str(request.POST.get("offer_accepted", "")).strip().lower()
    if raw_offer not in {"1", "true", "on", "yes"}:
        messages.error(
            request,
            "Для продолжения оплаты необходимо подтвердить согласие с условиями Публичной оферты.",
        )
        return redirect("clubs:my_fee_payment_preview")
    if not club_has_published_offer(club):
        messages.error(
            request,
            "Оплата недоступна: клуб не опубликовал оферту. Обратитесь к администратору клуба.",
        )
        return redirect("clubs:my_fee_payment_preview")
    raw_club_offer = str(request.POST.get("club_offer_accepted", "")).strip().lower()
    if raw_club_offer not in {"1", "true", "on", "yes"}:
        messages.error(
            request,
            "Для оплаты необходимо принять оферту этого клуба.",
        )
        return redirect("clubs:my_fee_payment_preview")
    raw_autopay = str(request.POST.get("enable_autopay", "")).strip().lower()
    enable_autopay = raw_autopay in {"1", "true", "on", "yes"}
    period_label = get_current_period_label(fee)
    if ClubFeePayment.objects.filter(
        member=member,
        fee=fee,
        period_label=period_label,
    ).exists():
        messages.info(request, "Взнос за этот период уже оплачен.")
        if next_url and url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return redirect(next_url)
        return redirect("clubs:my_plan")

    balance_transaction = None
    if breakdown.balance_to_apply > 0:
        try:
            balance_transaction = reserve_member_balance(
                member,
                breakdown.balance_to_apply,
                source=ClubMemberBalanceTransaction.Source.CLUB_FEE_PAYMENT,
                description=f"Оплата членского взноса за период {period_label}",
                reference=f"club-fee:{fee.id}:{period_label}",
                metadata={"fee_id": fee.id, "period_label": period_label},
            )
        except ValueError:
            messages.error(
                request,
                "Не удалось зарезервировать средства с баланса. Обновите страницу и попробуйте снова.",
            )
            return redirect("clubs:my_fee_payment_preview")

    if breakdown.external_amount_due <= 0:
        from apps.payments.models import PaymentRecord

        confirm_reserved_balance(balance_transaction)
        ClubFeePayment.objects.create(
            club=club,
            member=member,
            fee=fee,
            amount=fee.amount,
            period_label=period_label,
            paid_at=timezone.now(),
            method=FeePaymentMethod.BALANCE,
            payment_ref=f"balance:{fee.id}:{period_label}",
        )
        PaymentRecord.objects.create(
            user=request.user,
            payment_type=PaymentRecord.PaymentType.CLUB_FEE,
            item_id=str(fee.id),
            item_label="Членский взнос клуба",
            amount=fee.amount,
            status="succeeded",
            is_recurring=False,
            autopay_enabled=False,
            yookassa_payment_id=(
                f"balance-club-fee-{member.id}-{int(timezone.now().timestamp())}"
            ),
            metadata={
                "club_id": club.id,
                "club_slug": club.slug,
                "period_label": period_label,
                "balance_amount": f"{fee.amount:.2f}",
                "external_amount": "0.00",
                "total_amount": f"{fee.amount:.2f}",
                "fee_payment_ref": f"balance:{fee.id}:{period_label}",
            },
        )
        record_club_consent(request, club)
        messages.success(request, "Членский взнос успешно оплачен с баланса!")
        if next_url and url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return redirect(next_url)
        return redirect("clubs:my_finance")

    assert payment_settings is not None
    try:
        secret = decrypt_secret(payment_settings.payment_api_key)
    except Exception as exc:
        cancel_reserved_balance(balance_transaction)
        logger.warning("Не удалось расшифровать секретный ключ клуба: %s", exc)
        messages.error(
            request,
            "Ошибка настройки оплаты. Обратитесь к администратору клуба.",
        )
        return redirect("clubs:my_plan")

    amount_str = f"{breakdown.external_amount_due:.2f}"
    description = f"Членский взнос {club.name}, {period_label}"
    return_url = request.build_absolute_uri(reverse("clubs:my_fees_return"))
    metadata = {
        "payment_type": "club_fee",
        "club_id": str(club.id),
        "fee_id": str(fee.id),
        "member_id": str(member.id),
        "period_label": period_label,
    }
    if enable_autopay:
        metadata["enable_autopay"] = "1"

    try:
        payment_id, confirmation_url = create_payment_with_credentials(
            shop_id=payment_settings.payment_shop_id,
            secret_key=secret,
            amount=amount_str,
            return_url=return_url,
            description=description,
            metadata=metadata,
            customer_email=request.user.email,
            save_payment_method=enable_autopay,
        )
    except (ValueError, RuntimeError) as exc:
        cancel_reserved_balance(balance_transaction)
        logger.warning("Ошибка создания платежа взноса: %s", exc)
        messages.error(request, "Не удалось создать платёж. Проверьте настройки клуба.")
        if next_url and url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return redirect(next_url)
        return redirect("clubs:my_plan")
    except Exception as exc:
        cancel_reserved_balance(balance_transaction)
        logger.exception("Неожиданная ошибка при создании платежа взноса: %s", exc)
        messages.error(
            request,
            "Не удалось связаться с платёжным шлюзом. Средства с баланса возвращены — попробуйте снова.",
        )
        if next_url and url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return redirect(next_url)
        return redirect("clubs:my_plan")

    ClubFeePaymentPending.objects.create(
        payment_id=payment_id,
        club=club,
        fee=fee,
        member=member,
        amount=fee.amount,
        period_label=period_label,
    )
    request.session["club_fee_payment_pending"] = {
        "payment_id": payment_id,
        "club_slug": club.slug,
        "enable_autopay": "1" if enable_autopay else "",
        "next": next_url,
        "balance_transaction_id": balance_transaction.id if balance_transaction else "",
        "balance_amount": f"{breakdown.balance_to_apply:.2f}",
        "total_amount": f"{fee.amount:.2f}",
    }
    request.session.modified = True
    return redirect(confirmation_url)


@login_required
@require_GET
def my_fee_payment_preview(request: HttpRequest) -> HttpResponse:
    """Предпросмотр оплаты членского взноса через кассу текущего клуба."""
    member = _get_current_club_member(request)
    if not member:
        messages.info(request, "Вы не состоите в клубе.")
        return redirect("clubs:register_choice")

    fee = (
        ClubMembershipFee.objects.filter(club=member.club, is_active=True)
        .order_by("-id")
        .first()
    )
    if fee is None:
        messages.error(request, "Онлайн-оплата взносов не настроена в этом клубе.")
        return redirect("clubs:my_plan")

    breakdown = calculate_balance_payment_breakdown(member, fee.amount)
    payment_settings = _get_club_payment_settings(member.club)
    if payment_settings is None and breakdown.external_amount_due > 0:
        messages.error(request, "Онлайн-оплата взносов не настроена в этом клубе.")
        return redirect("clubs:my_plan")

    period_label = get_current_period_label(fee)
    if ClubFeePayment.objects.filter(
        member=member,
        fee=fee,
        period_label=period_label,
    ).exists():
        messages.info(request, "Взнос за текущий период уже оплачен.")
        next_url = (request.GET.get("next") or "").strip()
        if next_url and url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return redirect(next_url)
        return redirect("clubs:my_plan")

    next_url = (request.GET.get("next") or "").strip()
    details = [
        ("Клуб", member.club.name),
        ("Период", fee.get_period_display()),
        ("Расчётный период", period_label),
    ]
    if fee.description:
        details.append(("Комментарий клуба", fee.description))
    if breakdown.balance_to_apply > 0:
        details.append(("Спишется с баланса", f"{breakdown.balance_to_apply} ₽"))
    if breakdown.external_amount_due > 0 and breakdown.balance_to_apply > 0:
        details.append(("Доплата онлайн", f"{breakdown.external_amount_due} ₽"))

    return render(
        request,
        "clubs/my_plan_payment_preview.html",
        {
            "club": member.club,
            "club_offer_published": club_has_published_offer(member.club),
            "is_club_panel": True,
            "title": "Членский взнос клуба",
            "description": (
                f"Онлайн-оплата членского взноса. Получатель платежа — {member.club.name}."
            ),
            "amount": fee.amount,
            "amount_value": f"{fee.amount:.2f}",
            "item_id": fee.id,
            "payment_next_url": next_url,
            "details": details,
            "process_url": reverse("clubs:my_fees_pay"),
            "payment_type": "club_fee",
            "balance_available": breakdown.balance_available,
            "balance_to_apply": breakdown.balance_to_apply,
            "external_amount_due": breakdown.external_amount_due,
        },
    )


@login_required
@require_GET
def my_fees_return(request: HttpRequest) -> HttpResponse:
    """Return URL после оплаты членского взноса игроком."""
    session_pending = request.session.get("club_fee_payment_pending") or {}
    balance_transaction_id = int(
        str(session_pending.get("balance_transaction_id") or "0") or 0
    )
    balance_transaction = (
        ClubMemberBalanceTransaction.objects.filter(pk=balance_transaction_id).first()
        if balance_transaction_id
        else None
    )
    payment_id = str(
        session_pending.get("payment_id")
        or request.GET.get("payment_id")
        or request.GET.get("orderId")
        or request.GET.get("paymentId")
        or ""
    ).strip()
    if not payment_id:
        messages.warning(request, "ID платежа не найден.")
        return redirect("clubs:my_plan")

    pending = ClubFeePaymentPending.objects.filter(payment_id=payment_id).first()
    if not pending:
        messages.warning(request, "Платёж не найден или уже обработан.")
        cancel_reserved_balance(balance_transaction)
        request.session.pop("club_fee_payment_pending", None)
        request.session.modified = True
        return redirect("clubs:my_plan")

    member = _get_current_club_member(request)
    if not member or member.id != pending.member_id:
        messages.error(request, "Клуб не найден.")
        cancel_reserved_balance(balance_transaction)
        request.session.pop("club_fee_payment_pending", None)
        request.session.modified = True
        return redirect("clubs:register_choice")

    payment_settings = _get_club_payment_settings(pending.club)
    if payment_settings is None:
        messages.error(request, "Платёжные реквизиты клуба не настроены.")
        cancel_reserved_balance(balance_transaction)
        request.session.pop("club_fee_payment_pending", None)
        request.session.modified = True
        return redirect("clubs:my_plan")
    enable_autopay = str(session_pending.get("enable_autopay") or "").strip() == "1"
    try:
        secret = decrypt_secret(payment_settings.payment_api_key)
    except Exception as exc:
        logger.warning("Не удалось расшифровать секретный ключ при return: %s", exc)
        messages.error(request, "Ошибка проверки платежа.")
        cancel_reserved_balance(balance_transaction)
        request.session.pop("club_fee_payment_pending", None)
        request.session.modified = True
        return redirect("clubs:my_plan")

    status = get_payment_status_with_credentials(
        payment_id,
        payment_settings.payment_shop_id,
        secret,
    )
    if status != "succeeded":
        cancel_reserved_balance(balance_transaction)
        messages.error(request, "Оплата не прошла. Попробуйте снова.")
        pending.delete()
        request.session.pop("club_fee_payment_pending", None)
        request.session.modified = True
        return redirect("clubs:my_plan")

    confirm_reserved_balance(balance_transaction)
    if not ClubFeePayment.objects.filter(payment_ref=payment_id).exists():
        ClubFeePayment.objects.create(
            club=pending.club,
            member=pending.member,
            fee=pending.fee,
            amount=pending.amount,
            period_label=pending.period_label,
            paid_at=timezone.now(),
            method=FeePaymentMethod.ONLINE,
            payment_ref=payment_id,
        )
        try:
            send_fee_paid_notification(
                pending.club,
                pending.member,
                pending.amount,
                pending.period_label,
            )
        except Exception:
            logger.exception("Ошибка отправки уведомления об оплате взноса")

    if enable_autopay:
        from apps.payments.models import SavedPaymentMethod

        details = get_payment_details_with_credentials(
            payment_id,
            payment_settings.payment_shop_id,
            secret,
        )
        if isinstance(details, dict):
            payment_method = details.get("payment_method") or {}
            payment_method_id = payment_method.get("id")
            if isinstance(payment_method_id, str) and payment_method_id:
                card = payment_method.get("card") or {}
                SavedPaymentMethod.objects.filter(
                    user=request.user,
                    club=pending.club,
                    is_default_for_club_fees=True,
                ).update(is_default_for_club_fees=False)
                existing_method = SavedPaymentMethod.objects.filter(
                    payment_method_id=payment_method_id
                ).first()
                SavedPaymentMethod.objects.update_or_create(
                    payment_method_id=payment_method_id,
                    defaults={
                        "user": request.user,
                        "club": pending.club,
                        "card_last4": str(card.get("last4") or "")[-4:],
                        "card_exp_month": str(card.get("expiry_month") or "")[:2],
                        "card_exp_year": str(card.get("expiry_year") or "")[:4],
                        "card_network": str(
                            card.get("card_type") or card.get("brand") or ""
                        ).strip(),
                        "is_active": True,
                        "is_default_for_subscriptions": bool(
                            existing_method
                            and existing_method.is_default_for_subscriptions
                        ),
                        "is_default_for_club_plans": bool(
                            existing_method
                            and existing_method.is_default_for_club_plans
                        ),
                        "is_default_for_club_fees": True,
                    },
                )

    from apps.payments.models import PaymentRecord

    PaymentRecord.objects.update_or_create(
        user=request.user,
        yookassa_payment_id=payment_id,
        defaults={
            "payment_type": PaymentRecord.PaymentType.CLUB_FEE,
            "item_id": str(pending.fee_id),
            "item_label": "Членский взнос клуба",
            "amount": pending.amount,
            "status": "succeeded",
            "is_recurring": False,
            "autopay_enabled": enable_autopay,
            "metadata": {
                "club_id": pending.club_id,
                "club_slug": pending.club.slug,
                "period_label": pending.period_label,
                "balance_amount": str(session_pending.get("balance_amount", "") or "0"),
                "external_amount": str(
                    pending.amount
                    - Decimal(str(session_pending.get("balance_amount", "0") or "0"))
                ),
                "total_amount": str(
                    session_pending.get("total_amount", "") or pending.amount
                ),
                "fee_payment_ref": payment_id,
            },
        },
    )

    pending.delete()
    request.session.pop("club_fee_payment_pending", None)
    request.session.modified = True
    record_club_consent(request, pending.club)
    messages.success(request, "Оплата членского взноса прошла успешно!")
    next_url = str(session_pending.get("next") or "").strip()
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)
    return redirect("clubs:my_plan")


@login_required
@require_POST
def my_fee_disable_autopay(request: HttpRequest) -> HttpResponse:
    """Отключает карту для автосписаний членского взноса клуба."""
    from apps.payments.models import SavedPaymentMethod

    member = _get_current_club_member(request)
    if not member:
        messages.info(request, "Вы не состоите в клубе.")
        return redirect("clubs:register_choice")

    payment_method = (
        SavedPaymentMethod.objects.filter(
            user=request.user,
            club=member.club,
            is_active=True,
            is_default_for_club_fees=True,
        )
        .order_by("-created_at")
        .first()
    )
    if payment_method is None:
        messages.info(
            request,
            "Для членского взноса нет подключённой карты автосписания.",
        )
    else:
        payment_method.deactivate_for_club_fees()
        messages.success(
            request,
            "Автосписание членского взноса отключено, карта отвязана.",
        )

    next_url = (request.POST.get("next") or "").strip()
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)
    return redirect("clubs:my_plan")


def _process_club_fee_webhook(
    payment_id: str,
    payment_obj: dict[str, Any],
    club: Club,
    metadata: dict[str, Any],
) -> None:
    """Обрабатывает успешную оплату клубного взноса."""
    fee_id = metadata.get("fee_id")
    member_id = metadata.get("member_id")
    period_label = metadata.get("period_label")

    if not all([fee_id, member_id, period_label]):
        logger.warning(
            "Webhook: отсутствуют метаданные club_fee для платежа %s",
            payment_id,
        )
        return

    try:
        fee = ClubMembershipFee.objects.get(id=int(str(fee_id)), club=club)
        member = ClubMember.objects.get(id=int(str(member_id)), club=club)
    except Exception as exc:
        logger.warning(
            "Webhook: объекты club_fee не найдены для платежа %s: %s",
            payment_id,
            exc,
        )
        return

    amount_obj = payment_obj.get("amount") or {}
    amount_value = amount_obj.get("value", "0")
    is_recurring = str(metadata.get("autopay") or "").strip() == "1"
    enable_autopay = str(metadata.get("enable_autopay") or "").strip() == "1"
    autopay_enabled = enable_autopay or is_recurring
    try:
        amount = Decimal(str(amount_value))
    except Exception:
        amount = fee.amount

    fee_payment_created = False
    if not ClubFeePayment.objects.filter(payment_ref=payment_id).exists():
        ClubFeePayment.objects.create(
            club=club,
            member=member,
            fee=fee,
            amount=amount,
            period_label=str(period_label),
            paid_at=timezone.now(),
            method=FeePaymentMethod.ONLINE,
            payment_ref=payment_id,
        )
        fee_payment_created = True
        logger.info("Webhook: создан ClubFeePayment для payment_id=%s", payment_id)

    existing_record = PaymentRecord.objects.filter(
        user=member.user,
        yookassa_payment_id=payment_id,
    ).first()
    record_metadata = dict(existing_record.metadata if existing_record else {})
    record_metadata.update(
        {
            "club_id": club.id,
            "club_slug": club.slug,
            "period_label": str(period_label),
            "fee_payment_ref": payment_id,
        }
    )
    PaymentRecord.objects.update_or_create(
        user=member.user,
        yookassa_payment_id=payment_id,
        defaults={
            "payment_type": PaymentRecord.PaymentType.CLUB_FEE,
            "item_id": str(fee.id),
            "item_label": "Членский взнос клуба",
            "amount": amount,
            "status": "succeeded",
            "is_recurring": is_recurring,
            "autopay_enabled": autopay_enabled,
            "metadata": record_metadata,
        },
    )

    if enable_autopay:
        payment_method = payment_obj.get("payment_method") or {}
        payment_method_id = payment_method.get("id")
        if isinstance(payment_method_id, str) and payment_method_id:
            card = payment_method.get("card") or {}
            SavedPaymentMethod.objects.filter(
                user=member.user,
                club=club,
                is_default_for_club_fees=True,
            ).update(is_default_for_club_fees=False)
            existing_method = SavedPaymentMethod.objects.filter(
                payment_method_id=payment_method_id
            ).first()
            SavedPaymentMethod.objects.update_or_create(
                payment_method_id=payment_method_id,
                defaults={
                    "user": member.user,
                    "club": club,
                    "card_last4": str(card.get("last4") or "")[-4:],
                    "card_exp_month": str(card.get("expiry_month") or "")[:2],
                    "card_exp_year": str(card.get("expiry_year") or "")[:4],
                    "card_network": str(
                        card.get("card_type") or card.get("brand") or ""
                    ).strip(),
                    "is_active": True,
                    "is_default_for_subscriptions": bool(
                        existing_method and existing_method.is_default_for_subscriptions
                    ),
                    "is_default_for_club_plans": bool(
                        existing_method and existing_method.is_default_for_club_plans
                    ),
                    "is_default_for_club_fees": True,
                },
            )

    if fee_payment_created:
        try:
            send_fee_paid_notification(club, member, amount, str(period_label))
        except Exception:
            logger.exception(
                "Webhook: ошибка уведомления об оплате взноса payment_id=%s",
                payment_id,
            )


def _process_club_plan_webhook(
    payment_id: str,
    payment_obj: dict[str, Any],
    club: Club,
    metadata: dict[str, Any],
) -> None:
    """Обрабатывает успешную оплату клубного тарифа игрока."""
    plan_id = metadata.get("club_plan_id")
    user_id = metadata.get("user_id")
    is_recurring = str(metadata.get("autopay") or "").strip() == "1"
    enable_autopay = str(metadata.get("enable_autopay") or "").strip() == "1"
    autopay_enabled = enable_autopay or is_recurring

    if not all([plan_id, user_id]):
        logger.warning(
            "Webhook: отсутствуют метаданные club_plan для платежа %s",
            payment_id,
        )
        return

    from apps.core.models import LegalAcceptanceLog
    from apps.legal.utils import get_legal_document_version
    from apps.payments.models import PaymentRecord, SavedPaymentMethod
    from apps.users.models import User

    existing_payment_record = PaymentRecord.objects.filter(
        user_id=user_id,
        yookassa_payment_id=payment_id,
    ).first()
    if (
        existing_payment_record is not None
        and existing_payment_record.status == "succeeded"
    ):
        return

    try:
        plan = ClubPlayerPlan.objects.select_related("club").get(
            id=int(str(plan_id)),
            club=club,
        )
        member = ClubMember.objects.select_related("club").get(
            club=club,
            user_id=int(str(user_id)),
            status=ClubMemberStatus.ACTIVE,
        )
        user = User.objects.get(id=int(str(user_id)))
    except Exception as exc:
        logger.warning(
            "Webhook: объекты club_plan не найдены для платежа %s: %s",
            payment_id,
            exc,
        )
        return

    if enable_autopay:
        payment_method = payment_obj.get("payment_method") or {}
        payment_method_id = payment_method.get("id")
        if isinstance(payment_method_id, str) and payment_method_id:
            card = payment_method.get("card") or {}
            SavedPaymentMethod.objects.filter(
                user=user,
                club=club,
                is_default_for_club_plans=True,
            ).update(is_default_for_club_plans=False)
            existing_method = SavedPaymentMethod.objects.filter(
                payment_method_id=payment_method_id
            ).first()
            SavedPaymentMethod.objects.update_or_create(
                payment_method_id=payment_method_id,
                defaults={
                    "user": user,
                    "club": club,
                    "card_last4": str(card.get("last4") or "")[-4:],
                    "card_exp_month": str(card.get("expiry_month") or "")[:2],
                    "card_exp_year": str(card.get("expiry_year") or "")[:4],
                    "card_network": str(
                        card.get("card_type") or card.get("brand") or ""
                    ).strip(),
                    "is_active": True,
                    "is_default_for_subscriptions": bool(
                        existing_method and existing_method.is_default_for_subscriptions
                    ),
                    "is_default_for_club_plans": True,
                    "is_default_for_club_fees": bool(
                        existing_method and existing_method.is_default_for_club_fees
                    ),
                },
            )

    PaymentRecord.objects.update_or_create(
        user=user,
        yookassa_payment_id=payment_id,
        defaults={
            "payment_type": PaymentRecord.PaymentType.CLUB_PLAN,
            "item_id": str(plan.id),
            "item_label": f"{club.name}: {plan.name}",
            "amount": plan.monthly_fee,
            "status": "succeeded",
            "is_recurring": is_recurring,
            "autopay_enabled": autopay_enabled,
            "metadata": {
                **(existing_payment_record.metadata if existing_payment_record else {}),
                "club_id": club.id,
                "club_slug": club.slug,
            },
        },
    )

    LegalAcceptanceLog.objects.get_or_create(
        user=user,
        document_slug="offer",
        document_version=get_legal_document_version("offer"),
        source="payment",
        metadata={
            "payment_type": "club_plan",
            "item_id": str(plan.id),
            "payment_id": payment_id,
            "club_id": club.id,
        },
        defaults={
            "ip_address": None,
            "user_agent": "yookassa-webhook",
        },
    )

    purchase_member_plan(
        member,
        plan,
        assigned_by=user,
        change_reason="Оплата клубного тарифа участником (webhook)",
        auto_renew=autopay_enabled,
    )
    logger.info("Webhook: активирован клубный тариф для payment_id=%s", payment_id)


@csrf_exempt
@require_POST
def club_payment_webhook(request: HttpRequest) -> HttpResponse:
    """Единый webhook от ЮKassa для платежей клубов."""
    try:
        body = json.loads(request.body.decode("utf-8"))
    except Exception as exc:
        logger.warning("Webhook: не удалось распарсить body: %s", exc)
        return JsonResponse({"status": "error"}, status=400)

    event_type = body.get("event")
    if event_type != "payment.succeeded":
        return JsonResponse({"status": "ok"}, status=200)

    payment_obj = body.get("object") or {}
    payment_id = payment_obj.get("id")
    status = payment_obj.get("status")
    metadata = payment_obj.get("metadata") or {}

    if not payment_id or status != "succeeded":
        return JsonResponse({"status": "ok"}, status=200)

    club_id = metadata.get("club_id")
    payment_type = str(metadata.get("payment_type") or "").strip()
    if not club_id:
        logger.warning(
            "Webhook: отсутствует club_id для платежа %s",
            payment_id,
        )
        return JsonResponse({"status": "ok"}, status=200)

    try:
        club = Club.objects.get(id=int(str(club_id)))
        fee = _get_club_payment_settings(club)
        if fee is None:
            logger.warning(
                "Webhook: у клуба %s не найдены платёжные настройки YooKassa",
                club.id,
            )
            return JsonResponse({"status": "ok"}, status=200)
        secret = decrypt_secret(fee.payment_api_key)
        payment_details = get_payment_details_with_credentials(
            payment_id,
            fee.payment_shop_id,
            secret,
        )
    except Exception as exc:
        logger.warning(
            "Webhook: не удалось верифицировать платёж %s через API YooKassa: %s",
            payment_id,
            exc,
        )
        return JsonResponse({"status": "ok"}, status=200)

    if not isinstance(payment_details, dict):
        return JsonResponse({"status": "ok"}, status=200)
    if payment_details.get("status") != "succeeded":
        return JsonResponse({"status": "ok"}, status=200)

    resolved_metadata = payment_details.get("metadata") or metadata
    resolved_type = str(
        resolved_metadata.get("payment_type") or payment_type or ""
    ).strip()

    if resolved_type == PaymentRecord.PaymentType.CLUB_PLAN:
        _process_club_plan_webhook(
            payment_id,
            payment_details,
            club,
            resolved_metadata,
        )
    elif resolved_type == PaymentRecord.PaymentType.CLUB_FEE:
        _process_club_fee_webhook(
            payment_id,
            payment_details,
            club,
            resolved_metadata,
        )
    else:
        logger.warning(
            "Webhook: неподдерживаемый тип клубного платежа %s для payment_id=%s",
            resolved_type,
            payment_id,
        )

    return JsonResponse({"status": "ok"}, status=200)


@csrf_exempt
@require_POST
def club_fee_webhook(request: HttpRequest) -> HttpResponse:
    """Совместимый alias старого webhook URL для взносов клуба."""
    return club_payment_webhook(request)
