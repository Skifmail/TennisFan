from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import Http404
from django.urls import reverse
from urllib.parse import urlencode

from apps.core.decorators import login_required_with_message
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
    if request.method == 'POST':
        form = DonateForm(request.POST, user=request.user)
        if form.is_valid():
            params = {
                'type': 'donation',
                'amount': form.cleaned_data['amount'],
                'comment': form.cleaned_data.get('comment', ''),
            }
            # Добавляем имя/email если указано
            name_or_email = form.cleaned_data.get('name_or_email', '').strip()
            if name_or_email:
                params['name_or_email'] = name_or_email
            base_url = reverse('payment_preview')
            query_string = urlencode(params)
            return redirect(f'{base_url}?{query_string}')
    else:
        form = DonateForm(user=request.user)
        # Если пользователь авторизован, предзаполняем имя/email
        if request.user.is_authenticated:
            if request.user.get_full_name():
                form.fields['name_or_email'].initial = request.user.get_full_name()
            elif request.user.email:
                form.fields['name_or_email'].initial = request.user.email
    
    return render(request, 'payments/donate.html', {'form': form})


def payment_preview(request):
    """Предпросмотр платежа. Для доната доступно всем, для остальных типов требуется авторизация."""
    payment_type = request.GET.get('type')
    
    # Для подписок и турниров требуется авторизация
    if payment_type in ('subscription', 'tournament') and not request.user.is_authenticated:
        from django.conf import settings
        messages.info(request, "Для оплаты необходимо зарегистрироваться.")
        login_url = getattr(settings, 'LOGIN_URL', 'login')
        next_url = request.get_full_path()
        return redirect(f"{reverse(login_url)}?next={next_url}")
    
    context = {}
    
    if payment_type == 'subscription':
        tier_id = request.GET.get('id')
        tier = get_object_or_404(SubscriptionTier, pk=tier_id)
        context = {
            'title': f"Подписка: {tier.get_name_display()}",
            'description': "Ежемесячная подписка на сервис TennisFan",
            'amount': tier.price,
            'item_id': tier.id,
            'details': [
                ('Тариф', tier.get_name_display()),
                ('Срок действия', '1 месяц'),
            ]
        }
        
    elif payment_type == 'tournament':
        tournament_id = request.GET.get('id')
        tournament = get_object_or_404(Tournament, pk=tournament_id)
        
        # Calculate price (handle discount if user has subscription)
        entry_fee = tournament.entry_fee or 0
        discount = 0
        if request.user.is_authenticated and hasattr(request.user, 'subscription') and request.user.subscription.is_valid():
             discount_percent = request.user.subscription.tier.one_day_tournament_discount
             if discount_percent > 0:
                 from decimal import Decimal
                 discount = entry_fee * (Decimal(discount_percent) / 100)
                 entry_fee = entry_fee - discount

        context = {
            'title': f"Турнир: {tournament.name}",
            'description': f"Взнос за участие в турнире {tournament.get_city_display() if hasattr(tournament, 'get_city_display') else tournament.city}",
            'amount': entry_fee,
            'item_id': tournament.id,
            'details': [
                ('Турнир', tournament.name),
                ('Дата', tournament.start_date),
                ('Город', tournament.city),
                ('Скидка', f"{discount} руб." if discount else "Нет"),
            ]
        }

    elif payment_type == 'donation':
        amount = request.GET.get('amount')
        comment = request.GET.get('comment', '')
        name_or_email = request.GET.get('name_or_email', '')
        
        details = [('Тип', 'Донат')]
        if name_or_email:
            details.append(('Имя/Email', name_or_email))
        if comment:
            details.append(('Комментарий', comment))
        else:
            details.append(('Комментарий', 'Нет комментария'))
        
        context = {
            'title': "Поддержка проекта (Донат)",
            'description': "Добровольный взнос на развитие проекта",
            'amount': amount,
            'comment': comment,
            'name_or_email': name_or_email,
            'details': details
        }
    
    else:
        raise Http404("Unknown payment type")
    
    context['payment_type'] = payment_type
    context['process_url'] = reverse('payment_process')
    
    return render(request, 'payments/preview.html', context)

def payment_process(request):
    """Обработка платежа. Для доната доступно всем, для остальных типов требуется авторизация."""
    payment_type = request.POST.get('type') or request.GET.get('type')
    
    # Для подписок и турниров требуется авторизация
    if payment_type in ('subscription', 'tournament') and not request.user.is_authenticated:
        from django.conf import settings
        messages.info(request, "Для оплаты необходимо зарегистрироваться.")
        login_url = getattr(settings, 'LOGIN_URL', 'login')
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
