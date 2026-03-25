from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.core.consent_utils import record_club_consent
from apps.core.models import UserTelegramLink
from apps.payments.yookassa_client import (
    create_payment,
    create_payment_with_credentials,
    get_payment_details_with_credentials,
    get_payment_status_with_credentials,
)

from ..finance_services import (
    calculate_balance_payment_breakdown,
    cancel_reserved_balance,
    confirm_reserved_balance,
    reserve_member_balance,
)
from ..forms import ClubNotificationConfigForm, ClubNotificationSettingsForm
from ..models import (
    Club,
    ClubMember,
    ClubMemberBalanceTransaction,
    ClubMemberRole,
    ClubMemberStatus,
    ClubNotificationConfig,
    ClubNotificationSettings,
    ClubPlan,
    ClubPlayerPlan,
    ClubSubscription,
    ClubSubscriptionPaymentPending,
    ClubSubscriptionPeriod,
    ClubSubscriptionStatus,
)
from ..payment_utils import decrypt_secret
from ..plan_services import (
    cancel_member_plan_auto_renew,
    enable_member_plan_auto_renew,
    get_member_active_plan,
    purchase_member_plan,
)
from ..services import (
    club_has_published_offer,
    get_club_current_subscription,
    get_platform_plan,
    get_platform_plans,
    user_can_edit_club_settings,
)
from .helpers import _get_club_payment_settings, _get_current_club_member, logger


def _get_plan_prices_for_subscription() -> dict[str, dict[str, Decimal]]:
    """Словарь цен для subscription_pay."""
    result: dict[str, dict[str, Decimal]] = {}
    for plan_slug in ("start", "basic", "pro"):
        platform_plan = get_platform_plan(plan_slug)
        if platform_plan:
            result[plan_slug] = {
                "monthly": platform_plan.price_monthly,
                "yearly": platform_plan.price_yearly,
            }
        else:
            result[plan_slug] = {
                "monthly": Decimal("0"),
                "yearly": Decimal("0"),
            }
    return result


def _get_user_telegram_bot_state(user) -> tuple[bool, str]:
    """Возвращает статус подключения Telegram-бота и username бота."""
    is_connected = False
    bot_username = ""

    try:
        link = user.telegram_link
        is_connected = bool(link.user_bot_chat_id)
    except UserTelegramLink.DoesNotExist:
        is_connected = False

    if not is_connected:
        return False, ""

    try:
        from apps.telegram_bot import services as bot_services

        bot_username = bot_services.get_bot_username() or ""
    except Exception:
        bot_username = ""

    return True, bot_username


@login_required
@require_GET
def my_plan(request: HttpRequest) -> HttpResponse:
    """Показывает текущий тариф игрока и остатки лимитов."""
    return redirect("clubs:my_finance")


@login_required
@require_GET
def my_plan_change(request: HttpRequest) -> HttpResponse:
    """Показывает игроку список клубных тарифов для самостоятельной оплаты."""
    member = _get_current_club_member(request)
    if not member:
        messages.info(request, "Вы не состоите в клубе.")
        return redirect("clubs:register_choice")

    if not member.club.use_player_plans:
        messages.info(
            request,
            "Клуб отключил систему тарифов. Выбор и оплата тарифов сейчас недоступны.",
        )
        return redirect("clubs:my_finance")

    active_plans = ClubPlayerPlan.objects.filter(
        club=member.club,
        is_active=True,
    ).order_by("sort_order", "name")
    current_member_plan = get_member_active_plan(member)
    balance_breakdowns = {
        plan.id: calculate_balance_payment_breakdown(member, plan.monthly_fee)
        for plan in active_plans
    }

    return render(
        request,
        "clubs/my_plan_change.html",
        {
            "club": member.club,
            "is_club_panel": True,
            "active_plans": active_plans,
            "current_member_plan": current_member_plan,
            "balance_breakdowns": balance_breakdowns,
        },
    )


