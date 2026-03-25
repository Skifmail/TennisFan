import logging
from decimal import Decimal
from typing import cast
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.models import LegalAcceptanceLog
from apps.legal.utils import get_legal_document_version
from apps.subscriptions.models import SubscriptionTier
from apps.tournaments.models import Tournament

from .forms import DonateForm
from .models import PaymentRecord, SavedPaymentMethod
from .yookassa_client import (
    create_payment,
    create_payment_with_credentials,
    get_payment_details,
    get_payment_status,
    get_payment_status_with_credentials,
)

logger = logging.getLogger(__name__)


def _get_request_ip(request: HttpRequest) -> str | None:
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "").strip()
    if forwarded_for:
        return forwarded_for.split(",")[0].strip() or None
    remote_addr = request.META.get("REMOTE_ADDR", "").strip()
    return remote_addr or None


def _get_item_label(payment_type: str, item_id: str) -> str:
    if payment_type == "subscription":
        try:
            tier = SubscriptionTier.objects.filter(pk=int(item_id)).first()
        except (TypeError, ValueError):
            tier = None
        if tier is None:
            return "Подписка"
        return cast(str, tier.get_name_display())

    if payment_type == "club_plan":
        try:
            from apps.clubs.models import ClubPlayerPlan

            plan = (
                ClubPlayerPlan.objects.select_related("club")
                .filter(pk=int(item_id))
                .first()
            )
        except (TypeError, ValueError):
            plan = None
        if plan is None:
            return "Тариф клуба"
        return cast(str, f"{plan.club.name}: {plan.name}")

    if payment_type == "club_fee":
        return "Членский взнос клуба"

    if payment_type == "tournament":
        try:
            tournament = (
                Tournament.objects.select_related("club")
                .filter(pk=int(item_id))
                .first()
            )
        except (TypeError, ValueError):
            tournament = None
        if tournament is None:
            return "Турнир"
        if tournament.club_id:
            return cast(str, f"{tournament.club.name}: {tournament.name}")
        return cast(str, tournament.title)

    if payment_type == "donation":
        return "Поддержка проекта"

    return "Оплата"


def _get_tournament_club_member(user, tournament: Tournament):
    if not getattr(user, "is_authenticated", False) or not tournament.club_id:
        return None

    from apps.clubs.models import ClubMember, ClubMemberStatus

    return (
        ClubMember.objects.select_related("club")
        .filter(
            user=user,
            club_id=tournament.club_id,
            status=ClubMemberStatus.ACTIVE,
        )
        .first()
    )


def _get_discounted_tournament_entry_fee(
    request: HttpRequest,
    tournament: Tournament,
) -> Decimal:
    entry_fee: Decimal = tournament.entry_fee or Decimal("0")
    if tournament.club_id:
        return entry_fee
    if (
        request.user.is_authenticated
        and hasattr(request.user, "subscription")
        and request.user.subscription.is_valid()
    ):
        discount_percent = request.user.subscription.tier.one_day_tournament_discount
        if discount_percent > 0:
            discount = entry_fee * (Decimal(discount_percent) / 100)
            entry_fee = entry_fee - discount
    return entry_fee


def _get_balance_transaction_by_id(transaction_id_raw: str):
    try:
        transaction_id = int(str(transaction_id_raw or "").strip())
    except (TypeError, ValueError):
        return None

    if transaction_id <= 0:
        return None

    from apps.clubs.models import ClubMemberBalanceTransaction

    return ClubMemberBalanceTransaction.objects.filter(pk=transaction_id).first()


def _log_offer_acceptance(
    request: HttpRequest,
    *,
    user,
    payment_type: str,
    item_id: str,
    payment_id: str,
) -> None:
    LegalAcceptanceLog.objects.create(
        user=user,
        document_slug="offer",
        document_version=get_legal_document_version("offer"),
        source="payment",
        ip_address=_get_request_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", "").strip(),
        metadata={
            "payment_type": payment_type,
            "item_id": item_id,
            "payment_id": payment_id,
        },
    )


def _create_payment_record(
    *,
    user,
    payment_type: str,
    item_id: str,
    amount_str: str,
    payment_id: str,
    autopay_enabled: bool,
    status: str = "succeeded",
    is_recurring: bool = False,
    metadata: dict | None = None,
) -> None:
    try:
        amount = Decimal(amount_str)
    except Exception:
        amount = Decimal("0")

    PaymentRecord.objects.update_or_create(
        user=user,
        yookassa_payment_id=payment_id,
        defaults={
            "payment_type": payment_type or PaymentRecord.PaymentType.DONATION,
            "item_id": item_id,
            "item_label": _get_item_label(payment_type, item_id),
            "amount": amount,
            "status": status,
            "is_recurring": is_recurring,
            "autopay_enabled": autopay_enabled,
            "metadata": metadata or {},
        },
    )


def _is_offer_accepted(request: HttpRequest) -> bool:
    """Проверяет, подтверждена ли оферта в POST-форме оплаты.

    Args:
        request (HttpRequest): Текущий HTTP-запрос.

    Returns:
        bool: ``True``, если чекбокс акцепта оферты установлен.
    """
    raw = str(request.POST.get("offer_accepted", "")).strip().lower()
    return raw in {"1", "true", "on", "yes"}


