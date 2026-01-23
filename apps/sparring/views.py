"""
Sparring views.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from .forms import SparringRequestForm
from .models import SparringRequest


def sparring_list(request):
    """List of sparring requests."""
    city = request.GET.get('city', '')
    category = request.GET.get('category', '')

    requests = SparringRequest.objects.filter(
        status=SparringRequest.Status.ACTIVE
    ).select_related('player__user')

    if city:
        requests = requests.filter(city=city)
    if category:
        requests = requests.filter(desired_category=category)

    context = {
        'sparring_requests': requests,
        'current_city': city,
        'current_category': category,
    }
    return render(request, 'sparring/list.html', context)


@login_required
def sparring_create(request):
    """Create sparring request."""
    try:
        player = request.user.player
    except AttributeError:
        messages.error(request, 'Заполните профиль игрока.')
        return redirect('profile_edit')

    if request.method == 'POST':
        form = SparringRequestForm(request.POST)
        if form.is_valid():
            sparring = form.save(commit=False)
            sparring.player = player
            sparring.save()
            messages.success(request, 'Заявка на спарринг создана.')
            return redirect('sparring_list')
    else:
        form = SparringRequestForm(initial={'city': player.city})

    return render(request, 'sparring/create.html', {'form': form})
