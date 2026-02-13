"""
Sparring views.
"""

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.core.decorators import login_required_with_message, require_filled_profile
from apps.users.models import Player

from .forms import SparringRequestForm
from .models import SparringRequest, SparringResponse
from .utils import user_has_sparring_access

# Импорт для Telegram уведомлений
try:
    from apps.telegram_bot.notifications import notify_sparring_response
except ImportError:
    notify_sparring_response = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def _get_contact_url(player: Player, method: str) -> str | None:
    """Return contact URL for player and method (telegram/whatsapp/max), or None."""
    # TextChoices возвращает кортеж (value, label), используем строковые значения напрямую
    if method == "telegram" and player.telegram:
        uname = player.telegram.strip().lstrip("@")
        return f"https://t.me/{uname}"
    if method == "whatsapp" and player.whatsapp:
        phone = "".join(c for c in player.whatsapp if c.isdigit())
        if phone.startswith("8") and len(phone) == 11:
            phone = "7" + phone[1:]
        elif phone.startswith("7") and len(phone) == 11:
            pass
        elif len(phone) == 10:
            phone = "7" + phone
        else:
            return None
        return f"https://wa.me/{phone}"
    if method == "max":
        return player.max_url
    return None


@login_required_with_message(
    "Раздел спарринга доступен только для зарегистрированных пользователей."
)
def sparring_list(request):
    """List of active sparring requests. Только для авторизованных пользователей."""
    city = request.GET.get("city", "")
    category = request.GET.get("category", "")

    requests_qs = (
        SparringRequest.objects.filter(status=SparringRequest.Status.ACTIVE)
        .select_related(
            "player__user",
            "player__user__subscription",
            "player__user__subscription__tier",
        )
        .prefetch_related("responses")
    )
    if city:
        requests_qs = requests_qs.filter(city__icontains=city)
    if category:
        requests_qs = requests_qs.filter(desired_category=category)

    has_access = user_has_sparring_access(request.user)
    context = {
        "sparring_requests": requests_qs,
        "current_city": city,
        "current_category": category,
        "has_sparring_access": has_access,
    }
    return render(request, "sparring/list.html", context)


@login_required
@require_filled_profile
def sparring_create(request):
    """Create sparring request. Requires has_sparring_access."""
    if not user_has_sparring_access(request.user):
        messages.error(
            request,
            "Доступ к спаррингам предоставляется по подписке. Оформите подписку Silver, Gold или Diamond.",
        )
        return redirect("pricing")

    try:
        player = request.user.player
    except (AttributeError, Player.DoesNotExist):
        messages.error(request, "Заполните профиль игрока.")
        return redirect("profile_edit")

    if request.method == "POST":
        form = SparringRequestForm(request.POST)
        if form.is_valid():
            sparring = form.save(commit=False)
            sparring.player = player
            sparring.save()
            messages.success(request, "Заявка на спарринг создана.")
            return redirect("sparring_list")
    else:
        form = SparringRequestForm(initial={"city": player.city})

    return render(request, "sparring/create.html", {"form": form})


@require_http_methods(["GET", "POST"])
@login_required
def sparring_edit(request, pk):
    """Edit own sparring request."""
    if not user_has_sparring_access(request.user):
        messages.error(request, "Нет доступа к спаррингам.")
        return redirect("sparring_list")

    sparring = get_object_or_404(SparringRequest, pk=pk)
    if sparring.player.user_id != request.user.id:
        raise Http404

    if request.method == "POST":
        form = SparringRequestForm(request.POST, instance=sparring)
        if form.is_valid():
            form.save()
            messages.success(request, "Заявка обновлена.")
            return redirect("sparring_my_requests")
    else:
        form = SparringRequestForm(instance=sparring)

    return render(request, "sparring/edit.html", {"form": form, "sparring": sparring})


@require_POST
@login_required
def sparring_delete(request, pk):
    """Delete own sparring request."""
    sparring = get_object_or_404(SparringRequest, pk=pk)
    if sparring.player.user_id != request.user.id:
        raise Http404
    sparring.delete()
    messages.success(request, "Заявка удалена.")
    return redirect("sparring_my_requests")


@require_POST
@login_required
def sparring_cancel(request, pk):
    """Cancel (close) own sparring request."""
    sparring = get_object_or_404(SparringRequest, pk=pk)
    if sparring.player.user_id != request.user.id:
        raise Http404
    sparring.status = SparringRequest.Status.CLOSED
    sparring.save()
    messages.success(request, "Заявка отменена.")
    return redirect("sparring_my_requests")