def _build_preview_redirect(request: HttpRequest, payment_type: str | None):
    """Вернуть redirect на страницу предпросмотра с восстановлением параметров платежа.

    Args:
        request (HttpRequest): Текущий HTTP-запрос.
        payment_type (str | None): Тип платежа (subscription, tournament, donation).

    Returns:
        HttpResponse: Перенаправление на страницу предпросмотра платежа.
    """
    params: dict[str, str] = {}
    if payment_type:
        params["type"] = payment_type

    # subscription / club_plan / tournament
    item_id = request.POST.get("id") or request.GET.get("id")
    if item_id:
        params["id"] = str(item_id)
    next_url = request.POST.get("next") or request.GET.get("next")
    if next_url:
        params["next"] = str(next_url)

    # donation
    amount = request.POST.get("amount")
    if amount:
        params["amount"] = str(amount)
    comment = request.POST.get("comment")
    if comment:
        params["comment"] = str(comment)
    name_or_email = request.POST.get("name_or_email")
    if name_or_email:
        params["name_or_email"] = str(name_or_email)

    base_url = reverse("payment_preview")
    query_string = urlencode(params)
    return redirect(f"{base_url}?{query_string}" if query_string else base_url)


def donate_view(request: HttpRequest) -> HttpResponse:
    """Страница доната — доступна всем пользователям.

    Args:
        request (HttpRequest): Текущий HTTP-запрос.

    Returns:
        HttpResponse: HTML-страница с формой доната.
    """
    if request.method == "POST":
        form = DonateForm(request.POST, user=request.user)
        if form.is_valid():
            params = {
                "type": "donation",
                "amount": form.cleaned_data["amount"],
                "comment": form.cleaned_data.get("comment", ""),
            }
            # Добавляем имя/email если указано
            name_or_email = form.cleaned_data.get("name_or_email", "").strip()
            if name_or_email:
                params["name_or_email"] = name_or_email
            base_url = reverse("payment_preview")
            query_string = urlencode(params)
            return redirect(f"{base_url}?{query_string}")
    else:
        form = DonateForm(user=request.user)
        # Если пользователь авторизован, предзаполняем имя/email
        if request.user.is_authenticated:
            if request.user.get_full_name():
                form.fields["name_or_email"].initial = request.user.get_full_name()
            elif request.user.email:
                form.fields["name_or_email"].initial = request.user.email

    return render(request, "payments/donate.html", {"form": form})


