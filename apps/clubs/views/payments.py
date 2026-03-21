import json
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.payments.yookassa_client import (
    create_payment_with_credentials,
    get_payment_details_with_credentials,
    get_payment_status_with_credentials,
    test_yookassa_credentials,
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
    ClubMember,
    ClubMembershipFee,
    ClubMemberStatus,
    ClubPlayerPlan,
    FeePaymentMethod,
    FeePeriod,
)
from ..notifications import send_fee_paid_notification
from ..payment_utils import decrypt_secret, encrypt_secret
from ..plan_services import purchase_member_plan
from ..services import (
    club_is_operational,
    get_current_period_label,
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
    return redirect("clubs:my_plan")


@login_required
@require_GET
def my_payments(request: HttpRequest) -> HttpResponse:
    """История клубных платежей игрока с деталями по каждому платежу."""
    member = _get_current_club_member(request)
    if not member:
        messages.info(request, "Вы не состоите в клубе.")
        return redirect("clubs:register_choice")

    from apps.payments.models import PaymentRecord

    club_plan_payments = list(
        PaymentRecord.objects.filter(
            user=request.user,
            payment_type=PaymentRecord.PaymentType.CLUB_PLAN,
            metadata__club_id=member.club_id,
            status="succeeded",
        ).order_by("-paid_at")
    )
    fee_payments = list(
        ClubFeePayment.objects.filter(member=member)
        .select_related("fee")
        .order_by("-paid_at")
    )

    payment_rows: list[dict[str, Any]] = []
    for payment in club_plan_payments:
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
                "status": "Успешно",
                "details": {
                    "Тип платежа": "Клубный тариф",
                    "Наименование": payment.item_label or "Клубный тариф",
                    "Дата": payment.paid_at.strftime("%d.%m.%Y %H:%M"),
                    "Сумма": f"{payment.amount} {payment.currency or 'RUB'}",
                    "Способ": (
                        "Автосписание" if payment.is_recurring else "Онлайн-оплата"
                    ),
                    "Автосписание включено": (
                        "Да" if payment.autopay_enabled else "Нет"
                    ),
                    "Статус": "Успешно",
                    "ID платежа": payment.yookassa_payment_id or "Не указан",
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

    payment_rows.sort(key=lambda item: item["paid_at"], reverse=True)

    return render(
        request,
        "clubs/my_payments.html",
        {
            "club": member.club,
            "is_club_panel": True,
            "payments": payment_rows,
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
    fee = (
        ClubMembershipFee.objects.filter(club=club, is_active=True)
        .order_by("-id")
        .first()
    )
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
    return render(
        request,
        "clubs/fees_settings.html",
        {"club": club, "form": form, "fee": fee, "is_club_panel": True},
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

    fee = (
        ClubMembershipFee.objects.filter(club=club, is_active=True)
        .order_by("-id")
        .first()
    ) or ClubMembershipFee.objects.filter(club=club).order_by("-id").first()
    connection_test_ok: bool | None = None
    connection_test_message = ""
    if request.method == "POST":
        form = ClubPaymentSettingsForm(request.POST, instance=fee)
        if form.is_valid():
            new_secret = (form.cleaned_data.get("new_secret_key") or "").strip()
            action = (request.POST.get("action") or "save").strip()
            target_fee = form.save(commit=False)
            target_fee.club = club
            target_fee.amount = target_fee.amount or Decimal("0")
            target_fee.currency = target_fee.currency or "RUB"
            target_fee.period = target_fee.period or FeePeriod.MONTHLY
            target_fee.period_start_day = target_fee.period_start_day or 1

            if action == "test":
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

                if not target_fee.payment_shop_id or not secret:
                    connection_test_ok = False
                    connection_test_message = (
                        "Для теста связи укажите Shop ID и Secret Key YooKassa."
                    )
                    messages.error(request, connection_test_message)
                else:
                    connection_test_ok, connection_test_message = (
                        test_yookassa_credentials(
                            target_fee.payment_shop_id,
                            secret,
                        )
                    )
                    if connection_test_ok:
                        messages.success(request, connection_test_message)
                    else:
                        messages.error(request, connection_test_message)
                fee = target_fee
            else:
                fee = target_fee
                fee.save()
                if new_secret:
                    fee.payment_api_key = encrypt_secret(new_secret)
                    fee.save(update_fields=["payment_api_key"])
                messages.success(request, "Платёжные реквизиты клуба сохранены.")
                return redirect("clubs:payment_settings", slug=slug)
    else:
        form = ClubPaymentSettingsForm(instance=fee)

    return render(
        request,
        "clubs/payment_settings.html",
        {
            "club": club,
            "form": form,
            "fee": fee,
            "is_club_panel": True,
            "payment_connected": bool(
                fee
                and fee.payment_provider == ClubMembershipFee.PaymentProvider.YOOKASSA
                and fee.payment_shop_id
                and fee.payment_api_key
            ),
            "masked_shop_id": (
                f"{fee.payment_shop_id[:3]}***{fee.payment_shop_id[-2:]}"
                if fee and fee.payment_shop_id and len(fee.payment_shop_id) >= 5
                else (fee.payment_shop_id if fee and fee.payment_shop_id else "")
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
    payment_settings = _get_club_payment_settings(club)
    if not fee or payment_settings is None:
        messages.error(request, "Онлайн-оплата взносов не настроена в этом клубе.")
        return redirect("clubs:my_plan")

    next_url = (request.POST.get("next") or "").strip()
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

    try:
        secret = decrypt_secret(payment_settings.payment_api_key)
    except Exception as exc:
        logger.warning("Не удалось расшифровать секретный ключ клуба: %s", exc)
        messages.error(
            request,
            "Ошибка настройки оплаты. Обратитесь к администратору клуба.",
        )
        return redirect("clubs:my_plan")

    amount_str = f"{fee.amount:.2f}"
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
        logger.warning("Ошибка создания платежа взноса: %s", exc)
        messages.error(request, "Не удалось создать платёж. Проверьте настройки клуба.")
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
    payment_settings = _get_club_payment_settings(member.club)
    if not fee or payment_settings is None:
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

    return render(
        request,
        "clubs/my_plan_payment_preview.html",
        {
            "club": member.club,
            "is_club_panel": True,
            "title": "Членский взнос клуба",
            "description": (
                f"Оплата членского взноса через YooKassa клуба {member.club.name}."
            ),
            "amount": fee.amount,
            "amount_value": f"{fee.amount:.2f}",
            "item_id": fee.id,
            "payment_next_url": next_url,
            "details": details,
            "process_url": reverse("clubs:my_fees_pay"),
            "payment_type": "club_fee",
        },
    )


@login_required
@require_GET
def my_fees_return(request: HttpRequest) -> HttpResponse:
    """Return URL после оплаты членского взноса игроком."""
    session_pending = request.session.get("club_fee_payment_pending") or {}
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
        request.session.pop("club_fee_payment_pending", None)
        request.session.modified = True
        return redirect("clubs:my_plan")

    member = _get_current_club_member(request)
    if not member or member.id != pending.member_id:
        messages.error(request, "Клуб не найден.")
        request.session.pop("club_fee_payment_pending", None)
        request.session.modified = True
        return redirect("clubs:register_choice")

    payment_settings = _get_club_payment_settings(pending.club)
    if payment_settings is None:
        messages.error(request, "Платёжные реквизиты клуба не настроены.")
        request.session.pop("club_fee_payment_pending", None)
        request.session.modified = True
        return redirect("clubs:my_plan")
    enable_autopay = str(session_pending.get("enable_autopay") or "").strip() == "1"
    try:
        secret = decrypt_secret(payment_settings.payment_api_key)
    except Exception as exc:
        logger.warning("Не удалось расшифровать секретный ключ при return: %s", exc)
        messages.error(request, "Ошибка проверки платежа.")
        request.session.pop("club_fee_payment_pending", None)
        request.session.modified = True
        return redirect("clubs:my_plan")

    status = get_payment_status_with_credentials(
        payment_id,
        payment_settings.payment_shop_id,
        secret,
    )
    if status != "succeeded":
        messages.error(request, "Оплата не прошла. Попробуйте снова.")
        pending.delete()
        request.session.pop("club_fee_payment_pending", None)
        request.session.modified = True
        return redirect("clubs:my_plan")

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

    pending.delete()
    request.session.pop("club_fee_payment_pending", None)
    request.session.modified = True
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

    if ClubFeePayment.objects.filter(payment_ref=payment_id).exists():
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
    enable_autopay = str(metadata.get("enable_autopay") or "").strip() == "1"
    try:
        amount = Decimal(str(amount_value))
    except Exception:
        amount = fee.amount

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
    logger.info("Webhook: создан ClubFeePayment для payment_id=%s", payment_id)

    if enable_autopay:
        from apps.payments.models import SavedPaymentMethod

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
    enable_autopay = str(metadata.get("enable_autopay") or "").strip() == "1"

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

    if PaymentRecord.objects.filter(yookassa_payment_id=payment_id).exists():
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
            "payment_type": "club_plan",
            "item_id": str(plan.id),
            "item_label": f"{club.name}: {plan.name}",
            "amount": plan.monthly_fee,
            "status": "succeeded",
            "is_recurring": False,
            "autopay_enabled": enable_autopay,
            "metadata": {"club_id": club.id, "club_slug": club.slug},
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
        auto_renew=enable_autopay,
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

    if resolved_type == "club_plan":
        _process_club_plan_webhook(
            payment_id,
            payment_details,
            club,
            resolved_metadata,
        )
    else:
        _process_club_fee_webhook(
            payment_id,
            payment_details,
            club,
            resolved_metadata,
        )

    return JsonResponse({"status": "ok"}, status=200)


@csrf_exempt
@require_POST
def club_fee_webhook(request: HttpRequest) -> HttpResponse:
    """Совместимый alias старого webhook URL для взносов клуба."""
    return club_payment_webhook(request)
