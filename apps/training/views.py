"""
Training views.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from .models import Coach, Training, TrainingEnrollment


def training_list(request):
    """List of trainings."""
    skill_level = request.GET.get('level', '')
    training_type = request.GET.get('type', '')
    city = request.GET.get('city', '')

    trainings = Training.objects.filter(is_active=True).select_related('coach')

    if skill_level:
        trainings = trainings.filter(skill_level=skill_level)
    if training_type:
        trainings = trainings.filter(training_type=training_type)
    if city:
        trainings = trainings.filter(city=city)

    context = {
        'trainings': trainings,
        'current_level': skill_level,
        'current_type': training_type,
        'current_city': city,
    }
    return render(request, 'training/list.html', context)


def training_detail(request, slug):
    """Training detail page."""
    training = get_object_or_404(
        Training.objects.select_related('coach', 'court'),
        slug=slug,
        is_active=True
    )
    return render(request, 'training/detail.html', {'training': training})


@login_required
def training_enroll(request, slug):
    """Enroll in training."""
    training = get_object_or_404(Training, slug=slug, is_active=True)

    try:
        player = request.user.player
    except AttributeError:
        messages.error(request, 'Заполните профиль игрока.')
        return redirect('profile_edit')

    if request.method == 'POST':
        TrainingEnrollment.objects.create(
            training=training,
            player=player,
            message=request.POST.get('message', ''),
        )
        messages.success(request, 'Заявка на тренировку отправлена!')
        return redirect('training_detail', slug=slug)

    return render(request, 'training/enroll.html', {'training': training})


def coach_list(request):
    """List of coaches."""
    city = request.GET.get('city', '')

    coaches = Coach.objects.filter(is_active=True)
    if city:
        coaches = coaches.filter(city=city)

    context = {'coaches': coaches, 'current_city': city}
    return render(request, 'training/coach_list.html', context)


def coach_detail(request, slug):
    """Coach detail page."""
    coach = get_object_or_404(Coach, slug=slug, is_active=True)
    trainings = coach.trainings.filter(is_active=True)
    return render(request, 'training/coach_detail.html', {'coach': coach, 'trainings': trainings})
