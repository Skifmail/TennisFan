"""
Core views - main pages.
"""

import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import redirect, render
from django.utils.html import linebreaks

from apps.content.models import News, RulesSection
from apps.tournaments.models import Match, Tournament, TournamentDuration, TournamentGender, TournamentStatus
from apps.users.models import Player, SkillLevel

from .forms import FeedbackForm


def home(request):
    """Home page view. Формирование сеток по дедлайну выполняется по cron (generate_brackets_past_deadlines)."""
    tournaments = Tournament.objects.filter(
        status__in=[TournamentStatus.UPCOMING, TournamentStatus.ACTIVE]
    ).prefetch_related("participants__user", "allowed_categories")

    upcoming_tournaments = (
        Tournament.objects.filter(status=TournamentStatus.UPCOMING)
        .prefetch_related("allowed_categories")
        .order_by("start_date")[:6]
    )

    city = request.GET.get('city', '')
    category = request.GET.get('category', '')
    gender = request.GET.get('gender', '')
    duration = request.GET.get('duration', '')

    if city:
        tournaments = tournaments.filter(city__icontains=city)
    if category:
        tournaments = tournaments.filter(allowed_categories__category=category).distinct()
    if gender:
        tournaments = tournaments.filter(gender=gender)
    if duration:
        tournaments = tournaments.filter(duration=duration)

    tournaments = tournaments.order_by('start_date')

    context = {
        'filtered_tournaments': tournaments,
        'upcoming_tournaments': upcoming_tournaments,
        'top_players': Player.objects.filter(is_verified=True)
        .select_related('user', 'user__subscription', 'user__subscription__tier')
        .order_by('-total_points')[:10],
        'latest_news': News.objects.filter(is_published=True)[:4],
        'current_filters': {
            'city': city,
            'category': category,
            'gender': gender,
            'duration': duration,
        },
        'category_choices': SkillLevel.choices,
        'gender_choices': TournamentGender.choices,
        'duration_choices': TournamentDuration.choices,
    }
    return render(request, 'core/home.html', context)


def rating(request):
    """Player rating page."""
    city = request.GET.get('city', '')
    skill_level = request.GET.get('skill_level', '') or request.GET.get('category', '')
    search = request.GET.get('q', '')

    players = Player.objects.select_related(
        'user', 'user__subscription', 'user__subscription__tier'
    )

    if city:
        players = players.filter(city__icontains=city)
    if skill_level:
        players = players.filter(skill_level=skill_level)
    if search:
        players = players.filter(
            Q(user__first_name__icontains=search) |
            Q(user__last_name__icontains=search)
        )

    players = players.order_by('-total_points')

    context = {
        'players': players,
        'current_city': city,
        'current_skill_level': skill_level,
        'search_query': search,
        'skill_level_choices': SkillLevel.choices,
    }
    return render(request, 'core/rating.html', context)


def results(request):
    """Match results page."""
    matches = Match.objects.filter(
        status=Match.MatchStatus.COMPLETED
    ).select_related(
        'player1__user', 'player2__user', 'winner__user', 'tournament'
    ).order_by('-completed_datetime')[:50]
    return render(request, 'core/results.html', {'matches': matches})


def legends(request):
    """Hall of fame page."""
    legends = Player.objects.filter(is_legend=True).select_related('user')
    return render(request, 'core/legends.html', {'legends': legends})


def _is_html_content(text: str) -> bool:
    """Проверяет, похож ли текст на HTML (есть теги), чтобы не применять linebreaks."""
    if not text or "<" not in text:
        return False
    return bool(re.search(r"<\s*[a-zA-Z]", text))


def rules(request):
    """Rules page: tournament formats (FAN, etc.) with detailed descriptions. Content is editable via admin (RulesSection)."""
    rules_content = {}
    for s in RulesSection.objects.all():
        body = s.body or ""
        if body and not _is_html_content(body):
            body = linebreaks(body)
        rules_content[s.slug] = body
    return render(request, "core/rules.html", {"rules_content": rules_content})


@login_required
def feedback(request):
    """Обратная связь. Только для залогиненных. Отправляет данные в Telegram админу."""
    if request.method == "POST":
        form = FeedbackForm(request.POST)
        if form.is_valid():
            from apps.core.telegram_notify import notify_feedback

            notify_feedback(
                request.user,
                subject=form.cleaned_data.get("subject") or "",
                message=form.cleaned_data["message"],
            )
            messages.success(request, "Сообщение отправлено. Мы ответим вам на email.")
            return redirect("feedback")
    else:
        form = FeedbackForm()
    return render(request, "core/feedback.html", {"form": form})