@login_required
@require_GET
def my_plan_payment_preview(request: HttpRequest, plan_id: int) -> HttpResponse:
    """Предпросмотр оплаты клубного тарифа через кассу текущего клуба."""
    member = _get_current_club_member(request)
    if not member:
        messages.info(request, "Вы не состоите в клубе.")
        return redirect("clubs:register_choice")

    if not member.club.use_player_plans:
        messages.info(
            request, "Клуб отключил систему тарифов. Оплата тарифов сейчас недоступна."
        )
        return redirect("clubs:my_finance")

    plan = get_object_or_404(
        ClubPlayerPlan.objects.select_related("club"),
        pk=plan_id,
        club=member.club,
        is_active=True,
    )
    breakdown = calculate_balance_payment_breakdown(member, plan.monthly_fee)
    payment_settings = _get_club_payment_settings(member.club)
    if payment_settings is None and breakdown.external_amount_due > 0:
        messages.error(
            request,
            "Клуб ещё не подключил свою YooKassa. Оплата тарифов временно недоступна.",
        )
        return redirect("clubs:my_plan_change")

    next_url = (request.GET.get("next") or "").strip()
    details = [
        ("Клуб", member.club.name),
        ("Тариф", plan.name),
        ("Период", f"{plan.duration_days} дн."),
    ]
    if plan.has_unlimited_registrations:
        details.append(("Регистрации", "Безлимит"))
    else:
        details.append(("Регистрации", f"{plan.max_tournaments_per_month} в месяц"))
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
            "title": f"Тариф клуба: {plan.name}",
            "description": (
                f"Онлайн-оплата тарифа «{plan.name}». "
                f"Получатель платежа — {member.club.name}."
            ),
            "amount": plan.monthly_fee,
            "amount_value": f"{plan.monthly_fee:.2f}",
            "item_id": plan.id,
            "payment_next_url": next_url,
            "details": details,
            "process_url": reverse("clubs:my_plan_payment_process"),
            "payment_type": "club_plan",
            "balance_available": breakdown.balance_available,
            "balance_to_apply": breakdown.balance_to_apply,
            "external_amount_due": breakdown.external_amount_due,
        },
    )


