"""
Users views.
"""

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from .forms import PlayerProfileForm, PlayerRegistrationForm, UserRegistrationForm
from .models import Notification, Player, User


def register(request):
    """User registration view."""
    if request.method == 'POST':
        user_form = UserRegistrationForm(request.POST)
        player_form = PlayerRegistrationForm(request.POST)
        if user_form.is_valid() and player_form.is_valid():
            user = user_form.save()
            # Create player profile with required fields
            player = player_form.save(commit=False)
            player.user = user
            player.save()
            login(request, user)
            messages.success(request, 'Регистрация успешна! Добро пожаловать.')
            return redirect('home')
    else:
        user_form = UserRegistrationForm()
        player_form = PlayerRegistrationForm()
    return render(request, 'users/register.html', {
        'user_form': user_form,
        'player_form': player_form,
    })


def profile(request, pk):
    """User profile view."""
    player = get_object_or_404(Player.objects.select_related('user'), pk=pk)

    # Get player's recent matches
    from django.db.models import Q
    from apps.tournaments.models import Match

    # Get player's recent matches
    recent_matches = Match.objects.filter(
        Q(player1=player) | Q(player2=player)
    ).order_by('-scheduled_datetime')[:10]

    context = {
        'player': player,
        'recent_matches': recent_matches,
    }
    return render(request, 'users/profile.html', context)


@login_required
def profile_edit(request):
    """Edit profile view."""
    try:
        player = request.user.player
    except Player.DoesNotExist:
        player = Player.objects.create(user=request.user)

    if request.method == 'POST':
        form = PlayerProfileForm(request.POST, request.FILES, instance=player, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, 'Профиль обновлён.')
            return redirect('profile', pk=player.pk)
    else:
        form = PlayerProfileForm(instance=player, user=request.user)

    return render(request, 'users/profile_edit.html', {'form': form})


@login_required
def notifications(request):
    """User notifications inbox."""

    notes = Notification.objects.filter(user=request.user).order_by('-created_at')
    # mark all as read when viewed
    notes.filter(is_read=False).update(is_read=True)
    return render(request, 'users/notifications.html', {'notifications': notes})
