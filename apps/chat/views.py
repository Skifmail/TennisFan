"""Страницы и CSRF-защищённый HTTP-интерфейс городского чата."""

from __future__ import annotations

import json
from collections.abc import Callable
from functools import wraps
from typing import Any, Final, cast
from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, QuerySet
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.core.localities import primary_locality_name
from apps.core.models import City
from apps.users.models import User

from . import services
from .consent import (
    BASE_DISCLOSURE_CONDITIONS,
    accept_chat_consent,
    build_consent_form,
    consent_request_ip,
    withdraw_chat_consent,
)
from .domain import (
    MAX_DATABASE_ID,
    MAX_HTTP_BODY_BYTES,
    ChatConsentRequiredError,
    ChatError,
    ChatValidationError,
)
from .models import ChatMembership, ChatRoom

CITY_SEARCH_LIMIT: Final = 100


def _api(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    @wraps(view)
    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        try:
            services.require_active_user(cast(User, request.user))
            response = view(request, *args, **kwargs)
        except ChatError as exc:
            response = JsonResponse(
                {"error": str(exc), "code": exc.code}, status=exc.status
            )
            if exc.status == 429:
                response["Retry-After"] = "2"
        response["Cache-Control"] = "private, no-store"
        return response

    return wrapped


def _integer(value: Any, *, optional: bool = False, zero: bool = False) -> int | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ChatValidationError("Некорректный идентификатор.")
    try:
        result = int(value)
    except ValueError as exc:
        raise ChatValidationError("Некорректный идентификатор.") from exc
    if result < (0 if zero else 1) or result > MAX_DATABASE_ID:
        raise ChatValidationError("Идентификатор вне допустимого диапазона.")
    return result


def _id(value: Any, *, zero: bool = False) -> int:
    return cast(int, _integer(value, zero=zero))


def _text(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ChatValidationError("Ожидается текстовое поле.")
    return value


def _boolean(data: dict[str, Any], key: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise ChatValidationError("Ожидается логическое значение.")
    return value


def _search_cities(query: str) -> QuerySet[City]:
    # В SQLite и PostgreSQL с LC_CTYPE=C регистр кириллицы не преобразуется.
    # Варианты стандартного написания справочника дают одинаковый поиск в обеих БД.
    rows = City.objects.all()
    if not query:
        return rows
    condition = Q()
    for variant in {
        query,
        query.lower(),
        query.upper(),
        query.capitalize(),
        query.title(),
    }:
        condition |= Q(name__icontains=variant)
    return rows.filter(condition)


@login_required
@never_cache
@require_GET
def index(request: HttpRequest) -> HttpResponse:
    """Показывает участие и поиск чатов любых населённых пунктов.

    Args:
        request: Авторизованный запрос.

    Returns:
        Каталог городских комнат.
    """
    profile = getattr(request.user, "player", None)
    suggested = primary_locality_name(getattr(profile, "city", "") or "")
    query = request.GET.get("q", suggested).strip()[:CITY_SEARCH_LIMIT]
    cities_query = _search_cities(query)
    cities_list = list(
        cities_query.select_related("chat_room")
        .annotate(
            member_count=Count(
                "chat_room__memberships",
                filter=Q(
                    chat_room__is_active=True,
                    chat_room__memberships__is_active=True,
                    chat_room__memberships__user__is_active=True,
                ),
            )
        )
        .order_by("name", "region")[:CITY_SEARCH_LIMIT]
    )
    own = list(services.my_memberships(user=cast(User, request.user)))
    joined = {member.room.city_id for member in own}
    return render(
        request,
        "chat/index.html",
        {
            "memberships": own,
            "cities": cities_list,
            "joined_cities": joined,
            "query": query,
        },
    )


@login_required
@never_cache
@require_GET
def room(request: HttpRequest, room_id: int) -> HttpResponse:
    """Открывает переписку после явного вступления в комнату.

    Args:
        request: Запрос участника.
        room_id: Комната.

    Returns:
        Страница переписки либо каталог для вступления.
    """
    try:
        member = services.require_member(user=cast(User, request.user), room_id=room_id)
    except ChatConsentRequiredError:
        current = (
            ChatMembership.objects.select_related("room")
            .filter(user=cast(User, request.user), room_id=room_id)
            .first()
        )
        if current:
            return redirect(reverse("chat:join") + f"?city_id={current.room.city_id}")
        return redirect("chat:index")
    except ChatError:
        return redirect("chat:index")
    return render(
        request,
        "chat/room.html",
        {
            "membership": member,
            "room": member.room,
            "memberships": list(services.my_memberships(user=cast(User, request.user))),
            "member_count": member.room.memberships.filter(
                is_active=True, user__is_active=True
            ).count(),
            "chat_config": {
                "roomId": room_id,
                "userId": request.user.pk,
                "actionUrl": f"/chats/api/rooms/{room_id}/action/",
                "historyUrl": f"/chats/api/rooms/{room_id}/history/",
                "syncUrl": f"/chats/api/rooms/{room_id}/sync/",
                "socketPath": f"/ws/chats/{room_id}/",
                "indexUrl": "/chats/",
                "moderatorBadgeUrl": static("images/chat/moderator-badge.png"),
                "canModerate": bool(request.user.is_staff or member.is_moderator),
            },
        },
    )


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
@_api
def join(request: HttpRequest) -> HttpResponse:
    """Предъявляет документы и вступает только после трёх личных подтверждений.

    Args:
        request: Запрос формы либо её отправка с подписанным контекстом города.

    Returns:
        Форма, ожидание проверки особых условий или перенаправление в комнату.
    """
    city_id = _id(
        request.POST.get("city_id")
        if request.method == "POST"
        else request.GET.get("city_id")
    )
    city = get_object_or_404(City, pk=city_id)
    user = cast(User, request.user)
    error = ""
    status = 200
    if request.method == "POST":
        try:
            member = accept_chat_consent(
                user=user,
                city=city,
                data=request.POST,
                ip_address=consent_request_ip(request),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
            )
        except ChatError as exc:
            error, status = str(exc), exc.status
        else:
            if member.disclosure_conditions and not member.disclosure_reviewed:
                return render(
                    request,
                    "chat/join_pending.html",
                    {"city": city, "membership": member},
                    status=202,
                )
            return redirect("chat:room", room_id=member.room_id)
    # После ошибки все галочки снова пусты: новая редакция не принимается молча.
    template = (
        "chat/_join_form.html"
        if request.headers.get("X-Requested-With") == "XMLHttpRequest"
        else "chat/join.html"
    )
    return render(
        request,
        template,
        {
            "city": city,
            "consent_form": build_consent_form(user=user, city=city),
            "consent_error": error,
            "base_conditions": BASE_DISCLOSURE_CONDITIONS,
            "memberships": list(services.my_memberships(user=user)),
        },
        status=status,
    )


@require_GET
@never_cache
def data_conditions(request: HttpRequest, room_id: int) -> HttpResponse:
    """Публикует условия распространения без email и неразрешённых данных профиля.

    Args:
        request: Запрос пользователя или гостя.
        room_id: Городской чат.

    Returns:
        Публичный перечень условий по техническим обозначениям участников.
    """
    selected = get_object_or_404(ChatRoom.objects.select_related("city"), pk=room_id)
    members = (
        ChatMembership.objects.filter(room=selected, consented_at__isnull=False)
        .only(
            "user_id",
            "disclosure_name",
            "disclosure_avatar",
            "disclosure_conditions",
            "consented_at",
            "consent_withdrawn_at",
        )
        .order_by("user_id")
    )
    return render(
        request,
        "chat/data_conditions.html",
        {
            "chat_room": selected,
            "conditions_members": members,
            "base_conditions": BASE_DISCLOSURE_CONDITIONS,
        },
    )


@require_POST
@_api
def withdraw_consent(request: HttpRequest, room_id: int) -> HttpResponse:
    """Принимает личный отзыв согласий, не требуя их актуального состояния.

    Args:
        request: Авторизованный CSRF-защищённый запрос.
        room_id: Городской чат.

    Returns:
        Перенаправление в каталог после отзыва.
    """
    get_object_or_404(ChatRoom, pk=room_id)
    withdraw_chat_consent(
        user=cast(User, request.user),
        room_id=room_id,
        ip_address=consent_request_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", ""),
    )
    return redirect("chat:index")


@require_GET
@_api
def cities(request: HttpRequest) -> JsonResponse:
    """Возвращает ограниченный список однозначных записей справочника.

    Args:
        request: Запрос с необязательным названием.

    Returns:
        Названия, регионы и идентификаторы населённых пунктов.
    """
    query = request.GET.get("q", "").strip()[:100]
    rows = _search_cities(query)
    return JsonResponse(
        {
            "cities": [
                {
                    "id": city.pk,
                    "name": city.name,
                    "label": city.display_label(),
                    "region": city.region,
                }
                for city in rows.order_by("name", "region")[:CITY_SEARCH_LIMIT]
            ]
        }
    )


@require_GET
@_api
def history(request: HttpRequest, room_id: int) -> JsonResponse:
    """Возвращает страницу истории только участнику.

    Args:
        request: Запрос с курсором предыдущей страницы.
        room_id: Комната.

    Returns:
        Сообщения и курсор журнала.
    """
    return JsonResponse(
        services.history(
            user=cast(User, request.user),
            room_id=room_id,
            before_id=_integer(request.GET.get("before"), optional=True),
        )
    )


@require_GET
@_api
def sync(request: HttpRequest, room_id: int) -> JsonResponse:
    """Возвращает изменения после последнего подтверждённого курсора.

    Args:
        request: Запрос с параметром after.
        room_id: Комната.

    Returns:
        Страница изменений, в том числе удалений.
    """
    return JsonResponse(
        services.sync_events(
            user=cast(User, request.user),
            room_id=room_id,
            after_id=_id(request.GET.get("after", "0"), zero=True),
        )
    )


@require_POST
@_api
def action(request: HttpRequest, room_id: int) -> JsonResponse:
    """Выполняет авторизованное действие с проверкой структуры JSON.

    Args:
        request: JSON-запрос с действием.
        room_id: Комната.

    Returns:
        Результат действия или типизированная ошибка.
    """
    if len(request.body) > MAX_HTTP_BODY_BYTES:
        raise ChatValidationError("Слишком большой запрос.")
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise ChatValidationError("Некорректный JSON.") from exc
    if not isinstance(data, dict):
        raise ChatValidationError("Ожидается JSON-объект.")
    user = cast(User, request.user)
    operation = _text(data, "action")
    result: dict[str, Any] = {"ok": True}
    if operation == "send":
        try:
            client_id = UUID(_text(data, "client_id"))
        except ValueError as exc:
            raise ChatValidationError("Некорректный ключ отправки.") from exc
        message = services.send_message(
            user=user,
            room_id=room_id,
            text=_text(data, "text"),
            client_id=client_id,
            reply_to_id=_integer(data.get("reply_to_id"), optional=True),
        )
        result["message"] = services.serialize_message(message)
    elif operation == "edit":
        services.edit_message(
            user=user,
            room_id=room_id,
            message_id=_id(data.get("message_id")),
            text=_text(data, "text"),
        )
    elif operation == "delete":
        services.delete_message(
            user=user, room_id=room_id, message_id=_id(data.get("message_id"))
        )
    elif operation == "read":
        services.mark_read(
            user=user, room_id=room_id, message_id=_id(data.get("message_id"))
        )
    elif operation == "leave":
        services.leave_room(user=user, room_id=room_id)
    elif operation == "report":
        services.report_message(
            user=user,
            room_id=room_id,
            message_id=_id(data.get("message_id")),
            reason=_text(data, "reason"),
        )
    elif operation == "notifications":
        services.set_notifications(
            user=user, room_id=room_id, enabled=_boolean(data, "enabled")
        )
    elif operation == "ban":
        services.moderate_member(
            user=user,
            room_id=room_id,
            target_id=_id(data.get("user_id")),
            banned=_boolean(data, "banned"),
        )
    else:
        raise ChatValidationError("Неизвестное действие.")
    return JsonResponse(result)
