import logging
from decimal import Decimal
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.subscriptions.models import SubscriptionTier
from apps.tournaments.models import Tournament

from .forms import DonateForm
from .yookassa_client import create_payment, get_payment_status

logger = logging.getLogger(__name__)


def _is_offer_accepted(request) -> bool:
    """Проверяет, подтверждена ли оферта в POST-форме оплаты."""
    raw = str(request.POST.get("offer_accepted", "")).strip().lower()
    return raw in {"1", "true", "on", "yes"}


def _build_preview_redirect(request, payment_type: str | None):
    """Возвращает redirect на страницу предпросмотра с восстановлением параметров платежа."""
    params: dict[str, str] = {}
    if payment_type:
        params["type"] = payment_type

    # subscription / tournament
    item_id = request.POST.get("id") or request.GET.get("id")
    if item_id:
        params["id"] = str(item_id)

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


def donate_view(request):
    """Страница доната — доступна всем пользователям."""
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


def payment_preview(request):
    """Предпросмотр платежа. Для доната доступно всем, для остальных типов требуется авторизация."""
    payment_type = request.GET.get("type")

    # Для подписок и турниров требуется авторизация
    if (
        payment_type in ("subscription", "tournament")
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

    elif payment_type == "tournament":
        tournament_id = request.GET.get("id")
        tournament = get_object_or_404(Tournament, pk=tournament_id)
        next_url = request.GET.get("next", "").strip()

        # Админ не платит за турниры — сразу считаем участие подтверждённым
        if request.user.is_authenticated and (
            getattr(request.user, "is_staff", False)
            or getattr(request.user, "is_superuser", False)
        ):
            from urllib.parse import urlencode

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
        entry_fee = tournament.entry_fee or 0
        discount = 0
        if (
            request.user.is_authenticated
            and hasattr(request.user, "subscription")
            and request.user.subscription.is_valid()
        ):
            discount_percent = (
                request.user.subscription.tier.one_day_tournament_discount
            )
            if discount_percent > 0:
                discount = entry_fee * (Decimal(discount_percent) / 100)
                entry_fee = entry_fee - discount

        discount_int = int(discount) if discount else 0
        context = {
            "title": f"Турнир: {tournament.name}",
            "description": f"Взнос за участие в турнире {tournament.get_city_display() if hasattr(tournament, 'get_city_display') else tournament.city}",
            "amount": entry_fee,
            "amount_value": f"{entry_fee:.2f}",
            "item_id": tournament.id,
            "payment_next_url": next_url,
            "details": [
                ("Турнир", tournament.name),
                ("Дата", tournament.start_date),
                ("Город", tournament.city),
                ("Скидка", f"{discount_int} ₽" if discount_int else "Нет"),
            ],
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

    return render(request, "payments/preview.html", context)


def payment_process(request):
    """Обработка платежа. Для доната доступно всем, для остальных типов требуется авторизация."""
    payment_type = request.POST.get("type") or request.GET.get("type")

    # Для подписок и турниров требуется авторизация
    if (
        payment_type in ("subscription", "tournament")
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

    # Сумма и описание для ЮKassa (нормализуем запятую в точку — форма может отдать "100,00" в русской локали)
    amount_raw = (request.POST.get("amount") or "").strip().replace(",", ".")
    try:
        amount_decimal = Decimal(amount_raw or "0")
    except Exception:
        amount_decimal = Decimal("0")
    if amount_decimal <= 0:
        messages.error(request, "Укажите корректную сумму оплаты.")
        return _build_preview_redirect(request, payment_type)

    amount_str = f"{amount_decimal:.2f}"
    item_id = request.POST.get("id", "").strip()
    next_url = request.POST.get("next", "").strip()

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
    elif payment_type == "tournament":
        description = "Взнос за участие в турнире TennisFan"
    else:
        description = "Оплата на TennisFan"

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

    return_url_absolute = request.build_absolute_uri(reverse("payment_return"))
    metadata = {
        "payment_type": payment_type,
        "item_id": item_id or "",
        "next": next_url or "",
    }
    if request.user.is_authenticated:
        metadata["user_id"] = str(request.user.pk)

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
        payment_id, confirmation_url = create_payment(
            amount=amount_str,
            return_url=return_url_absolute,
            description=description[:128],
            metadata=metadata,
            customer_email=receipt_email,
        )
    except (ValueError, RuntimeError) as e:
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
    }
    if payment_type == "donation":
        pending_data["amount"] = amount_str
        pending_data["name_or_email"] = request.POST.get("name_or_email", "").strip()
        pending_data["comment"] = request.POST.get("comment", "").strip()
    else:
        # Подписка и турнир: сохраняем фактическую сумму (региональная цена, акция 1 ₽ и т.д.)
        pending_data["amount"] = amount_str
    request.session["yookassa_pending"] = pending_data
    request.session.modified = True
    return redirect(confirmation_url)


def payment_return(request):
    """
    Страница возврата после оплаты в ЮKassa.
    ЮKassa перенаправляет сюда пользователя по return_url. Проверяем статус платежа
    и редиректим на payment_success при успехе.
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

    status = get_payment_status(payment_id) if payment_id else None
    if status != "succeeded":
        messages.error(
            request,
            "Оплата не была завершена или отменена. Попробуйте снова или выберите другой способ.",
        )
        del request.session["yookassa_pending"]
        request.session.modified = True
        params = {}
        if payment_type:
            params["type"] = payment_type
        if item_id:
            params["id"] = item_id
        if params:
            return redirect(reverse("payment_preview") + "?" + urlencode(params))
        return redirect("donate")

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

    del request.session["yookassa_pending"]
    request.session.modified = True

    # Донат от гостя — просто благодарим и редирект на главную
    if payment_type == "donation" and not request.user.is_authenticated:
        messages.success(request, "Спасибо за поддержку проекта!")
        return redirect("home")

    success_url = reverse("payment_success")
    params = []
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
        success_url += "?" + urlencode(params)
    return redirect(success_url)


def payment_success(request):
    """
    Редирект после успешной оплаты (вызывается платёжным шлюзом).
    GET: type=tournament, id=<tournament_id>, next=<url>.
    Для турнира: записываем оплату в сессию (для парной регистрации),
    для одиночного — добавляем участника и редирект на страницу турнира.
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
                try:
                    from apps.core.telegram_notify import notify_subscription_purchase

                    notify_subscription_purchase(
                        request.user,
                        tier,
                        amount_paid=request.GET.get("amount"),
                    )
                except Exception as e:
                    logger.warning(
                        "Telegram notify_subscription_purchase failed: %s", e
                    )
                messages.success(
                    request, f"Подписка «{tier.get_name_display()}» успешно оформлена."
                )
                try:
                    return redirect("profile", pk=request.user.player.pk)
                except Exception:
                    return redirect("pricing")

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