def payment_preview(request: HttpRequest) -> HttpResponse:
    """Предпросмотр платежа перед переходом на форму ЮKassa.

    Для доната страница доступна всем пользователям, для подписок и турниров
    требуется авторизация.

    Args:
        request (HttpRequest): Текущий HTTP-запрос.

    Returns:
        HttpResponse: HTML-страница с деталями платежа и кнопкой перехода к оплате.
    """
    payment_type = request.GET.get("type")

    # Для подписок и турниров требуется авторизация
    if (
        payment_type in ("subscription", "club_plan", "tournament")
        and not request.user.is_authenticated
    ):
        from django.conf import settings

        messages.info(request, "Для оплаты необходимо зарегистрироваться.")
        login_url = getattr(settings, "LOGIN_URL", "login")
        next_url = request.get_full_path()
        return redirect(f"{reverse(login_url)}?next={next_url}")

    context = {}

    if payment_type == "subscription":
        tier_id = request.GET.get("id")
        tier = get_object_or_404(SubscriptionTier, pk=tier_id)
        # Эффективная цена: региональная при наличии, иначе из тарифа
        effective_price = tier.price
        user_city = "moscow"
        if request.user.is_authenticated:
            try:
                player = getattr(request.user, "player", None)
                if player and player.city:
                    city_val = player.city.lower().strip()
                    user_city = (
                        "moscow"
                        if city_val in ("moscow", "moskva", "москва")
                        else city_val
                    )
            except Exception:
                pass
        if user_city != "moscow":
            from apps.subscriptions.models import RegionalTierPrice

            rp = RegionalTierPrice.objects.filter(tier=tier).first()
            if rp:
                effective_price = rp.price
        # Первая подписка за 1 ₽ (если тариф участвует и игрок ещё ни разу не покупал)
        first_time_one_ruble = False
        if tier.first_subscription_one_ruble and request.user.is_authenticated:
            try:
                if (
                    getattr(request.user, "player", None)
                    and not request.user.player.has_ever_paid_subscription
                ):
                    first_time_one_ruble = True
            except Exception:
                pass
        amount = Decimal("1") if first_time_one_ruble else effective_price
        details = [
            ("Тариф", tier.get_name_display()),
            ("Срок действия", tier.duration_label),
        ]
        if first_time_one_ruble:
            details.append(("Акция", "Первая подписка за 1 ₽"))
        context = {
            "title": f"Подписка: {tier.get_name_display()}",
            "description": "Подписка на сервис TennisFan",
            "amount": amount,
            "amount_value": f"{amount:.2f}",
            "item_id": tier.id,
            "details": details,
        }

    elif payment_type == "club_plan":
        try:
            plan_id = int(str(request.GET.get("id") or "").strip())
        except (TypeError, ValueError) as err:
            raise Http404("Unknown club plan") from err
        next_url = request.GET.get("next", "").strip()
        redirect_url = reverse(
            "clubs:my_plan_payment_preview",
            kwargs={"plan_id": plan_id},
        )
        if next_url:
            redirect_url = f"{redirect_url}?{urlencode({'next': next_url})}"
        return redirect(redirect_url)

    elif payment_type == "tournament":
        tournament_id = request.GET.get("id")
        tournament = get_object_or_404(
            Tournament.objects.select_related("club"),
            pk=tournament_id,
        )
        next_url = request.GET.get("next", "").strip()
        tournament_member = _get_tournament_club_member(request.user, tournament)

        if (
            tournament.club_id
            and not (
                getattr(request.user, "is_staff", False)
                or getattr(request.user, "is_superuser", False)
            )
            and tournament_member is None
        ):
            messages.error(
                request,
                "Оплата клубного турнира доступна только активным участникам клуба.",
            )
            return redirect("tournament_detail", slug=tournament.slug)

        # Админ не платит за турниры — сразу считаем участие подтверждённым
        if request.user.is_authenticated and (
            getattr(request.user, "is_staff", False)
            or getattr(request.user, "is_superuser", False)
        ):
            messages.success(
                request,
                "Регистрация без оплаты.",
            )
            params = {"type": "tournament", "id": tournament.id}
            if next_url:
                params["next"] = next_url
            success_url = reverse("payment_success") + "?" + urlencode(params)
            return redirect(success_url)

        # Calculate price (handle discount if user has subscription)
        raw_entry_fee: Decimal = tournament.entry_fee or Decimal("0")
        entry_fee = _get_discounted_tournament_entry_fee(request, tournament)
        discount: Decimal = Decimal("0")
        if (
            not tournament.club_id
            and request.user.is_authenticated
            and hasattr(request.user, "subscription")
            and request.user.subscription.is_valid()
        ):
            discount_percent = (
                request.user.subscription.tier.one_day_tournament_discount
            )
            if discount_percent > 0:
                discount = raw_entry_fee * (Decimal(discount_percent) / 100)

        discount_int = int(discount) if discount else 0
        balance_available = Decimal("0.00")
        balance_to_apply = Decimal("0.00")
        external_amount_due = entry_fee
        if tournament_member is not None:
            from apps.clubs.finance_services import calculate_balance_payment_breakdown

            breakdown = calculate_balance_payment_breakdown(
                tournament_member, entry_fee
            )
            balance_available = breakdown.balance_available
            balance_to_apply = breakdown.balance_to_apply
            external_amount_due = breakdown.external_amount_due

        if tournament.club_id and external_amount_due > 0:
            from apps.clubs.views.helpers import _get_club_payment_settings

            payment_settings = _get_club_payment_settings(tournament.club)
            if payment_settings is None:
                messages.error(
                    request,
                    "Клуб ещё не подключил свою YooKassa. Онлайн-оплата турнира временно недоступна.",
                )
                return redirect("tournament_detail", slug=tournament.slug)

        context = {
            "title": (
                f"Турнир клуба: {tournament.name}"
                if tournament.club_id
                else f"Турнир: {tournament.name}"
            ),
            "description": (
                f"Вступительный взнос за участие в турнире клуба «{tournament.club.name}»."
                if tournament.club_id
                else (
                    "Взнос за участие в турнире "
                    f"{tournament.get_city_display() if hasattr(tournament, 'get_city_display') else tournament.city}"
                )
            ),
            "amount": entry_fee,
            "amount_value": f"{entry_fee:.2f}",
            "item_id": tournament.id,
            "payment_next_url": next_url,
            "details": [
                *([("Клуб", tournament.club.name)] if tournament.club_id else []),
                ("Турнир", tournament.name),
                ("Дата", tournament.start_date),
                ("Город", tournament.city),
                *(
                    [("Получатель", f"{tournament.club.name} · YooKassa клуба")]
                    if tournament.club_id
                    else []
                ),
                ("Скидка", f"{discount_int} ₽" if discount_int else "Нет"),
                (
                    "Спишется с баланса",
                    f"{balance_to_apply} ₽" if balance_to_apply > 0 else "0 ₽",
                ),
            ],
            "balance_available": balance_available,
            "balance_to_apply": balance_to_apply,
            "external_amount_due": external_amount_due,
            **(
                {"is_club_panel": True, "club": tournament.club}
                if tournament_member is not None
                else {}
            ),
        }

    elif payment_type == "donation":
        amount = request.GET.get("amount")
        comment = request.GET.get("comment", "")
        name_or_email = request.GET.get("name_or_email", "")

        try:
            amount_decimal = Decimal(str(amount or "0").replace(",", "."))
        except Exception:
            amount_decimal = Decimal("0")
        if amount_decimal <= 0:
            messages.warning(request, "Укажите сумму для доната.")
            return redirect(reverse("donate"))

        amount_value = f"{amount_decimal:.2f}"
        details = [("Тип", "Донат"), ("Сумма", f"{amount_decimal} ₽")]
        if name_or_email:
            details.append(("Имя/Email", name_or_email))
        if comment:
            details.append(("Комментарий", comment))
        else:
            details.append(("Комментарий", "Нет комментария"))

        context = {
            "title": "Поддержка проекта (Донат)",
            "description": "Добровольный взнос на развитие проекта",
            "amount": amount_decimal,
            "amount_value": amount_value,
            "comment": comment,
            "name_or_email": name_or_email,
            "details": details,
        }

    else:
        raise Http404("Unknown payment type")

    context["payment_type"] = payment_type
    context["process_url"] = reverse("payment_process")
    context.setdefault("external_amount_due", context.get("amount"))

    return render(request, "payments/preview.html", context)