@login_required
def sparring_my_requests(request):
    """List current user's sparring requests (active + closed)."""
    try:
        player = request.user.player
    except (AttributeError, Player.DoesNotExist):
        messages.error(request, "Заполните профиль игрока.")
        return redirect("profile_edit")

    requests_qs = (
        SparringRequest.objects.filter(player=player)
        .prefetch_related("responses__respondent__user")
        .order_by("-created_at")
    )
    has_access = user_has_sparring_access(request.user)
    return render(
        request,
        "sparring/my_requests.html",
        {"sparring_requests": requests_qs, "has_sparring_access": has_access},
    )


@require_POST
@login_required
def sparring_confirm_response(request, response_id):
    """
    Подтвердить отклик и создать матч из спарринга.
    POST /sparring/response/<id>/confirm/
    """
    if not user_has_sparring_access(request.user):
        messages.error(request, "Нет доступа к спаррингам.")
        return redirect("sparring_list")

    try:
        response = SparringResponse.objects.select_related(
            "sparring_request__player", "respondent"
        ).get(pk=response_id)
    except SparringResponse.DoesNotExist:
        messages.error(request, "Отклик не найден.")
        return redirect("sparring_my_requests")

    # Проверяем, что пользователь - автор заявки
    if response.sparring_request.player.user_id != request.user.id:
        messages.error(request, "Вы не можете подтвердить этот отклик.")
        return redirect("sparring_my_requests")

    # Проверяем, что отклик еще не принят
    if response.status == SparringResponse.ResponseStatus.ACCEPTED:
        messages.warning(request, "Этот отклик уже принят.")
        return redirect("sparring_my_requests")

    # Проверяем, что заявка еще активна
    if response.sparring_request.status != SparringRequest.Status.ACTIVE:
        messages.error(request, "Заявка уже закрыта.")
        return redirect("sparring_my_requests")

    try:
        from apps.sparring.services import create_match_from_response

        match = create_match_from_response(response)

        # Обновляем статус отклика
        response.status = SparringResponse.ResponseStatus.ACCEPTED
        response.save(update_fields=["status"])

        # Закрываем заявку
        response.sparring_request.status = SparringRequest.Status.CLOSED
        response.sparring_request.save(update_fields=["status"])

        messages.success(
            request,
            f"Матч создан! Вы играете с {response.respondent}. Дедлайн: {match.deadline.strftime('%d.%m.%Y')}.",
        )
        logger.info(
            "Sparring match created: %s from response %s by user %s",
            match.pk,
            response.pk,
            request.user.id,
        )
    except ValueError as e:
        messages.error(request, str(e))
        logger.error("Failed to create sparring match: %s", e)
    except Exception:
        messages.error(request, "Ошибка при создании матча.")
        logger.exception(
            "Failed to create sparring match from response %s", response.pk
        )

    return redirect("sparring_my_requests")


@require_GET
@login_required
@require_filled_profile
def sparring_respond(request, pk):
    """
    Record response and redirect to contact URL.
    GET /sparring/<id>/respond/?method=telegram|whatsapp|max
    """
    if not user_has_sparring_access(request.user):
        messages.error(request, "Доступ к спаррингам по подписке.")
        return redirect("pricing")

    sparring = get_object_or_404(
        SparringRequest.objects.select_related("player"),
        pk=pk,
        status=SparringRequest.Status.ACTIVE,
    )
    method = (request.GET.get("method") or "").lower()
    valid_methods = ("telegram", "whatsapp", "max")
    if method not in valid_methods:
        messages.error(request, "Укажите способ связи: telegram, whatsapp или max.")
        return redirect("sparring_list")

    try:
        respondent = request.user.player
    except (AttributeError, Player.DoesNotExist):
        messages.error(request, "Заполните профиль игрока.")
        return redirect("profile_edit")

    if respondent.id == sparring.player_id:
        messages.error(request, "Нельзя откликнуться на свою заявку.")
        return redirect("sparring_list")

    obj, created = SparringResponse.objects.update_or_create(
        sparring_request=sparring,
        respondent=respondent,
        defaults={
            "contact_method": method,
            "status": SparringResponse.ResponseStatus.PENDING,
        },
    )

    # Отправляем уведомление автору заявки в Telegram
    if created and notify_sparring_response is not None:
        try:
            notify_sparring_response(obj)
        except Exception as e:
            logger.warning(
                "Failed to send Telegram notification for sparring response: %s", e
            )

    url = _get_contact_url(sparring.player, method)
    if url:
        return redirect(url)
    if created:
        messages.success(request, "Отклик записан. Автор заявки получит уведомление.")
    return redirect("sparring_list")
