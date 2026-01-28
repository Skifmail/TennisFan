"""
Courts views.
"""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from .forms import CourtApplicationForm
from .models import Court


def court_list(request):
    """List of courts."""
    city = request.GET.get('city', '')
    surface = request.GET.get('surface', '')

    courts = Court.objects.filter(is_active=True)

    if city:
        courts = courts.filter(city__icontains=city)
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


def court_application_create(request):
    """Подача заявки на добавление корта. Поля как в админке."""
    if request.method == "POST":
        form = CourtApplicationForm(request.POST, request.FILES)
        if form.is_valid():
            app = form.save()
            from apps.core.telegram_notify import notify_court_application

            notify_court_application(app)
            messages.success(
                request,
                "Заявка отправлена. Мы рассмотрим её и свяжемся с вами. "
                "После одобрения корт появится на сайте.",
            )
            return redirect("court_application_success")
    else:
        form = CourtApplicationForm()
    return render(
        request,
        "courts/application_form.html",
        {"form": form},
    )


def court_application_success(request):
    """Страница после успешной отправки заявки."""
    return render(request, "courts/application_success.html")
