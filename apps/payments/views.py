from urllib.parse import urlencode

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.subscriptions.models import SubscriptionTier
from apps.tournaments.models import Tournament

from .forms import DonateForm


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
        from decimal import Decimal

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
            ("Срок действия", "1 месяц"),
        ]
        if first_time_one_ruble:
            details.append(("Акция", "Первая подписка за 1 ₽"))
        context = {
            "title": f"Подписка: {tier.get_name_display()}",
            "description": "Ежемесячная подписка на сервис TennisFan",
            "amount": amount,
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
                from decimal import Decimal

                discount = entry_fee * (Decimal(discount_percent) / 100)
                entry_fee = entry_fee - discount

        discount_int = int(discount) if discount else 0
        context = {
            "title": f"Турнир: {tournament.name}",
            "description": f"Взнос за участие в турнире {tournament.get_city_display() if hasattr(tournament, 'get_city_display') else tournament.city}",
            "amount": entry_fee,
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

        details = [("Тип", "Донат")]
        if name_or_email:
            details.append(("Имя/Email", name_or_email))
        if comment:
            details.append(("Комментарий", comment))
        else:
            details.append(("Комментарий", "Нет комментария"))

        context = {
            "title": "Поддержка проекта (Донат)",
            "description": "Добровольный взнос на развитие проекта",
            "amount": amount,
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
        from django.conf import settings

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

    # Пока платежный шлюз не подключен
    raise Http404("Payment gateway not connected")


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

    if payment_type == "subscription" and item_id:
        try:
            tier_id = int(item_id)
        except (TypeError, ValueError):
            tier_id = None
        if tier_id is not None:
            from dateutil.relativedelta import relativedelta

            tier = SubscriptionTier.objects.filter(pk=tier_id).first()
            if tier:
                from apps.subscriptions.models import UserSubscription
                from apps.subscriptions.views import _mark_user_paid_subscription

                sub, _ = UserSubscription.objects.get_or_create(
                    user=request.user,
                    defaults={"tier": tier, "end_date": timezone.now()},
                )
                sub.tier = tier
                sub.start_date = timezone.now()
                sub.end_date = sub.start_date + relativedelta(months=1)
                sub.is_active = True
                sub.cancelled_at = None
                sub.tournaments_registered_count = 0
                sub.save()
                _mark_user_paid_subscription(request.user)
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
                        request, "Оплата прошла успешно. Вы зарегистрированы на турнир."
                    )
                return redirect("tournament_detail", slug=tournament.slug)
            if tournament and tournament.is_doubles() and next_url:
                return redirect(next_url)

    if next_url:
        return redirect(next_url)
    return redirect("tournament_list")
