from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import Http404
from django.urls import reverse
from urllib.parse import urlencode

from apps.subscriptions.models import SubscriptionTier
from apps.tournaments.models import Tournament
from .forms import DonateForm

def donate_view(request):
    if request.method == 'POST':
        form = DonateForm(request.POST)
        if form.is_valid():
            params = {
                'type': 'donation',
                'amount': form.cleaned_data['amount'],
                'comment': form.cleaned_data['comment']
            }
            base_url = reverse('payment_preview')
            query_string = urlencode(params)
            return redirect(f'{base_url}?{query_string}')
    else:
        form = DonateForm()
    
    return render(request, 'payments/donate.html', {'form': form})

def payment_preview(request):
    payment_type = request.GET.get('type')
    context = {}
    
    if payment_type == 'subscription':
        tier_id = request.GET.get('id')
        tier = get_object_or_404(SubscriptionTier, pk=tier_id)
        context = {
            'title': f"Подписка: {tier.get_name_display()}",
            'description': "Ежемесячная подписка на сервис TennisFan",
            'amount': tier.price,
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
        context = {
            'title': "Поддержка проекта (Донат)",
            'description': "Добровольный взнос на развитие проекта",
            'amount': amount,
            'comment': comment,
            'details': [
                ('Тип', 'Донат'),
                ('Комментарий', comment if comment else 'Нет комментария')
            ]
        }
    
    else:
        raise Http404("Unknown payment type")
    
    context['payment_type'] = payment_type
    context['process_url'] = reverse('payment_process')
    
    return render(request, 'payments/preview.html', context)

def payment_process(request):
    raise Http404("Payment gateway not connected")
