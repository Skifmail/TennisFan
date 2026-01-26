"""
Users views.
"""

import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import PlayerProfileForm, PlayerRegistrationForm, UserRegistrationForm
from .models import Notification, Player, SkillLevel, User


def _map_ntrp_to_skill_level(level: Decimal) -> str:
    normalized = int(level.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if normalized <= 2:
        return SkillLevel.NOVICE
    if normalized <= 4:
        return SkillLevel.AMATEUR
    if normalized == 5:
        return SkillLevel.EXPERIENCED
    if normalized == 6:
        return SkillLevel.ADVANCED
    return SkillLevel.PROFESSIONAL


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


def ntrp_test(request):
    """Public NTRP test page."""
    can_save = request.user.is_authenticated
    return render(request, 'users/ntrp_test.html', {'can_save': can_save})


@login_required
@require_POST
def save_ntrp(request):
    """Save NTRP level for the authenticated user's player profile."""
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "invalid_json"}, status=400)

    raw_level = payload.get("ntrp_level")
    if raw_level is None:
        return JsonResponse({"ok": False, "error": "missing_level"}, status=400)

    try:
        level = Decimal(str(raw_level))
    except (InvalidOperation, ValueError):
        return JsonResponse({"ok": False, "error": "invalid_level"}, status=400)

    if level < Decimal("1.0") or level > Decimal("7.0"):
        return JsonResponse({"ok": False, "error": "out_of_range"}, status=400)

    try:
        player = request.user.player
    except Player.DoesNotExist:
        return JsonResponse({"ok": False, "error": "player_not_found"}, status=404)

    player.ntrp_level = level
    player.skill_level = _map_ntrp_to_skill_level(level)
    player.save(update_fields=["ntrp_level", "skill_level"])
    return JsonResponse({"ok": True, "ntrp_level": f"{level:.1f}"})