def payment_process(request: HttpRequest) -> HttpResponse:
    """Создание платежа в ЮKassa и редирект на страницу оплаты.

    Для доната доступно всем пользователям, для подписок и турниров требуется
    авторизация. Здесь же обрабатывается акцепт оферты и, для подписок,
    выбор опции сохранения карты для автопродления.

    Args:
        request (HttpRequest): Текущий HTTP-запрос.

    Returns:
        HttpResponse: Редирект либо обратно на предпросмотр, либо на форму оплаты.
    """
    payment_type = request.POST.get("type") or request.GET.get("type")

    if payment_type == "club_plan":
        try:
            plan_id = int(
                str(request.POST.get("id") or request.GET.get("id") or "").strip()
            )
        except (TypeError, ValueError):
            plan_id = None
        if plan_id is not None:
            return redirect("clubs:my_plan_payment_preview", plan_id=plan_id)
        return redirect("clubs:my_plan_change")

    # Для подписок и турниров требуется авторизация
    if (
        payment_type in ("subscription", "club_plan", "tournament")
        and not request.user.is_authenticated
    ):
        messages.info(request, "Для оплаты необходимо зарегистрироваться.")
        login_url = getattr(settings, "LOGIN_URL", "login")
        next_url = request.get_full_path()
        return redirect(f"{reverse(login_url)}?next={next_url}")

    # Server-side проверка акцепта оферты (нельзя полагаться только на required в HTML)
    if request.method == "POST" and not _is_offer_accepted(request):
        messages.error(
            request,
            "Для продолжения оплаты необходимо подтвердить согласие с условиями Публичной оферты.",
        )
        return _build_preview_redirect(request, payment_type)

    if request.method != "POST":
        return _build_preview_redirect(request, payment_type)

    tournament = None
    tournament_member = None
    balance_transaction = None
    club_payment_settings = None
    club_payment_secret = ""

    # Сумма и описание для ЮKassa (нормализуем запятую в точку — форма может отдать "100,00" в русской локали)
    amount_raw = (request.POST.get("amount") or "").strip().replace(",", ".")
    try:
        amount_decimal = Decimal(amount_raw or "0")
    except Exception:
        amount_decimal = Decimal("0")

    if payment_type == "tournament":
        tournament = get_object_or_404(
            Tournament.objects.select_related("club"),
            pk=request.POST.get("id"),
        )
        tournament_member = _get_tournament_club_member(request.user, tournament)
        if (
            tournament.club_id
            and not (
                getattr(request.user, "is_staff", False)
                or getattr(request.user, "is_superuser", False)
            )
            and tournament_member is None
        ):
            messages.error(
                request,
                "Оплата клубного турнира доступна только активным участникам клуба.",
            )
            return redirect("tournament_detail", slug=tournament.slug)

        amount_decimal = _get_discounted_tournament_entry_fee(request, tournament)
        balance_to_apply = Decimal("0.00")
        if tournament_member is not None:
            from apps.clubs.finance_services import (
                calculate_balance_payment_breakdown,
                reserve_member_balance,
                spend_member_balance,
            )
            from apps.clubs.models import ClubMemberBalanceTransaction

            breakdown = calculate_balance_payment_breakdown(
                tournament_member,
                amount_decimal,
            )
            balance_to_apply = breakdown.balance_to_apply
            if breakdown.balance_to_apply > 0 and breakdown.external_amount_due > 0:
                try:
                    balance_transaction = reserve_member_balance(
                        tournament_member,
                        breakdown.balance_to_apply,
                        source=ClubMemberBalanceTransaction.Source.TOURNAMENT_PAYMENT,
                        description=f"Оплата турнира «{tournament.name}»",
                        reference=f"tournament:{tournament.id}",
                        metadata={"tournament_id": tournament.id},
                    )
                except ValueError:
                    messages.error(
                        request,
                        "Не удалось зарезервировать средства с баланса. Обновите страницу и попробуйте снова.",
                    )
                    return _build_preview_redirect(request, payment_type)

            if breakdown.external_amount_due <= 0:
                spend_member_balance(
                    tournament_member,
                    breakdown.balance_to_apply,
                    source=ClubMemberBalanceTransaction.Source.TOURNAMENT_PAYMENT,
                    description=f"Оплата турнира «{tournament.name}»",
                    reference=f"tournament:{tournament.id}",
                    metadata={"tournament_id": tournament.id},
                )
                success_url = reverse("payment_success")
                success_url += "?" + urlencode(
                    {
                        "type": "tournament",
                        "id": tournament.id,
                        "next": request.POST.get("next", "").strip(),
                        "amount": f"{amount_decimal:.2f}",
                    }
                )
                return redirect(success_url)

            amount_decimal = breakdown.external_amount_due
        if tournament.club_id and amount_decimal > 0:
            from apps.clubs.payment_utils import decrypt_secret
            from apps.clubs.views.helpers import _get_club_payment_settings

            club_payment_settings = _get_club_payment_settings(tournament.club)
            if club_payment_settings is None:
                messages.error(
                    request,
                    "Клуб ещё не подключил свою YooKassa. Онлайн-оплата турнира временно недоступна.",
                )
                return _build_preview_redirect(request, payment_type)
            try:
                club_payment_secret = decrypt_secret(
                    club_payment_settings.payment_api_key
                )
            except Exception as exc:
                if balance_transaction is not None:
                    from apps.clubs.finance_services import cancel_reserved_balance

                    cancel_reserved_balance(balance_transaction)
                logger.warning(
                    "Не удалось расшифровать ключ YooKassa клуба для оплаты турнира: %s",
                    exc,
                )
                messages.error(request, "Ошибка платёжных настроек клуба.")
                return _build_preview_redirect(request, payment_type)

    if amount_decimal <= 0:
        messages.error(request, "Укажите корректную сумму оплаты.")
        return _build_preview_redirect(request, payment_type)

    amount_str = f"{amount_decimal:.2f}"
    item_id = request.POST.get("id", "").strip()
    next_url = request.POST.get("next", "").strip()

    # Для подписки пользователь может включить автопродление и сохранение карты.
    enable_autopay = False
    if payment_type in ("subscription", "club_plan") and request.user.is_authenticated:
        raw_autopay = str(request.POST.get("enable_autopay", "")).strip().lower()
        enable_autopay = raw_autopay in {"1", "true", "on", "yes"}

    if payment_type == "donation":
        description = "Поддержка проекта TennisFan (донат)"
    elif payment_type == "subscription":
        description = "Подписка TennisFan"
        if item_id:
            try:
                tier = SubscriptionTier.objects.filter(pk=int(item_id)).first()
                if tier:
                    description = f"Подписка TennisFan: {tier.get_name_display()}"
            except (ValueError, TypeError):
                pass
    elif payment_type == "club_plan":
        description = "Оплата клубного тарифа TennisFan"
        if item_id:
            try:
                from apps.clubs.models import ClubPlayerPlan

                plan = (
                    ClubPlayerPlan.objects.select_related("club")
                    .filter(pk=int(item_id))
                    .first()
                )
                if plan:
                    description = f"Клубный тариф {plan.club.name}: {plan.name}"
            except (ValueError, TypeError):
                pass
    elif payment_type == "tournament":
        if tournament is not None and tournament.club_id:
            description = f"Вступительный взнос за турнир клуба {tournament.club.name}: {tournament.name}"
        else:
            description = "Взнос за участие в турнире TennisFan"
    else:
        description = "Оплата на TennisFan"

    return_url_absolute = request.build_absolute_uri(reverse("payment_return"))
    metadata = {
        "payment_type": payment_type,
        "item_id": item_id or "",
        "next": next_url or "",
    }
    if request.user.is_authenticated:
        metadata["user_id"] = str(request.user.pk)
    if tournament is not None and tournament.club_id:
        metadata["club_id"] = str(tournament.club_id)
        metadata["club_slug"] = tournament.club.slug
    if payment_type in ("subscription", "club_plan") and enable_autopay:
        # Маркер в metadata — в логах и ЛК ЮKassa будет видно, что платёж
        # используется для включения автопродления подписки.
        metadata["enable_autopay"] = "1"

    # Email для чека 54-ФЗ (обязателен при включённой передаче чеков в ЛК ЮKassa)
    receipt_email = ""
    if request.user.is_authenticated and getattr(request.user, "email", None):
        receipt_email = (request.user.email or "").strip()
    if not receipt_email or "@" not in receipt_email:
        name_or_email = (request.POST.get("name_or_email") or "").strip()
        if "@" in name_or_email:
            receipt_email = name_or_email
    if not receipt_email or "@" not in receipt_email:
        receipt_email = (
            getattr(settings, "DEFAULT_FROM_EMAIL", "") or "info@tennisfan.ru"
        )

    try:
        if tournament is not None and tournament.club_id:
            if club_payment_settings is None:
                raise RuntimeError("Отсутствуют платёжные реквизиты клуба.")
            payment_id, confirmation_url = create_payment_with_credentials(
                shop_id=club_payment_settings.payment_shop_id,
                secret_key=club_payment_secret,
                amount=amount_str,
                return_url=return_url_absolute,
                description=description[:128],
                metadata=metadata,
                customer_email=receipt_email,
            )
        else:
            shop_id = (getattr(settings, "YOOKASSA_SHOP_ID", None) or "").strip()
            secret_key = (getattr(settings, "YOOKASSA_SECRET_KEY", None) or "").strip()
            if not shop_id or not secret_key:
                logger.warning(
                    "YooKassa: ключи не заданы. SHOP_ID=%s, SECRET_KEY=%s. "
                    "В Environment не должно быть комментариев (#) и пустых строк; задайте переменные отдельными строками.",
                    "пусто" if not shop_id else "задан",
                    "пусто" if not secret_key else "задан",
                )
                messages.error(
                    request,
                    "Платёжный шлюз временно недоступен. Попробуйте позже или свяжитесь с нами.",
                )
                return _build_preview_redirect(request, payment_type)
            payment_id, confirmation_url = create_payment(
                amount=amount_str,
                return_url=return_url_absolute,
                description=description[:128],
                metadata=metadata,
                customer_email=receipt_email,
                save_payment_method=(
                    enable_autopay
                    if payment_type in ("subscription", "club_plan")
                    else None
                ),
            )
    except (ValueError, RuntimeError) as e:
        if balance_transaction is not None:
            from apps.clubs.finance_services import cancel_reserved_balance

            cancel_reserved_balance(balance_transaction)
        logger.exception("YooKassa create_payment failed: %s", e)
        messages.error(
            request,
            "Не удалось создать платёж. Проверьте сумму и попробуйте снова.",
        )
        return _build_preview_redirect(request, payment_type)

    pending_data = {
        "payment_id": payment_id,
        "payment_type": payment_type,
        "item_id": item_id,
        "next": next_url,
        "enable_autopay": (
            "1"
            if payment_type in ("subscription", "club_plan") and enable_autopay
            else ""
        ),
    }
    if payment_type == "donation":
        pending_data["amount"] = amount_str
        pending_data["name_or_email"] = request.POST.get("name_or_email", "").strip()
        pending_data["comment"] = request.POST.get("comment", "").strip()
    else:
        # Подписка и турнир: сохраняем фактическую сумму (региональная цена, акция 1 ₽ и т.д.)
        pending_data["amount"] = amount_str
    if payment_type == "tournament" and tournament is not None:
        pending_data["balance_amount"] = f"{balance_to_apply:.2f}"
        pending_data["total_amount"] = (
            f"{_get_discounted_tournament_entry_fee(request, tournament):.2f}"
        )
        if tournament.club_id:
            pending_data["club_id"] = str(tournament.club_id)
            pending_data["club_slug"] = tournament.club.slug
    if balance_transaction is not None:
        pending_data["balance_transaction_id"] = str(balance_transaction.id)
    request.session["yookassa_pending"] = pending_data
    request.session.modified = True
    return redirect(confirmation_url)