@login_required
@require_POST
def my_plan_payment_process(request: HttpRequest) -> HttpResponse:
    """Создаёт платёж клубного тарифа через реквизиты текущего клуба."""
    member = _get_current_club_member(request)
    if not member:
        messages.info(request, "Вы не состоите в клубе.")
        return redirect("clubs:register_choice")

    if not member.club.use_player_plans:
        messages.info(
            request, "Клуб отключил систему тарифов. Оплата тарифов сейчас недоступна."
        )
        return redirect("clubs:my_finance")

    raw_offer = str(request.POST.get("offer_accepted", "")).strip().lower()
    if raw_offer not in {"1", "true", "on", "yes"}:
        messages.error(
            request,
            "Для продолжения оплаты необходимо подтвердить согласие с условиями Публичной оферты.",
        )
        return redirect(
            "clubs:my_plan_payment_preview",
            plan_id=request.POST.get("id") or 0,
        )

    if not club_has_published_offer(member.club):
        messages.error(
            request,
            "Оплата недоступна: клуб не опубликовал оферту. Обратитесь к администратору клуба.",
        )
        return redirect(
            "clubs:my_plan_payment_preview",
            plan_id=request.POST.get("id") or 0,
        )
    raw_club_offer = str(request.POST.get("club_offer_accepted", "")).strip().lower()
    if raw_club_offer not in {"1", "true", "on", "yes"}:
        messages.error(
            request,
            "Для оплаты необходимо принять оферту этого клуба.",
        )
        return redirect(
            "clubs:my_plan_payment_preview",
            plan_id=request.POST.get("id") or 0,
        )

    try:
        plan_id = int(str(request.POST.get("id") or "").strip())
    except (TypeError, ValueError):
        messages.error(request, "Не удалось определить тариф для оплаты.")
        return redirect("clubs:my_plan_change")

    plan = get_object_or_404(
        ClubPlayerPlan.objects.select_related("club"),
        pk=plan_id,
        club=member.club,
        is_active=True,
    )
    breakdown = calculate_balance_payment_breakdown(member, plan.monthly_fee)
    payment_settings = _get_club_payment_settings(member.club)
    if payment_settings is None and breakdown.external_amount_due > 0:
        messages.error(
            request,
            "Клуб ещё не подключил свою YooKassa. Оплата тарифов временно недоступна.",
        )
        return redirect("clubs:my_plan_change")

    raw_autopay = str(request.POST.get("enable_autopay", "")).strip().lower()
    enable_autopay = raw_autopay in {"1", "true", "on", "yes"}
    next_url = (request.POST.get("next") or "").strip()
    balance_transaction = None
    if breakdown.balance_to_apply > 0:
        try:
            balance_transaction = reserve_member_balance(
                member,
                breakdown.balance_to_apply,
                source=ClubMemberBalanceTransaction.Source.CLUB_PLAN_PAYMENT,
                description=f"Оплата тарифа клуба «{plan.name}»",
                reference=f"club-plan:{plan.id}",
                metadata={"plan_id": plan.id, "club_id": member.club_id},
            )
        except ValueError:
            messages.error(
                request,
                "Не удалось зарезервировать средства с баланса. Обновите страницу и попробуйте снова.",
            )
            return redirect("clubs:my_plan_payment_preview", plan_id=plan.id)

    if breakdown.external_amount_due <= 0:
        from apps.payments.models import PaymentRecord

        confirm_reserved_balance(balance_transaction)
        purchase_member_plan(
            member,
            plan,
            assigned_by=request.user,
            change_reason="Оплата клубного тарифа с баланса участником",
            auto_renew=False,
        )
        PaymentRecord.objects.create(
            user=request.user,
            payment_type=PaymentRecord.PaymentType.CLUB_PLAN,
            item_id=str(plan.id),
            item_label=f"{member.club.name}: {plan.name}",
            amount=plan.monthly_fee,
            status="succeeded",
            is_recurring=False,
            autopay_enabled=False,
            yookassa_payment_id=(
                f"balance-club-plan-{member.id}-{int(timezone.now().timestamp())}"
            ),
            metadata={
                "club_id": member.club_id,
                "club_slug": member.club.slug,
                "balance_amount": f"{plan.monthly_fee:.2f}",
                "external_amount": "0.00",
                "total_amount": f"{plan.monthly_fee:.2f}",
            },
        )
        record_club_consent(request, member.club)
        messages.success(request, f"Клубный тариф «{plan.name}» успешно оформлен.")
        return redirect("clubs:my_finance")

    assert payment_settings is not None
    try:
        secret = decrypt_secret(payment_settings.payment_api_key)
    except Exception as exc:
        cancel_reserved_balance(balance_transaction)
        logger.warning(
            "Не удалось расшифровать ключ YooKassa клуба для оплаты тарифа: %s",
            exc,
        )
        messages.error(request, "Ошибка платёжных настроек клуба.")
        return redirect("clubs:my_plan_change")

    amount_str = f"{breakdown.external_amount_due:.2f}"
    description = f"Клубный тариф {member.club.name}: {plan.name}"
    return_url = request.build_absolute_uri(reverse("clubs:my_plan_payment_return"))

    metadata = {
        "payment_type": "club_plan",
        "club_id": member.club_id,
        "club_slug": member.club.slug,
        "club_plan_id": plan.id,
        "user_id": request.user.pk,
        "next": next_url,
    }
    if enable_autopay:
        metadata["enable_autopay"] = "1"

    receipt_email = (request.user.email or "").strip()
    if not receipt_email or "@" not in receipt_email:
        receipt_email = "info@tennisfan.ru"

    try:
        payment_id, confirmation_url = create_payment_with_credentials(
            shop_id=payment_settings.payment_shop_id,
            secret_key=secret,
            amount=amount_str,
            return_url=return_url,
            description=description[:128],
            metadata=metadata,
            customer_email=receipt_email,
            save_payment_method=enable_autopay,
        )
    except (ValueError, RuntimeError) as exc:
        cancel_reserved_balance(balance_transaction)
        logger.warning("Ошибка создания оплаты клубного тарифа: %s", exc)
        messages.error(
            request,
            "Не удалось создать платёж клубного тарифа. Проверьте настройки клуба и попробуйте снова.",
        )
        return redirect("clubs:my_plan_payment_preview", plan_id=plan.id)
    except Exception as exc:
        # Сетевые/TLS сбои requests и прочие ошибки — иначе резерв баланса не снимается.
        cancel_reserved_balance(balance_transaction)
        logger.exception(
            "Неожиданная ошибка при создании оплаты клубного тарифа: %s", exc
        )
        messages.error(
            request,
            "Не удалось связаться с платёжным шлюзом. Средства с баланса возвращены — попробуйте снова.",
        )
        return redirect("clubs:my_plan_payment_preview", plan_id=plan.id)

    request.session["club_plan_payment_pending"] = {
        "payment_id": payment_id,
        "plan_id": plan.id,
        "club_slug": member.club.slug,
        "enable_autopay": "1" if enable_autopay else "",
        "next": next_url,
        "amount": amount_str,
        "balance_transaction_id": balance_transaction.id if balance_transaction else "",
        "balance_amount": f"{breakdown.balance_to_apply:.2f}",
        "total_amount": f"{plan.monthly_fee:.2f}",
    }
    request.session.modified = True
    return redirect(confirmation_url)


