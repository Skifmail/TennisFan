"""Страницы и CSRF-защищённый HTTP-интерфейс городского чата."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from functools import wraps
from typing import Any, Final, cast
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, QuerySet
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

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
from .governance import can_manage, change_role
from .invitations import can_invite
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


def _catalog_cards(
    cities: Sequence[City],
    rooms: Sequence[ChatRoom],
    memberships: Sequence[ChatMembership],
    *,
    ranked: bool,
) -> list[dict[str, Any]]:
    joined = {member.room_id for member in memberships}
    cards: list[dict[str, Any]] = []
    for city in cities:
        room = getattr(city, "chat_room", None)
        cards.append(
            {
                "name": city.name,
                "label": city.display_label,
                "region": city.region or "Городской чат",
                "member_count": getattr(city, "member_count", 0),
                "room_id": room.pk if room else None,
                "created": bool(room and room.is_created),
                "active": not room or room.is_active,
                "joined": bool(room and room.pk in joined),
                "join_query": f"city_id={city.pk}",
            }
        )
    for room in rooms:
        cards.append(
            {
                "name": room.display_name,
                "label": room.display_label,
                "region": "Сообщество TennisFan",
                "member_count": getattr(room, "member_count", 0),
                "room_id": room.pk,
                "created": True,
                "active": True,
                "joined": room.pk in joined,
                "join_query": room.join_query,
            }
        )
    return sorted(
        cards,
        key=lambda card: (
            -card["member_count"] if ranked else 0,
            card["name"],
            card["region"],
        ),
    )[:CITY_SEARCH_LIMIT]


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
    query = request.GET.get("q", "").strip()[:CITY_SEARCH_LIMIT]
    cities_query = (
        _search_cities(query)
        .select_related("chat_room")
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
    )
    if "q" not in request.GET:
        cities_query = cities_query.filter(
            chat_room__is_active=True, chat_room__is_created=True, member_count__gt=0
        ).order_by("-member_count", "name", "region", "pk")
    else:
        cities_query = cities_query.order_by("name", "region")
    cities_list = list(cities_query[:CITY_SEARCH_LIMIT])
    custom_query = ChatRoom.objects.filter(
        city__isnull=True, is_active=True, is_created=True
    ).annotate(
        member_count=Count(
            "memberships",
            filter=Q(memberships__is_active=True, memberships__user__is_active=True),
        )
    )
    if "q" not in request.GET:
        custom_query = custom_query.filter(member_count__gt=0)
    if query:
        title_filter = Q()
        for variant in {
            query,
            query.lower(),
            query.upper(),
            query.capitalize(),
            query.title(),
        }:
            title_filter |= Q(title__icontains=variant)
        custom_query = custom_query.filter(title_filter)
    custom_rooms = list(
        custom_query.order_by("-member_count", "title", "pk")[:CITY_SEARCH_LIMIT]
    )
    own = list(services.my_memberships(user=cast(User, request.user)))
    joined = {member.room.city_id for member in own}
    return render(
        request,
        "chat/index.html",
        {
            "memberships": own,
            "cities": cities_list,
            "custom_rooms": custom_rooms,
            "catalog_cards": _catalog_cards(
                cities_list, custom_rooms, own, ranked="q" not in request.GET
            ),
            "joined_rooms": {member.room_id for member in own},
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
            return redirect(reverse("chat:join") + f"?{current.room.join_query}")
        return redirect("chat:index")
    except ChatError:
        return redirect("chat:index")
    profile = getattr(request.user, "player", None)
    return render(
        request,
        "chat/room.html",
        {
            "membership": member,
            "can_manage_chat": can_manage(
                user=cast(User, request.user), room=member.room
            ),
            "can_invite_chat": can_invite(
                user=cast(User, request.user), room_id=room_id
            ),
            "profile_has_photo": bool(profile and profile.avatar),
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
                "administratorBadgeUrl": static("images/chat/administrator-badge.webp"),
                "canModerate": bool(
                    request.user.is_staff
                    or member.is_moderator
                    or member.room.administrator_id == request.user.pk
                ),
            },
        },
    )


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
@_api
def join(request: HttpRequest) -> HttpResponse:
    """Предъявляет два обязательных подтверждения и добровольный выбор профиля.

    Args:
        request: Запрос формы либо её отправка с подписанным контекстом города.

    Returns:
        Форма, ожидание проверки особых условий или перенаправление в комнату.
    """
    params = request.POST if request.method == "POST" else request.GET
    if params.get("room_id"):
        city: City | ChatRoom = get_object_or_404(
            ChatRoom,
            pk=_id(params.get("room_id")),
            city__isnull=True,
            is_created=True,
            is_active=True,
        )
        current_filter: dict[str, Any] = {"room": city}
    else:
        city = get_object_or_404(City, pk=_id(params.get("city_id")))
        current_filter = {"room__city": city}
    user = cast(User, request.user)
    current = (
        ChatMembership.objects.select_related("room")
        .filter(user=user, **current_filter)
        .first()
    )
    is_consent_update = bool(
        current
        and current.is_active
        and current.consented_at
        and not current.consent_withdrawn_at
    )
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
                    {
                        "city": city,
                        "membership": member,
                        "target_name": str(city),
                        "join_query": member.room.join_query,
                    },
                    status=202,
                )
            if is_consent_update:
                messages.success(
                    request,
                    "Согласия обновлены. Выбранные настройки показа имени и фото применены.",
                )
            return redirect("chat:room", room_id=member.room_id)
    # В настройках показываем только действующие сохранённые значения, не неподтверждённый POST.
    consent_form = build_consent_form(user=user, city=city, membership=current)
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
            "custom_room": city if isinstance(city, ChatRoom) else None,
            "target_name": (
                city.display_name if isinstance(city, ChatRoom) else city.name
            ),
            "target_label": str(city),
            "consent_form": consent_form,
            "has_saved_consents": bool(consent_form.initial.get("personal_data")),
            "consent_error": error,
            "is_consent_update": is_consent_update,
            "is_chat_creation": not isinstance(city, ChatRoom)
            and not ChatRoom.objects.filter(city=city, is_created=True).exists(),
            "consent_room_id": current.room_id if current else None,
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
    selected = get_object_or_404(ChatRoom.objects.select_related("city"), pk=room_id)
    withdraw_chat_consent(
        user=cast(User, request.user),
        room_id=room_id,
        ip_address=consent_request_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", ""),
    )
    messages.success(
        request,
        f"Вы отозвали основные согласия и исключены из чата «{selected.display_name}». "
        "Доступ к переписке закрыт, ваши сообщения и данные скрыты. "
        "Для возвращения потребуется снова подтвердить обязательные пункты.",
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


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
@_api
def management(request: HttpRequest, room_id: int) -> HttpResponse:
    """Показывает управление ролями без публикации скрытых имён и контактов.

    Args:
        request: Запрос администратора сообщества или платформы.
        room_id: Выбранная комната.

    Returns:
        Страница ролей либо перенаправление после сохранения.

    Raises:
        ChatAccessError: Нет полномочий в выбранной комнате.
    """
    from django.core.paginator import Paginator

    from .consent import ensure_chat_consent
    from .domain import ChatAccessError

    user = cast(User, request.user)
    selected = get_object_or_404(
        ChatRoom.objects.select_related("city"), pk=room_id, is_created=True
    )
    if not can_manage(user=user, room=selected):
        raise ChatAccessError("Требуются права администратора этого чата.")
    if not (user.is_staff and user.has_perm("chat.change_chatroom")):
        services.require_member(user=user, room_id=room_id)
    if request.method == "POST":
        if request.POST.get("operation") == "moderator" and request.POST.get(
            "enabled"
        ) not in {"0", "1"}:
            raise ChatValidationError("Некорректное значение роли.")
        change_role(
            user=user,
            room_id=room_id,
            target_id=_id(request.POST.get("target_id")),
            operation=request.POST.get("operation", ""),
            enabled=request.POST.get("enabled") == "1",
        )
        messages.success(request, "Права участника обновлены.")
        return redirect(
            (
                "chat:room"
                if request.POST.get("operation") == "transfer"
                else "chat:management"
            ),
            room_id=room_id,
        )
    page = Paginator(
        ChatMembership.objects.select_related("user")
        .filter(room=selected, is_active=True, user__is_active=True)
        .order_by("pk"),
        CITY_SEARCH_LIMIT,
    ).get_page(request.GET.get("page"))
    participants = []
    for member in page:
        try:
            ensure_chat_consent(member)
        except ChatError:
            continue
        participants.append(
            {
                "id": member.user_id,
                "label": (
                    member.user.get_full_name()
                    if member.disclosure_name
                    else f"Участник #{member.user_id}"
                ),
                "moderator": member.is_moderator,
                "administrator": selected.administrator_id == member.user_id,
            }
        )
    return render(
        request,
        "chat/management.html",
        {
            "room": selected,
            "participants": participants,
            "page": page,
            "memberships": list(services.my_memberships(user=user)),
        },
    )