def payment_return(request: HttpRequest) -> HttpResponse:
    """Обработка возврата пользователя с формы ЮKassa.

    ЮKassa перенаправляет браузер на ``return_url``. На этой странице мы
    проверяем статус платежа и перенаправляем пользователя далее на
    ``payment_success`` при успешной оплате.

    Args:
        request (HttpRequest): Текущий HTTP-запрос.

    Returns:
        HttpResponse: Редирект на страницу успеха или обратно к оформлению.
    """
    pending = request.session.get("yookassa_pending")
    if not pending:
        messages.warning(
            request,
            "Сессия истекла или вы уже обработали этот платёж. Проверьте результат в личном кабинете.",
        )
        return redirect("donate")

    payment_id = pending.get("payment_id")
    payment_type = pending.get("payment_type", "")
    item_id = pending.get("item_id", "")
    next_url = pending.get("next", "")
    enable_autopay = str(pending.get("enable_autopay", "")).strip() == "1"
    balance_transaction = _get_balance_transaction_by_id(
        str(pending.get("balance_transaction_id") or "")
    )
    club_id = str(pending.get("club_id") or "").strip()
    status = None
    tournament = None
    if payment_type == "tournament" and item_id:
        try:
            tournament = (
                Tournament.objects.select_related("club")
                .filter(pk=int(item_id))
                .first()
            )
        except (TypeError, ValueError):
            tournament = None
        if tournament and tournament.club_id and club_id:
            from apps.clubs.payment_utils import decrypt_secret
            from apps.clubs.views.helpers import _get_club_payment_settings

            club_payment_settings = _get_club_payment_settings(tournament.club)
            if club_payment_settings is None:
                if balance_transaction is not None:
                    from apps.clubs.finance_services import cancel_reserved_balance

                    cancel_reserved_balance(balance_transaction)
                messages.error(
                    request,
                    "Клуб отключил YooKassa или не настроил платёжные реквизиты. Оплата турнира не подтверждена.",
                )
                del request.session["yookassa_pending"]
                request.session.modified = True
                return redirect("tournament_detail", slug=tournament.slug)
            try:
                club_secret = decrypt_secret(club_payment_settings.payment_api_key)
            except Exception as exc:
                if balance_transaction is not None:
                    from apps.clubs.finance_services import cancel_reserved_balance

                    cancel_reserved_balance(balance_transaction)
                logger.warning(
                    "Не удалось расшифровать ключ YooKassa клуба при проверке оплаты турнира: %s",
                    exc,
                )
                messages.error(request, "Ошибка проверки клубного платежа.")
                del request.session["yookassa_pending"]
                request.session.modified = True
                return redirect("tournament_detail", slug=tournament.slug)
            status = (
                get_payment_status_with_credentials(
                    payment_id,
                    club_payment_settings.payment_shop_id,
                    club_secret,
                )
                if payment_id
                else None
            )
        else:
            status = get_payment_status(payment_id) if payment_id else None
    else:
        status = get_payment_status(payment_id) if payment_id else None
    if status != "succeeded":
        if balance_transaction is not None:
            from apps.clubs.finance_services import cancel_reserved_balance

            cancel_reserved_balance(balance_transaction)
        messages.error(
            request,
            "Оплата не была завершена или отменена. Попробуйте снова или выберите другой способ.",
        )
        del request.session["yookassa_pending"]
        request.session.modified = True
        preview_params: dict[str, str] = {}
        if payment_type:
            preview_params["type"] = payment_type
        if item_id:
            preview_params["id"] = item_id
        if preview_params:
            return redirect(
                reverse("payment_preview") + "?" + urlencode(preview_params)
            )
        return redirect("donate")

    if balance_transaction is not None:
        from apps.clubs.finance_services import confirm_reserved_balance

        confirm_reserved_balance(balance_transaction)

    # Уведомление админу о донате до очистки сессии
    if payment_type == "donation":
        try:
            from apps.core.telegram_notify import notify_donation

            notify_donation(
                amount=pending.get("amount", ""),
                name_or_email=pending.get("name_or_email", ""),
                comment=pending.get("comment", ""),
            )
        except Exception as e:
            logger.warning("Telegram notify_donation failed: %s", e)

    # Для подписки и клубного тарифа при включённом автопродлении пробуем сохранить способ оплаты.
    if (
        payment_type in ("subscription", "club_plan")
        and enable_autopay
        and request.user.is_authenticated
        and payment_id
    ):
        details = get_payment_details(payment_id)
        if isinstance(details, dict):
            pm = details.get("payment_method") or {}
            pm_id = pm.get("id")
            if isinstance(pm_id, str) and pm_id:
                card = pm.get("card") or {}
                card_last4 = str(card.get("last4") or "")[-4:]
                exp_month = str(card.get("expiry_month") or "")[:2]
                exp_year = str(card.get("expiry_year") or "")[:4]
                network = str(card.get("card_type") or card.get("brand") or "").strip()

                if payment_type == "subscription":
                    SavedPaymentMethod.objects.filter(
                        user=request.user,
                        club__isnull=True,
                        is_default_for_subscriptions=True,
                    ).update(is_default_for_subscriptions=False)
                else:
                    SavedPaymentMethod.objects.filter(
                        user=request.user,
                        is_default_for_club_plans=True,
                    ).update(is_default_for_club_plans=False)

                existing_method = SavedPaymentMethod.objects.filter(
                    payment_method_id=pm_id
                ).first()
                defaults = {
                    "user": request.user,
                    "club": (
                        None
                        if payment_type == "subscription"
                        else (existing_method.club if existing_method else None)
                    ),
                    "card_last4": card_last4,
                    "card_exp_month": exp_month,
                    "card_exp_year": exp_year,
                    "card_network": network,
                    "is_active": True,
                    "is_default_for_subscriptions": (
                        True
                        if payment_type == "subscription"
                        else bool(
                            existing_method
                            and existing_method.is_default_for_subscriptions
                        )
                    ),
                    "is_default_for_club_plans": (
                        True
                        if payment_type == "club_plan"
                        else bool(
                            existing_method
                            and existing_method.is_default_for_club_plans
                        )
                    ),
                    "is_default_for_club_fees": bool(
                        existing_method and existing_method.is_default_for_club_fees
                    ),
                }

                SavedPaymentMethod.objects.update_or_create(
                    payment_method_id=pm_id, defaults=defaults
                )

    if request.user.is_authenticated and payment_id:
        _log_offer_acceptance(
            request,
            user=request.user,
            payment_type=payment_type,
            item_id=item_id,
            payment_id=payment_id,
        )
        _create_payment_record(
            user=request.user,
            payment_type=payment_type,
            item_id=item_id,
            amount_str=str(pending.get("amount", "")),
            payment_id=payment_id,
            autopay_enabled=enable_autopay,
            metadata={
                "club_id": str(pending.get("club_id", "") or ""),
                "club_slug": str(pending.get("club_slug", "") or ""),
                "balance_amount": str(pending.get("balance_amount", "") or "0"),
                "external_amount": str(pending.get("amount", "") or "0"),
                "total_amount": str(
                    pending.get("total_amount", "") or pending.get("amount", "") or "0"
                ),
            },
        )

    del request.session["yookassa_pending"]
    request.session.modified = True

    # Донат от гостя — просто благодарим и редирект на главную
    if payment_type == "donation" and not request.user.is_authenticated:
        messages.success(request, "Спасибо за поддержку проекта!")
        return redirect("home")

    success_url = reverse("payment_success")
    params: list[tuple[str, str]] = []
    if payment_type:
        params.append(("type", payment_type))
    if item_id:
        params.append(("id", item_id))
    if next_url:
        params.append(("next", next_url))
    amount_paid = pending.get("amount", "").strip()
    if amount_paid:
        params.append(("amount", amount_paid))
    if params:
        if payment_type == "club_plan" and enable_autopay:
            params.append(("autopay", "1"))
        success_url += "?" + urlencode(params)
    return redirect(success_url)