@login_required
@require_GET
def my_plan_payment_return(request: HttpRequest) -> HttpResponse:
    """Обрабатывает возврат после оплаты клубного тарифа через YooKassa клуба."""
    pending = request.session.get("club_plan_payment_pending")
    if not pending:
        messages.warning(
            request,
            "Сессия оплаты клубного тарифа истекла или уже обработана.",
        )
        return redirect("clubs:my_plan")

    club_slug = str(pending.get("club_slug") or "").strip()
    plan_id = pending.get("plan_id")
    payment_id = str(pending.get("payment_id") or "").strip()
    enable_autopay = str(pending.get("enable_autopay") or "").strip() == "1"
    balance_transaction_id = int(str(pending.get("balance_transaction_id") or "0") or 0)
    balance_transaction = (
        ClubMemberBalanceTransaction.objects.filter(pk=balance_transaction_id).first()
        if balance_transaction_id
        else None
    )

    member = (
        ClubMember.objects.select_related("club")
        .filter(
            user=request.user,
            club__slug=club_slug,
            status=ClubMemberStatus.ACTIVE,
        )
        .first()
    )
    if member is None:
        cancel_reserved_balance(balance_transaction)
        del request.session["club_plan_payment_pending"]
        request.session.modified = True
        messages.error(request, "Не удалось определить клуб для оплаченного тарифа.")
        return redirect("clubs:register_choice")

    payment_settings = _get_club_payment_settings(member.club)
    if payment_settings is None:
        cancel_reserved_balance(balance_transaction)
        del request.session["club_plan_payment_pending"]
        request.session.modified = True
        messages.error(request, "Платёжные реквизиты клуба не настроены.")
        return redirect("clubs:my_plan_change")

    try:
        secret = decrypt_secret(payment_settings.payment_api_key)
    except Exception as exc:
        logger.warning(
            "Не удалось расшифровать ключ YooKassa клуба при возврате оплаты тарифа: %s",
            exc,
        )
        cancel_reserved_balance(balance_transaction)
        del request.session["club_plan_payment_pending"]
        request.session.modified = True
        messages.error(request, "Ошибка проверки платежа.")
        return redirect("clubs:my_plan_change")

    status = (
        get_payment_status_with_credentials(
            payment_id,
            payment_settings.payment_shop_id,
            secret,
        )
        if payment_id
        else None
    )
    if status != "succeeded":
        cancel_reserved_balance(balance_transaction)
        del request.session["club_plan_payment_pending"]
        request.session.modified = True
        messages.error(
            request,
            "Оплата клубного тарифа не была завершена или отменена.",
        )
        return redirect("clubs:my_plan_change")

    plan = get_object_or_404(
        ClubPlayerPlan.objects.select_related("club"),
        pk=plan_id,
        club=member.club,
    )
    confirm_reserved_balance(balance_transaction)

    if enable_autopay and payment_id:
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
                    club=member.club,
                    is_default_for_club_plans=True,
                ).update(is_default_for_club_plans=False)
                existing_method = SavedPaymentMethod.objects.filter(
                    payment_method_id=payment_method_id
                ).first()
                SavedPaymentMethod.objects.update_or_create(
                    payment_method_id=payment_method_id,
                    defaults={
                        "user": request.user,
                        "club": member.club,
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
                        "is_default_for_club_plans": True,
                        "is_default_for_club_fees": bool(
                            existing_method and existing_method.is_default_for_club_fees
                        ),
                    },
                )

    from apps.core.models import LegalAcceptanceLog
    from apps.legal.utils import get_legal_document_version
    from apps.payments.models import PaymentRecord

    LegalAcceptanceLog.objects.create(
        user=request.user,
        document_slug="offer",
        document_version=get_legal_document_version("offer"),
        source="payment",
        ip_address=(
            request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
            or request.META.get("REMOTE_ADDR", "").strip()
            or None
        ),
        user_agent=request.META.get("HTTP_USER_AGENT", "").strip(),
        metadata={
            "payment_type": "club_plan",
            "item_id": str(plan.id),
            "payment_id": payment_id,
            "club_id": member.club_id,
        },
    )
    PaymentRecord.objects.update_or_create(
        user=request.user,
        yookassa_payment_id=payment_id,
        defaults={
            "payment_type": PaymentRecord.PaymentType.CLUB_PLAN,
            "item_id": str(plan.id),
            "item_label": f"{member.club.name}: {plan.name}",
            "amount": plan.monthly_fee,
            "status": "succeeded",
            "is_recurring": False,
            "autopay_enabled": enable_autopay,
            "metadata": {
                "club_id": member.club_id,
                "club_slug": member.club.slug,
                "balance_amount": str(pending.get("balance_amount", "") or "0"),
                "external_amount": str(pending.get("amount", "") or "0"),
                "total_amount": str(
                    pending.get("total_amount", "") or f"{plan.monthly_fee:.2f}"
                ),
            },
        },
    )

    purchase_member_plan(
        member,
        plan,
        assigned_by=request.user,
        change_reason="Оплата клубного тарифа участником",
        auto_renew=enable_autopay,
    )
    record_club_consent(request, member.club)
    request.session["current_club_slug"] = member.club.slug
    del request.session["club_plan_payment_pending"]
    request.session.modified = True
    messages.success(request, f"Клубный тариф «{plan.name}» успешно оформлен.")

    next_url = str(pending.get("next") or "").strip()
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)
    return redirect("clubs:my_plan")


