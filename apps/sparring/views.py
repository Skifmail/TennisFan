"""
Sparring views.
"""

import json
import logging

from django.contrib import messages  # pyright: ignore[reportMissingImports]
from django.contrib.auth.decorators import (
    login_required,
)

# pyright: ignore[reportMissingImports]
from django.http import Http404, JsonResponse  # pyright: ignore[reportMissingImports]
from django.shortcuts import (
    get_object_or_404,
    redirect,
    render,
)

# pyright: ignore[reportMissingImports]
from django.urls import reverse

# pyright: ignore[reportMissingImports]
from django.views.decorators.http import (
    require_http_methods,
    require_POST,
)

from apps.core.decorators import login_required_with_message, require_filled_profile
from apps.users.models import Notification, Player

from .doubles_services import (
    accept_join_request,
    add_partner_to_author_team,
    cancel_join_request,
    cancel_match_request,
    confirm_match,
    confirm_team_sparring_series,
    create_doubles_request,
    create_join_request,
    reject_join_request,
    remove_team_member,
)
from .forms import (
    DoublesAddPartnerForm,
    DoublesMatchRequestForm,
    SparringRequestForm,
)
from .models import (
    DoublesJoinRequest,
    DoublesJoinRequestStatus,
    DoublesMatchKind,
    DoublesMatchRequest,
    DoublesMatchRequestStatus,
    SparringMatchType,
    SparringRequest,
    SparringResponse,
    TeamSide,
)
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
    """Объединённый список заявок на спарринг: одиночный (1×1) и парный (2×2)."""
    sparring_type = request.GET.get("type", "singles")
    if sparring_type not in ("singles", "doubles", "team"):
        sparring_type = "singles"

    city = request.GET.get("city", "")
    category = request.GET.get("category", "")
    level = request.GET.get("level", "")
    preferred_gender = request.GET.get("preferred_gender", "")
    has_access = user_has_sparring_access(request.user)

    context = {
        "sparring_type": sparring_type,
        "current_city": city,
        "current_category": category,
        "current_level": level,
        "current_preferred_gender": preferred_gender,
        "has_sparring_access": has_access,
    }

    if sparring_type == "singles":
        requests_qs = (
            SparringRequest.objects.filter(
                status=SparringRequest.Status.ACTIVE,
                match_type="singles",
            )
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
        if preferred_gender:
            requests_qs = requests_qs.filter(preferred_gender=preferred_gender)
        context["sparring_requests"] = requests_qs
    else:
        doubles_qs = (
            DoublesMatchRequest.objects.filter(
                status__in=(
                    DoublesMatchRequestStatus.OPEN,
                    DoublesMatchRequestStatus.FORMING,
                    DoublesMatchRequestStatus.READY,
                )
            )
            .select_related(
                "created_by__user",
                "created_by__user__subscription",
                "created_by__user__subscription__tier",
            )
            .prefetch_related("teams__members__player__user")
            .order_by("-created_at")
        )
        if sparring_type == "doubles":
            doubles_qs = doubles_qs.filter(kind=DoublesMatchKind.CLASSIC)
        else:
            doubles_qs = doubles_qs.filter(kind=DoublesMatchKind.TEAM)
        if city:
            doubles_qs = doubles_qs.filter(city__icontains=city)
        if level:
            doubles_qs = doubles_qs.filter(desired_level=level)
        if preferred_gender:
            doubles_qs = doubles_qs.filter(preferred_gender=preferred_gender)
        context["doubles_requests"] = doubles_qs

    return render(request, "sparring/list.html", context)


@login_required
@require_filled_profile
def sparring_create(request):
    """Создать заявку на спарринг: одиночный (1×1) или парный (2×2)."""
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

    sparring_type = request.GET.get("type", "singles")
    if request.method == "POST":
        sparring_type = request.POST.get("sparring_type", "singles")

    if sparring_type not in ("singles", "doubles", "team"):
        sparring_type = "singles"

    if sparring_type in ("doubles", "team"):
        if request.method == "POST":
            form = DoublesMatchRequestForm(request.POST)
            if form.is_valid():
                try:
                    kind = (
                        DoublesMatchKind.TEAM
                        if sparring_type == "team"
                        else DoublesMatchKind.CLASSIC
                    )
                    req = create_doubles_request(
                        created_by=player,
                        city=form.cleaned_data.get("city", ""),
                        preferred_gender=form.cleaned_data.get("preferred_gender", ""),
                        is_friendly=form.cleaned_data.get("is_friendly", False),
                        description=form.cleaned_data.get("description", ""),
                        preferred_days=form.cleaned_data.get("preferred_days", ""),
                        preferred_time=form.cleaned_data.get("preferred_time", ""),
                        desired_level=form.cleaned_data.get("desired_level", ""),
                        desired_age_min=form.cleaned_data.get("desired_age_min"),
                        desired_age_max=form.cleaned_data.get("desired_age_max"),
                        preferred_location=form.cleaned_data.get(
                            "preferred_location", ""
                        ),
                        kind=kind,
                    )
                    messages.success(
                        request,
                        (
                            "Заявка на командный спарринг создана."
                            if sparring_type == "team"
                            else "Заявка на парный матч создана."
                        ),
                    )
                    return redirect("doubles_detail", pk=req.pk)
                except Exception as e:
                    messages.error(request, str(e))
        else:
            form = DoublesMatchRequestForm(
                initial={"city": getattr(player, "city", "")}
            )
    else:
        if request.method == "POST":
            form = SparringRequestForm(request.POST)
            if form.is_valid():
                sparring = form.save(commit=False)
                sparring.player = player
                sparring.match_type = SparringMatchType.SINGLES
                sparring.save()
                messages.success(request, "Заявка на спарринг создана.")
                return redirect("sparring_list")
        else:
            form = SparringRequestForm(initial={"city": player.city})

    return render(
        request,
        "sparring/create.html",
        {"form": form, "sparring_type": sparring_type},
    )


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
    """Объединённый список заявок пользователя: одиночный (1×1) и парный (2×2)."""
    try:
        player = request.user.player
    except (AttributeError, Player.DoesNotExist):
        messages.error(request, "Заполните профиль игрока.")
        return redirect("profile_edit")

    sparring_type = request.GET.get("type", "singles")
    if sparring_type not in ("singles", "doubles", "team"):
        sparring_type = "singles"

    has_access = user_has_sparring_access(request.user)
    context = {
        "sparring_type": sparring_type,
        "has_sparring_access": has_access,
    }

    if sparring_type == "singles":
        requests_qs = (
            SparringRequest.objects.filter(player=player)
            .prefetch_related("responses__respondent__user")
            .order_by("-created_at")
        )
        context["sparring_requests"] = requests_qs
    else:
        doubles_qs = (
            DoublesMatchRequest.objects.filter(created_by=player)
            .select_related("created_by__user", "match")
            .prefetch_related(
                "teams__members__player__user", "join_requests__members__player__user"
            )
            .order_by("-created_at")
        )
        if sparring_type == "doubles":
            doubles_qs = doubles_qs.filter(kind=DoublesMatchKind.CLASSIC)
        else:
            doubles_qs = doubles_qs.filter(kind=DoublesMatchKind.TEAM)
        context["doubles_requests"] = doubles_qs

    return render(request, "sparring/my_requests.html", context)


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

        # Уведомление в ЛК для откликнувшегося игрока
        try:
            deadline_str = (
                match.deadline.strftime("%d.%m.%Y")
                if match.deadline
                else "Не установлен"
            )
            Notification.objects.create(
                user=response.respondent.user,
                message=f"Ваш отклик на заявку на спарринг принят! Матч создан. Дедлайн: {deadline_str}.",
                url=reverse("match_detail", args=[match.pk]),
            )
        except Exception as e:
            logger.warning(
                "Failed to create notification for sparring response acceptance: %s", e
            )

        # Уведомление в Telegram откликнувшемуся
        try:
            from apps.telegram_bot.notifications import (
                notify_sparring_response_accepted,
            )

            notify_sparring_response_accepted(response, match)
        except Exception as e:
            logger.warning(
                "Failed to send Telegram notification for sparring response acceptance: %s",
                e,
            )

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


def _build_contact_urls(player: Player) -> dict:
    """Return dict telegram/whatsapp/max -> URL or None."""
    return {
        "telegram": _get_contact_url(player, "telegram"),
        "whatsapp": _get_contact_url(player, "whatsapp"),
        "max": _get_contact_url(player, "max"),
    }


@require_http_methods(["GET", "POST"])
@login_required
@require_filled_profile
def sparring_respond(request, pk):
    """
    Записать отклик и (при GET с method) перенаправить на контакт.
    GET /sparring/<id>/respond/?method=telegram|whatsapp|max — редирект на мессенджер.
    POST /sparring/<id>/respond/ — только записать отклик, уведомить автора; JSON с contact_urls.
    """
    logger.debug(
        "sparring_respond: user=%s, pk=%s, method=%s",
        request.user.id,
        pk,
        request.method,
    )

    if not user_has_sparring_access(request.user):
        if request.method == "POST":
            return JsonResponse(
                {"success": False, "error": "Доступ к спаррингам по подписке."},
                status=403,
            )
        messages.error(request, "Доступ к спаррингам по подписке.")
        return redirect("pricing")

    try:
        sparring = SparringRequest.objects.select_related("player").get(
            pk=pk,
            status=SparringRequest.Status.ACTIVE,
        )
        logger.debug("sparring_respond: found sparring request %s", sparring.pk)
    except SparringRequest.DoesNotExist:
        logger.warning(
            "sparring_respond: sparring request %s not found or not active", pk
        )
        if request.method == "POST":
            return JsonResponse(
                {"success": False, "error": "Заявка не найдена или уже закрыта."},
                status=404,
            )
        messages.error(request, "Заявка не найдена или уже закрыта.")
        return redirect("sparring_list")
    valid_methods = ("telegram", "whatsapp", "max")

    if request.method == "GET":
        method = (request.GET.get("method") or "").lower()
        if method not in valid_methods:
            messages.error(request, "Укажите способ связи: telegram, whatsapp или max.")
            return redirect("sparring_list")
    else:
        # POST: method опционален, по умолчанию telegram (только для записи)
        method = "telegram"
        if request.content_type and "application/json" in request.content_type:
            try:
                data = json.loads(request.body or "{}")
                m = (data.get("method") or "").lower()
                if m in valid_methods:
                    method = m
            except (ValueError, TypeError):
                pass

    try:
        respondent = request.user.player
        logger.debug("sparring_respond: respondent=%s", respondent.pk)
    except (AttributeError, Player.DoesNotExist):
        logger.warning(
            "sparring_respond: user %s has no player profile", request.user.id
        )
        if request.method == "POST":
            return JsonResponse(
                {"success": False, "error": "Заполните профиль игрока."},
                status=400,
            )
        messages.error(request, "Заполните профиль игрока.")
        return redirect("profile_edit")

    if respondent.id == sparring.player_id:
        logger.warning(
            "sparring_respond: user %s tried to respond to own request", request.user.id
        )
        if request.method == "POST":
            return JsonResponse(
                {"success": False, "error": "Нельзя откликнуться на свою заявку."},
                status=400,
            )
        messages.error(request, "Нельзя откликнуться на свою заявку.")
        return redirect("sparring_list")

    try:
        obj, created = SparringResponse.objects.update_or_create(
            sparring_request=sparring,
            respondent=respondent,
            defaults={
                "contact_method": method,
                "status": SparringResponse.ResponseStatus.PENDING,
            },
        )
        logger.info(
            "sparring_respond: response %s %s (sparring=%s, respondent=%s)",
            "created" if created else "updated",
            obj.pk,
            sparring.pk,
            respondent.pk,
        )
    except Exception as e:
        logger.error("Failed to create/update sparring response: %s", e, exc_info=True)
        if request.method == "POST":
            return JsonResponse(
                {"success": False, "error": f"Ошибка при сохранении отклика: {str(e)}"},
                status=500,
            )
        messages.error(request, "Ошибка при сохранении отклика. Попробуйте позже.")
        return redirect("sparring_list")

    if created:
        # Создаем уведомление в личном кабинете для автора заявки
        try:
            Notification.objects.create(
                user=sparring.player.user,
                message=f"{respondent} откликнулся на вашу заявку на спарринг.",
                url=reverse("sparring_my_requests"),
            )
        except Exception as e:
            logger.warning("Failed to create notification for sparring response: %s", e)

        # Отправляем Telegram уведомление
        if notify_sparring_response is not None:
            try:
                notify_sparring_response(obj)
            except Exception as e:
                logger.warning(
                    "Failed to send Telegram notification for sparring response: %s", e
                )

    if request.method == "POST":
        try:
            contact_urls = _build_contact_urls(sparring.player)
            return JsonResponse(
                {
                    "success": True,
                    "contact_urls": contact_urls,
                    "message": (
                        "Отклик успешно отправлен!" if created else "Отклик обновлен."
                    ),
                }
            )
        except Exception as e:
            logger.error("Failed to build contact URLs: %s", e, exc_info=True)
            return JsonResponse(
                {
                    "success": True,
                    "contact_urls": {},
                    "message": (
                        "Отклик успешно отправлен!" if created else "Отклик обновлен."
                    ),
                    "warning": "Не удалось получить контакты для связи.",
                }
            )

    url = _get_contact_url(sparring.player, method)
    if url:
        return redirect(url)
    if created:
        messages.success(request, "Отклик записан. Автор заявки получит уведомление.")
    return redirect("sparring_list")


# ---------------------------------------------------------------------------
# Парный спарринг 2×2
# ---------------------------------------------------------------------------


@login_required
def doubles_detail(request, pk):
    """Детальная страница заявки на парный матч: команды, отклики, действия."""
    req = get_object_or_404(
        DoublesMatchRequest.objects.select_related(
            "created_by__user", "match"
        ).prefetch_related(
            "teams__members__player__user",
            "join_requests__members__player__user",
        ),
        pk=pk,
    )
    try:
        player = request.user.player
    except (AttributeError, Player.DoesNotExist):
        player = None

    is_author = bool(player and req.created_by_id == player.id)
    is_team_sparring = req.kind == DoublesMatchKind.TEAM
    author_team = req.teams.filter(side=TeamSide.AUTHOR).first()
    opponent_team = req.teams.filter(side=TeamSide.OPPONENT).first()
    pending_join_requests = req.join_requests.filter(
        status=DoublesJoinRequestStatus.PENDING
    )

    can_confirm = (
        is_author
        and req.status == DoublesMatchRequestStatus.READY
        and author_team
        and opponent_team
        and author_team.members.count() == 2
        and opponent_team.members.count() == 2
    )
    can_edit_teams = is_author and req.status in (
        DoublesMatchRequestStatus.OPEN,
        DoublesMatchRequestStatus.FORMING,
        DoublesMatchRequestStatus.READY,
    )
    exclude_for_partner = set()
    if author_team:
        exclude_for_partner = set(
            author_team.members.values_list("player_id", flat=True)
        )
    if opponent_team:
        exclude_for_partner |= set(
            opponent_team.members.values_list("player_id", flat=True)
        )

    add_partner_form = None
    if can_edit_teams and author_team and author_team.members.count() < 2:
        add_partner_form = DoublesAddPartnerForm(exclude_player_ids=exclude_for_partner)

    return render(
        request,
        "sparring/doubles_detail.html",
        {
            "doubles_request": req,
            "author_team": author_team,
            "opponent_team": opponent_team,
            "pending_join_requests": pending_join_requests,
            "is_author": is_author,
            "player": player,
            "can_confirm": can_confirm,
            "can_edit_teams": can_edit_teams,
            "add_partner_form": add_partner_form,
            "is_team_sparring": is_team_sparring,
            "has_sparring_access": user_has_sparring_access(request.user),
        },
    )


@require_POST
@login_required
def doubles_join(request, pk):
    """Откликнуться на заявку: в команду автора или соперников, один или с партнёром."""
    try:
        player = request.user.player
    except (AttributeError, Player.DoesNotExist):
        messages.error(request, "Заполните профиль игрока.")
        return redirect("profile_edit")

    target_side = request.POST.get("target_side")
    partner_id = request.POST.get("partner_id")
    if target_side not in (TeamSide.AUTHOR, TeamSide.OPPONENT):
        messages.error(request, "Укажите, в какую команду хотите вступить.")
        return redirect("doubles_detail", pk=pk)

    players = [player]
    if partner_id:
        try:
            partner = Player.objects.get(pk=int(partner_id))
            if partner.id != player.id:
                players.append(partner)
        except (ValueError, Player.DoesNotExist):
            pass

    try:
        create_join_request(
            match_request_id=pk,
            created_by=player,
            target_side=target_side,
            players=players,
        )
        messages.success(request, "Отклик отправлен. Ожидайте решения автора заявки.")
    except (PermissionError, ValueError) as e:
        messages.error(request, str(e))
    return redirect("doubles_detail", pk=pk)


@require_POST
@login_required
def doubles_accept_join(request, pk, join_request_id):
    """Принять отклик (только автор). Уведомление в ЛК и Telegram всем из отклика."""
    try:
        player = request.user.player
    except (AttributeError, Player.DoesNotExist):
        return redirect("profile_edit")
    try:
        accept_join_request(
            match_request_id=pk,
            join_request_id=join_request_id,
            accepted_by=player,
        )
        jr = DoublesJoinRequest.objects.prefetch_related("members__player__user").get(
            pk=join_request_id, match_request_id=pk
        )
        detail_url = reverse("doubles_detail", args=[pk])
        msg = "Ваш отклик на парный матч 2×2 принят. Ожидайте подтверждения матча автором заявки."
        if len(msg) > 255:
            msg = msg[:252] + "..."
        for m in jr.members.all():
            if m.player and getattr(m.player, "user_id", None):
                try:
                    Notification.objects.create(
                        user=m.player.user,
                        message=msg,
                        url=detail_url,
                    )
                except Exception as e:
                    logger.warning(
                        "doubles_accept_join Notification for user %s: %s",
                        m.player.user_id,
                        e,
                    )
        try:
            from apps.telegram_bot.notifications import notify_doubles_join_accepted

            notify_doubles_join_accepted(jr)
        except Exception as e:
            logger.warning("notify_doubles_join_accepted failed: %s", e)
        messages.success(request, "Отклик принят.")
    except (PermissionError, ValueError) as e:
        messages.error(request, str(e))
    return redirect("doubles_detail", pk=pk)


@require_POST
@login_required
def doubles_reject_join(request, pk, join_request_id):
    """Отклонить отклик (только автор)."""
    try:
        player = request.user.player
    except (AttributeError, Player.DoesNotExist):
        return redirect("profile_edit")
    try:
        reject_join_request(
            match_request_id=pk,
            join_request_id=join_request_id,
            rejected_by=player,
        )
        messages.info(request, "Отклик отклонён.")
    except PermissionError as e:
        messages.error(request, str(e))
    return redirect("doubles_detail", pk=pk)


@require_POST
@login_required
def doubles_cancel_join(request, join_request_id):
    """Отменить свой отклик."""
    try:
        player = request.user.player
    except (AttributeError, Player.DoesNotExist):
        return redirect("profile_edit")
    try:
        cancel_join_request(join_request_id=join_request_id, cancelled_by=player)
        messages.success(request, "Отклик отменён.")
    except (PermissionError, ValueError) as e:
        messages.error(request, str(e))
    next_url = (
        request.POST.get("next")
        or request.META.get("HTTP_REFERER")
        or reverse("doubles_list")
    )
    return redirect(next_url)


@require_POST
@login_required
def doubles_add_partner(request, pk):
    """Добавить партнёра в команду автора."""
    try:
        player = request.user.player
    except (AttributeError, Player.DoesNotExist):
        return redirect("profile_edit")
    partner_id = request.POST.get("player_id")
    if not partner_id:
        messages.error(request, "Выберите игрока.")
        return redirect("doubles_detail", pk=pk)
    try:
        add_partner_to_author_team(
            match_request_id=pk,
            player_id=int(partner_id),
            added_by=player,
        )
        messages.success(request, "Партнёр добавлен в команду.")
    except (PermissionError, ValueError) as e:
        messages.error(request, str(e))
    return redirect("doubles_detail", pk=pk)


@require_POST
@login_required
def doubles_remove_member(request, pk):
    """Удалить участника из команды (только автор)."""
    try:
        player = request.user.player
    except (AttributeError, Player.DoesNotExist):
        return redirect("profile_edit")
    team_side = request.POST.get("team_side")
    member_player_id = request.POST.get("player_id")
    if not team_side or not member_player_id:
        messages.error(request, "Не указаны команда или игрок.")
        return redirect("doubles_detail", pk=pk)
    try:
        remove_team_member(
            match_request_id=pk,
            team_side=team_side,
            player_id=int(member_player_id),
            removed_by=player,
        )
        messages.success(request, "Участник удалён из команды.")
    except (PermissionError, ValueError) as e:
        messages.error(request, str(e))
    return redirect("doubles_detail", pk=pk)


@require_POST
@login_required
def doubles_confirm(request, pk):
    """Подтвердить состав и создать матч или серию матчей (для командного спарринга)."""
    try:
        player = request.user.player
    except (AttributeError, Player.DoesNotExist):
        return redirect("profile_edit")
    try:
        req = DoublesMatchRequest.objects.get(pk=pk)
    except DoublesMatchRequest.DoesNotExist:
        messages.error(request, "Заявка не найдена.")
        return redirect("sparring_my_requests")

    try:
        if req.kind == DoublesMatchKind.TEAM:
            matches = confirm_team_sparring_series(
                match_request_id=pk,
                confirmed_by=player,
            )
            messages.success(
                request,
                (
                    "Матчи командного спарринга созданы! "
                    "Проверьте раздел «Мои матчи» для внесения результатов."
                ),
            )
            logger.info(
                "Team sparring matches created from request %s by user %s "
                "(total_matches=%s)",
                pk,
                request.user.id,
                len(matches),
            )
            return redirect("doubles_detail", pk=pk)
        match = confirm_match(match_request_id=pk, confirmed_by=player)
        messages.success(
            request, "Матч создан! Перейдите в «Мои матчи» для внесения результата."
        )
        return redirect("match_detail", pk=match.pk)
    except (PermissionError, ValueError) as e:
        messages.error(request, str(e))
        return redirect("doubles_detail", pk=pk)


@require_POST
@login_required
def doubles_cancel_request(request, pk):
    """Отменить заявку на парный матч."""
    try:
        player = request.user.player
    except (AttributeError, Player.DoesNotExist):
        return redirect("profile_edit")
    try:
        cancel_match_request(match_request_id=pk, cancelled_by=player)
        messages.success(request, "Заявка отменена.")
    except (PermissionError, ValueError) as e:
        messages.error(request, str(e))
    return redirect("doubles_my_requests")