def payment_success(request: HttpRequest) -> HttpResponse:
    """Финальная обработка успешного платежа и перенаправление пользователя.

    GET-параметры:

    * ``type`` — тип платежа (``tournament``, ``subscription``, ``donation``);
    * ``id`` — идентификатор объекта (турнир или тариф);
    * ``next`` — опциональный URL для дальнейшего редиректа.

    Args:
        request (HttpRequest): Текущий HTTP-запрос.

    Returns:
        HttpResponse: Редирект на страницу профиля, турнира или список турниров.
    """
    if not request.user.is_authenticated:
        from django.conf import settings

        messages.info(request, "Для просмотра необходимо войти.")
        login_url = getattr(settings, "LOGIN_URL", "login")
        return redirect(f"{reverse(login_url)}?next={request.get_full_path()}")

    payment_type = request.GET.get("type")
    item_id = request.GET.get("id")
    next_url = request.GET.get("next", "").strip()

    if payment_type == "donation":
        messages.success(request, "Спасибо за поддержку проекта!")
        return redirect("home")

    if payment_type == "subscription" and item_id:
        try:
            tier_id = int(item_id)
        except (TypeError, ValueError):
            tier_id = None
        if tier_id is not None:
            tier = SubscriptionTier.objects.filter(pk=tier_id).first()
            if tier:
                from apps.subscriptions.models import UserSubscription
                from apps.subscriptions.views import _mark_user_paid_subscription

                sub, created = UserSubscription.objects.get_or_create(
                    user=request.user,
                    defaults={"tier": tier, "end_date": timezone.now()},
                )
                sub.tier = tier
                sub.is_active = True
                sub.cancelled_at = None
                now = timezone.now()
                if created:
                    sub.start_date = now
                    sub.end_date = tier.apply_duration(now)
                else:
                    base = sub.end_date if sub.end_date and sub.end_date > now else now
                    sub.end_date = tier.apply_duration(base)
                from apps.subscriptions.utils import normalize_city_for_pricing

                city = getattr(request.user, "player", None) and getattr(
                    request.user.player, "city", None
                )
                sub.purchase_city = normalize_city_for_pricing(city or "")
                sub.save()
                if not tier.is_unlimited and tier.max_tournaments > 0:
                    sub.add_tournament_registration_slots(tier.max_tournaments)
                _mark_user_paid_subscription(request.user)
                amount_paid = request.GET.get("amount")
                try:
                    from apps.core.telegram_notify import notify_subscription_purchase

                    notify_subscription_purchase(
                        request.user,
                        tier,
                        amount_paid=amount_paid,
                    )
                except Exception as e:
                    logger.warning(
                        "Telegram notify_subscription_purchase failed: %s", e
                    )
                try:
                    from apps.subscriptions.utils import (
                        send_subscription_purchase_email,
                    )

                    send_subscription_purchase_email(
                        user=request.user,
                        subscription=sub,
                        amount_paid=amount_paid,
                    )
                except Exception:
                    # Ошибки отправки email не должны ломать успешный флоу оплаты.
                    pass
                messages.success(
                    request, f"Подписка «{tier.get_name_display()}» успешно оформлена."
                )
                try:
                    return redirect("profile", pk=request.user.player.pk)
                except Exception:
                    return redirect("pricing")

    if payment_type == "club_plan" and item_id:
        try:
            plan_id = int(item_id)
        except (TypeError, ValueError):
            plan_id = None
        if plan_id is not None:
            from apps.clubs.models import ClubMember, ClubMemberStatus, ClubPlayerPlan
            from apps.clubs.plan_services import purchase_member_plan

            plan = (
                ClubPlayerPlan.objects.select_related("club").filter(pk=plan_id).first()
            )
            if plan is not None:
                member = (
                    ClubMember.objects.select_related("club")
                    .filter(
                        club=plan.club,
                        user=request.user,
                        status=ClubMemberStatus.ACTIVE,
                    )
                    .first()
                )
                if member is None:
                    messages.error(request, "Вы не состоите в клубе для этого тарифа.")
                    return redirect("clubs:club_public_detail", slug=plan.club.slug)

                request.session["current_club_slug"] = plan.club.slug
                request.session.modified = True
                auto_renew_enabled = str(
                    request.GET.get("autopay", "")
                ).strip().lower() in {"1", "true"}
                purchase_member_plan(
                    member,
                    plan,
                    assigned_by=request.user,
                    change_reason="Оплата клубного тарифа участником",
                    auto_renew=auto_renew_enabled,
                )
                messages.success(
                    request,
                    f"Клубный тариф «{plan.name}» успешно оформлен.",
                )
                return redirect("clubs:my_plan")

    if payment_type == "tournament" and item_id:
        try:
            tid = int(item_id)
        except (TypeError, ValueError):
            tid = None
        if tid is not None:
            paid_ids = list(request.session.get("tournament_entry_paid") or [])
            if tid not in paid_ids:
                paid_ids.append(tid)
                request.session["tournament_entry_paid"] = paid_ids
                request.session.modified = True

            tournament = Tournament.objects.filter(pk=tid).first()
            if tournament:
                club_member = _get_tournament_club_member(request.user, tournament)
                if tournament.club_id:
                    if club_member is None:
                        messages.error(
                            request,
                            "Вы больше не состоите в клубе и не можете завершить регистрацию на его турнир.",
                        )
                        return redirect("tournament_detail", slug=tournament.slug)
                    request.session["current_club_slug"] = tournament.club.slug
                    request.session.modified = True
                try:
                    from apps.core.telegram_notify import (
                        notify_tournament_entry_payment,
                    )

                    notify_tournament_entry_payment(
                        tournament,
                        request.user,
                        amount=request.GET.get("amount"),
                    )
                except Exception as e:
                    logger.warning(
                        "Telegram notify_tournament_entry_payment failed: %s",
                        e,
                    )
            if tournament and not tournament.is_doubles():
                # Одиночный турнир: сразу добавляем участника (лимит подписки не тратим)
                player = getattr(request.user, "player", None)
                if player and not tournament.participants.filter(pk=player.pk).exists():
                    tournament.participants.add(player)
                    from apps.tournaments.models import TournamentEntryPayment

                    TournamentEntryPayment.objects.get_or_create(
                        tournament=tournament,
                        user=request.user,
                    )
                messages.success(
                    request,
                    "Оплата вступительного взноса прошла успешно. Вы зарегистрированы на турнир.",
                )
                return redirect("tournament_detail", slug=tournament.slug)
            if tournament and tournament.is_doubles():
                messages.success(
                    request,
                    "Оплата вступительного взноса прошла успешно. Завершите регистрацию на странице турнира: выберите партнёра или создайте команду.",
                )
                if next_url:
                    return redirect(next_url)
                return redirect("tournament_detail", slug=tournament.slug)

    if next_url:
        return redirect(next_url)
    return redirect("tournament_list")


@login_required
@require_POST
def disable_subscription_autopay(request: HttpRequest) -> HttpResponse:
    """Отключить автопродление подписки и отвязать сохранённую карту.

    Функция не влияет на текущий оплаченный период подписки: доступ к сервису
    сохраняется до окончания ``end_date``. Мы помечаем сохранённые способы
    оплаты как неактивные и удаляем флаг использования для автопродления.

    Args:
        request (HttpRequest): Текущий HTTP-запрос.

    Returns:
        HttpResponse: Редирект на страницу профиля пользователя.
    """
    methods_qs = SavedPaymentMethod.objects.filter(
        user=request.user,
        club__isnull=True,
        is_active=True,
        is_default_for_subscriptions=True,
    )
    if not methods_qs.exists():
        messages.info(
            request,
            "У вас нет активных сохранённых способов оплаты для автопродления.",
        )
    else:
        for method in methods_qs:
            method.deactivate_for_subscriptions()
        messages.success(
            request,
            "Автопродление подписки отключено, карта отвязана от автоплатежей.",
        )

    player = getattr(request.user, "player", None)
    if player:
        return redirect("profile", pk=player.pk)
    return redirect("pricing")