@login_required
@require_POST
def my_plan_cancel(request: HttpRequest) -> HttpResponse:
    """Отключает автопродление текущего клубного тарифа игрока."""
    member = _get_current_club_member(request)
    if not member:
        messages.info(request, "Вы не состоите в клубе.")
        return redirect("clubs:register_choice")

    member_plan = cancel_member_plan_auto_renew(member)
    if member_plan is None:
        messages.info(
            request,
            "Для текущего тарифа автопродление не используется или тариф назначен клубом вручную.",
        )
    else:
        until_text = (
            member_plan.ended_at.strftime("%d.%m.%Y")
            if member_plan.ended_at
            else "конца периода"
        )
        messages.success(
            request,
            f"Автопродление клубного тарифа отключено. Доступ сохранится до {until_text}.",
        )
    return redirect("clubs:my_plan")


@login_required
@require_POST
def my_plan_disable_autopay(request: HttpRequest) -> HttpResponse:
    """Отключает карту для автопродления клубного тарифа."""
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
            is_default_for_club_plans=True,
        )
        .order_by("-created_at")
        .first()
    )
    if payment_method is None:
        messages.info(
            request, "Для клубного тарифа нет подключённой карты автопродления."
        )
    else:
        payment_method.deactivate_for_club_plans()
        messages.success(
            request,
            "Автосписание клубного тарифа отключено, карта отвязана от продления клуба.",
        )
    return redirect("clubs:my_plan")


