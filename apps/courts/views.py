"""
Courts views.
"""

from django.shortcuts import get_object_or_404, render

from .models import Court


def court_list(request):
    """List of courts."""
    city = request.GET.get('city', '')
    surface = request.GET.get('surface', '')

    courts = Court.objects.filter(is_active=True)

    if city:
        courts = courts.filter(city=city)
    if surface:
        courts = courts.filter(surface=surface)

    context = {
        'courts': courts,
        'current_city': city,
        'current_surface': surface,
    }
    return render(request, 'courts/list.html', context)


def court_detail(request, slug):
    """Court detail page."""
    court = get_object_or_404(Court, slug=slug, is_active=True)
    recent_matches = court.matches.select_related(
        'player1__user', 'player2__user'
    ).order_by('-scheduled_datetime')[:10]
    context = {
        'court': court,
        'recent_matches': recent_matches,
    }
    return render(request, 'courts/detail.html', context)
