"""
Training views.
"""

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify
from django.views.decorators.http import require_http_methods, require_POST

from .forms import CoachApplicationForm, TrainingEnrollmentForm, TrainingForm
from .models import (
    Coach,
    CoachApplication,
    Training,
    TrainingEnrollment,
)

logger = logging.getLogger(__name__)


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
        trainings = trainings.filter(city__icontains=city)

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
        messages.error(request, "Заполните профиль игрока.")
        return redirect("profile_edit")

    if request.method == "POST":
        form = TrainingEnrollmentForm(request.POST)
        if form.is_valid():
            enrollment = form.save(commit=False)
            enrollment.training = training
            enrollment.player = player
            enrollment.save()
            messages.success(request, "Заявка на тренировку отправлена!")
            return redirect("training_detail", slug=slug)
    else:
        initial = {}
        user = request.user
        if user.get_full_name():
            initial["full_name"] = user.get_full_name().strip()
        if user.email:
            initial["email"] = user.email
        if hasattr(player, "telegram") and player.telegram:
            initial["telegram"] = player.telegram
        if hasattr(player, "whatsapp") and player.whatsapp:
            initial["whatsapp"] = player.whatsapp
        form = TrainingEnrollmentForm(initial=initial)

    return render(request, "training/enroll.html", {"training": training, "form": form})


def coach_list(request):
    """List of coaches."""
    city = request.GET.get('city', '')

    coaches = Coach.objects.filter(is_active=True)
    if city:
        coaches = coaches.filter(city__icontains=city)

    context = {'coaches': coaches, 'current_city': city}
    return render(request, 'training/coach_list.html', context)


@login_required
def coach_application_create(request):
    """Подача заявки «Стать тренером». Только для зарегистрированных пользователей."""
    if getattr(request.user, "coach", None):
        messages.info(request, "Вы уже тренер.")
        return redirect("my_trainings")

    if request.method == "POST":
        form = CoachApplicationForm(request.POST, request.FILES)
        if form.is_valid():
            app = form.save(commit=False)
            app.applicant_user = request.user
            app.save()
            from apps.core.telegram_notify import notify_coach_application

            notify_coach_application(app)
            messages.success(
                request,
                "Заявка отправлена. Мы рассмотрим её и свяжемся с вами. "
                "После одобрения вы появитесь в разделе «Наши тренеры».",
            )
            return redirect("coach_application_success")
    else:
        user = request.user
        initial = {}
        if user.get_full_name():
            initial["applicant_name"] = user.get_full_name().strip()
        if user.email:
            initial["applicant_email"] = user.email
        form = CoachApplicationForm(initial=initial)

    return render(
        request,
        "training/coach_application_form.html",
        {"form": form},
    )


def coach_application_success(request):
    """Страница после успешной отправки заявки «Стать тренером»."""
    return render(request, "training/coach_application_success.html")


def coach_detail(request, slug):
    """Coach detail page."""
    coach = get_object_or_404(Coach, slug=slug, is_active=True)
    trainings = coach.trainings.filter(is_active=True)
    return render(request, "training/coach_detail.html", {"coach": coach, "trainings": trainings})


def _enrollment_contact_url(enrollment, method: str) -> str | None:
    """Return redirect URL for contacting enrollee (telegram, whatsapp, email) or None."""
    if method == "telegram" and enrollment.telegram_url:
        return enrollment.telegram_url
    if method == "whatsapp" and enrollment.whatsapp_url:
        return enrollment.whatsapp_url
    if method == "email" and enrollment.email:
        return f"mailto:{enrollment.email}"
    return None


@login_required
def my_trainings(request):
    """Мои тренировки: для тренера — свои тренировки и записи; для пользователя — свои заявки."""
    coach = getattr(request.user, "coach", None)
    try:
        player = request.user.player
    except Exception:
        player = None

    coach_trainings = []
    my_enrollments = []

    if coach:
        coach_trainings = (
            Training.objects.filter(coach=coach)
            .prefetch_related("enrollments")
            .order_by("-created_at")
        )
    if player:
        my_enrollments = (
            TrainingEnrollment.objects.filter(player=player)
            .select_related("training")
            .order_by("-created_at")
        )

    return render(
        request,
        "training/my_trainings.html",
        {
            "coach": coach,
            "coach_trainings": coach_trainings,
            "my_enrollments": my_enrollments,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def training_add(request):
    """Добавить тренировку (только тренер)."""
    coach = getattr(request.user, "coach", None)
    if not coach:
        messages.error(request, "Доступно только тренерам.")
        return redirect("my_trainings")

    if request.method == "POST":
        form = TrainingForm(request.POST, request.FILES)
        if form.is_valid():
            t = form.save(commit=False)
            t.coach = coach
            base = slugify(t.title, allow_unicode=True) or "training"
            slug = base
            n = 0
            while Training.objects.filter(slug=slug).exists():
                n += 1
                slug = f"{base}-{n}"
            t.slug = slug
            t.save()
            messages.success(request, f"Тренировка «{t.title}» создана.")
            return redirect("my_trainings")
    else:
        form = TrainingForm(initial={"city": coach.city} if coach.city else {})

    return render(request, "training/training_form.html", {"form": form, "training": None})


@login_required
@require_http_methods(["GET", "POST"])
def training_edit(request, pk):
    """Редактировать тренировку (только свой, тренер)."""
    coach = getattr(request.user, "coach", None)
    if not coach:
        messages.error(request, "Доступно только тренерам.")
        return redirect("my_trainings")

    training = get_object_or_404(Training, pk=pk)
    if training.coach_id != coach.id:
        messages.error(request, "Нельзя редактировать чужую тренировку.")
        return redirect("my_trainings")

    if request.method == "POST":
        form = TrainingForm(request.POST, request.FILES, instance=training)
        if form.is_valid():
            form.save()
            messages.success(request, "Тренировка обновлена.")
            return redirect("my_trainings")
    else:
        form = TrainingForm(instance=training)

    return render(request, "training/training_form.html", {"form": form, "training": training})


@login_required
def enrollment_contact(request, pk, method):
    """Связаться с клиентом (Telegram/WhatsApp/Email). Редирект + статус «Связались»."""
    coach = getattr(request.user, "coach", None)
    if not coach:
        messages.error(request, "Доступно только тренерам.")
        return redirect("my_trainings")

    enrollment = get_object_or_404(
        TrainingEnrollment.objects.select_related("training"),
        pk=pk,
    )
    if enrollment.training.coach_id != coach.id:
        messages.error(request, "Нет доступа к этой заявке.")
        return redirect("my_trainings")

    url = _enrollment_contact_url(enrollment, method)
    if not url:
        messages.error(request, "Контакт недоступен.")
        return redirect("my_trainings")

    enrollment.status = TrainingEnrollment.Status.CONTACTED
    enrollment.save(update_fields=["status", "updated_at"])

    return redirect(url)


@login_required
@require_POST
def enrollment_delete(request, pk):
    """Удалить свою заявку на тренировку."""
    try:
        player = request.user.player
    except Exception:
        messages.error(request, "Профиль игрока не найден.")
        return redirect("my_trainings")

    enrollment = get_object_or_404(TrainingEnrollment, pk=pk)
    if enrollment.player_id != player.id:
        messages.error(request, "Нельзя удалить чужую заявку.")
        return redirect("my_trainings")

    training_title = enrollment.training.title
    enrollment.delete()
    messages.success(request, f"Заявка на «{training_title}» удалена.")
    return redirect("my_trainings")