@login_required
@require_POST
def my_plan_enable_auto_renew(request: HttpRequest) -> HttpResponse:
    """Включает автопродление активного клубного тарифа при наличии карты."""
    from apps.payments.models import SavedPaymentMethod

    member = _get_current_club_member(request)
    if not member:
        messages.info(request, "Вы не состоите в клубе.")
        return redirect("clubs:register_choice")

    member_plan = get_member_active_plan(member)
    if member_plan is None or member_plan.ended_at is None:
        messages.info(
            request,
            "Для текущего тарифа автопродление недоступно.",
        )
        return redirect("clubs:my_plan")

    payment_method = (
        SavedPaymentMethod.objects.filter(
            user=request.user,
            club=member.club,
            is_active=True,
            is_default_for_club_plans=True,
        )
        .order_by("-created_at")
        .first()
    )
    if payment_method is None:
        messages.error(
            request,
            "Чтобы включить автопродление, сначала подключите карту для автосписания тарифа.",
        )
        return redirect("clubs:my_plan")

    if member_plan.auto_renew:
        messages.info(request, "Автопродление клубного тарифа уже включено.")
        return redirect("clubs:my_plan")

    member_plan = enable_member_plan_auto_renew(member)
    if member_plan is None:
        messages.info(
            request,
            "Для текущего тарифа автопродление недоступно.",
        )
        return redirect("clubs:my_plan")

    until_text = (
        member_plan.ended_at.strftime("%d.%m.%Y")
        if member_plan.ended_at
        else "конца периода"
    )
    messages.success(
        request,
        (
            "Автопродление клубного тарифа включено. "
            f"Следующее автоматическое продление произойдёт после {until_text}."
        ),
    )
    return redirect("clubs:my_plan")


@login_required
@require_GET
def subscription_view(request: HttpRequest, slug: str) -> HttpResponse:
    """Страница подписки клуба (только admin)."""
    club = get_object_or_404(Club, slug=slug)
    if not user_can_edit_club_settings(request.user, club):
        messages.error(request, "Просмотр подписки доступен только администратору.")
        return redirect("clubs:dashboard", slug=slug)
    subscription = get_club_current_subscription(club)
    history = club.subscriptions.order_by("-ends_at")[:10]
    current_plan_slug: str = subscription.plan if subscription else "start"
    current_platform_plan = get_platform_plan(current_plan_slug)
    return render(
        request,
        "clubs/subscription.html",
        {
            "club": club,
            "subscription": subscription,
            "history": history,
            "plan_prices": _get_plan_prices_for_subscription(),
            "platform_plans": get_platform_plans(),
            "current_platform_plan": current_platform_plan,
            "is_club_panel": True,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def subscription_pay(request: HttpRequest, slug: str) -> HttpResponse:
    """Выбор тарифа и период оплаты, инициация платежа подписки клуба платформе."""
    club = get_object_or_404(Club, slug=slug)
    if not user_can_edit_club_settings(request.user, club):
        messages.error(request, "Оплата подписки доступна только администратору.")
        return redirect("clubs:subscription", slug=slug)

    initial_plan = request.GET.get("plan", "").strip()
    initial_period = request.GET.get("period", "").strip()

    if request.method == "POST":
        plan = request.POST.get("plan", "").strip()
        period = request.POST.get("period", "").strip()
        if plan not in (ClubPlan.START, ClubPlan.BASIC, ClubPlan.PRO):
            messages.error(request, "Выберите корректный тариф.")
            return redirect("clubs:subscription_pay", slug=slug)
        if period not in ("monthly", "yearly"):
            messages.error(request, "Выберите период оплаты (месяц или год).")
            return redirect("clubs:subscription_pay", slug=slug)

        platform_plan = get_platform_plan(plan)
        if not platform_plan:
            messages.error(request, "Тариф не найден. Обратитесь в поддержку.")
            return redirect("clubs:subscription_pay", slug=slug)
        amount = platform_plan.get_price_for_period(period)
        if amount <= 0:
            messages.error(request, "Неверная сумма для выбранного тарифа.")
            return redirect("clubs:subscription_pay", slug=slug)

        period_label = "ежемесячно" if period == "monthly" else "ежегодно"
        plan_name = dict(ClubPlan.choices).get(plan, plan)
        description = f"Подписка клуба {club.name}, тариф {plan_name}, {period_label}"
        return_url = request.build_absolute_uri(
            reverse("clubs:subscription_return", kwargs={"slug": slug})
        )

        try:
            payment_id, confirmation_url = create_payment(
                amount=f"{amount:.2f}",
                return_url=return_url,
                description=description,
                metadata={
                    "subscription_type": "club",
                    "club_id": str(club.id),
                    "plan": plan,
                    "period": period,
                },
                customer_email=club.email,
            )
        except (ValueError, RuntimeError) as exc:
            logger.warning("Ошибка создания платежа подписки клуба: %s", exc)
            messages.error(
                request, "Не удалось создать платёж. Проверьте настройки ЮKassa."
            )
            return redirect("clubs:subscription_pay", slug=slug)

        ClubSubscriptionPaymentPending.objects.create(
            payment_id=payment_id,
            club=club,
            plan=plan,
            period=period,
            amount=amount,
        )
        return redirect(confirmation_url)

    context = {
        "club": club,
        "plan_prices": _get_plan_prices_for_subscription(),
        "plans": ClubPlan,
    }
    if initial_plan in (ClubPlan.START, ClubPlan.BASIC, ClubPlan.PRO):
        context["initial_plan"] = initial_plan
    if initial_period in ("monthly", "yearly"):
        context["initial_period"] = initial_period

    context["is_club_panel"] = True
    return render(request, "clubs/subscription_pay.html", context)


@login_required
@require_GET
def subscription_return(request: HttpRequest, slug: str) -> HttpResponse:
    """Return URL после оплаты подписки клуба платформе."""
    club = get_object_or_404(Club, slug=slug)
    if not user_can_edit_club_settings(request.user, club):
        messages.error(request, "Нет доступа.")
        return redirect("clubs:subscription", slug=slug)

    payment_id = (
        request.GET.get("payment_id")
        or request.GET.get("orderId")
        or request.GET.get("paymentId")
    )
    if not payment_id:
        pending_fallback = (
            ClubSubscriptionPaymentPending.objects.filter(club=club)
            .order_by("-id")
            .first()
        )
        if not pending_fallback:
            messages.warning(request, "ID платежа не найден в запросе.")
            return redirect("clubs:subscription", slug=slug)
        payment_id = pending_fallback.payment_id

    pending = ClubSubscriptionPaymentPending.objects.filter(
        payment_id=payment_id,
        club=club,
    ).first()
    if not pending:
        messages.warning(request, "Платёж не найден или уже обработан.")
        return redirect("clubs:subscription", slug=slug)

    from apps.payments.yookassa_client import get_payment_status

    status = get_payment_status(payment_id)
    if status != "succeeded":
        messages.error(request, "Оплата не прошла. Попробуйте снова.")
        pending.delete()
        return redirect("clubs:subscription", slug=slug)

    now = timezone.now()
    duration_days = 30 if pending.period == "monthly" else 365
    ends_at = now + timezone.timedelta(days=duration_days)

    ClubSubscription.objects.filter(
        club=club,
        status=ClubSubscriptionStatus.ACTIVE,
    ).update(status=ClubSubscriptionStatus.EXPIRED)

    ClubSubscription.objects.create(
        club=club,
        plan=pending.plan,
        period=(
            ClubSubscriptionPeriod.MONTHLY
            if pending.period == "monthly"
            else ClubSubscriptionPeriod.YEARLY
        ),
        price=pending.amount,
        started_at=now,
        ends_at=ends_at,
        auto_renew=False,
        payment_provider="yookassa",
        payment_ref=payment_id,
        status=ClubSubscriptionStatus.ACTIVE,
    )
    pending.delete()
    messages.success(request, "Подписка успешно оплачена и активирована!")
    return redirect("clubs:subscription", slug=slug)


@login_required
@require_http_methods(["GET", "POST"])
def my_notification_settings(request: HttpRequest) -> HttpResponse:
    """Настройки уведомлений игрока для текущего клуба."""
    member = _get_current_club_member(request)
    if not member:
        messages.info(request, "Вы не состоите в клубе.")
        return redirect("clubs:register_choice")

    club = member.club
    obj, _ = ClubNotificationSettings.objects.get_or_create(
        user=request.user,
        club=club,
    )
    config, _ = ClubNotificationConfig.objects.get_or_create(club=club)
    telegram_connected, telegram_bot_username = _get_user_telegram_bot_state(
        request.user
    )
    email_destination = (request.user.email or "").strip()
    active_delivery_channels = 0

    if (
        obj.is_enabled
        and obj.email_enabled
        and config.notify_by_email
        and email_destination
    ):
        active_delivery_channels += 1
    if (
        obj.is_enabled
        and obj.telegram_enabled
        and config.notify_by_telegram
        and telegram_connected
    ):
        active_delivery_channels += 1

    if request.method == "POST":
        form = ClubNotificationSettingsForm(
            request.POST,
            instance=obj,
            user=request.user,
        )
        if form.is_valid():
            form.save()
            messages.success(request, "Настройки уведомлений сохранены.")
            return redirect("clubs:my_notification_settings")
    else:
        form = ClubNotificationSettingsForm(instance=obj, user=request.user)

    return render(
        request,
        "clubs/my_notification_settings.html",
        {
            "club": club,
            "form": form,
            "is_club_panel": True,
            "club_notification_config": config,
            "email_destination": email_destination,
            "telegram_connected": telegram_connected,
            "telegram_bot_username": telegram_bot_username,
            "active_delivery_channels": active_delivery_channels,
            "player_notification_events": [
                "напоминания о членском взносе",
                "уведомления о просрочке взноса",
                "подтверждение оплаты взноса",
                "напоминания о клубных турнирах",
            ],
            "admin_notification_events": (
                [
                    "истечение подписки клуба",
                    "новый участник клуба",
                    "сводка должников",
                ]
                if member.role == ClubMemberRole.ADMIN
                else []
            ),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def club_notification_config(request: HttpRequest, slug: str) -> HttpResponse:
    """Глобальные настройки уведомлений клуба (только admin)."""
    club = get_object_or_404(Club, slug=slug)
    if not user_can_edit_club_settings(request.user, club):
        messages.error(request, "Настройки уведомлений доступны только администратору.")
        return redirect("clubs:dashboard", slug=slug)

    config, _ = ClubNotificationConfig.objects.get_or_create(club=club)

    if request.method == "POST":
        form = ClubNotificationConfigForm(request.POST, instance=config)
        if form.is_valid():
            form.save()
            messages.success(request, "Настройки уведомлений клуба сохранены.")
            return redirect("clubs:club_notification_config", slug=slug)
    else:
        form = ClubNotificationConfigForm(instance=config)

    return render(
        request,
        "clubs/club_notification_config.html",
        {"club": club, "form": form, "is_club_panel": True},
    )
