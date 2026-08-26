"""
Общие декораторы для проекта.
"""

from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse

from apps.core.redirects import append_next
from apps.users.verification import get_missing_profile_fields


def login_required_with_message(
    message: str = "Данная информация доступна только для зарегистрированных пользователей.",
):
    """
    Декоратор, требующий авторизации и показывающий сообщение при редиректе на страницу входа.

    Args:
        message: Сообщение, которое будет показано пользователю.

    Usage:
        @login_required_with_message("Для просмотра необходимо войти в аккаунт.")
        def my_view(request):
            ...
    """

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                messages.info(request, message)
                login_url = getattr(settings, "LOGIN_URL", "login")
                return redirect(
                    append_next(reverse(login_url), request.get_full_path())
                )
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


def require_filled_profile(view_func):
    """
    Декоратор, проверяющий заполненность обязательных полей профиля перед доступом к функционалу.
    Если профиль не заполнен, перенаправляет на страницу редактирования с сообщением.
    """

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return login_required_with_message()(view_func)(request, *args, **kwargs)

        user = request.user
        try:
            player = user.player
        except Exception:
            from apps.users.models import Player

            player = Player.objects.create(user=user)

        if get_missing_profile_fields(user, player):
            # Для POST запросов с JSON возвращаем JSON ответ вместо редиректа
            if (
                request.method == "POST"
                and request.content_type
                and "application/json" in request.content_type
            ):
                return JsonResponse(
                    {
                        "success": False,
                        "error": "Для выполнения этого действия необходимо заполнить профиль (Имя, Фамилия, Телефон, Дата рождения).",
                        "redirect": reverse("profile_edit"),
                    },
                    status=400,
                )
            messages.warning(
                request,
                "Для выполнения этого действия необходимо заполнить профиль (Имя, Фамилия, Телефон, Дата рождения).",
            )
            return redirect(
                append_next(reverse("profile_edit"), request.get_full_path())
            )

        return view_func(request, *args, **kwargs)

    return wrapper


def require_verified_player(view_func):
    """
    Декоратор, блокирующий доступ к функционалу для неподтверждённых игроков.
    Суперпользователи и staff всегда проходят.
    Применяется поверх require_filled_profile там, где нужна верификация.
    """

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return login_required_with_message()(view_func)(request, *args, **kwargs)

        user = request.user
        if user.is_superuser or user.is_staff:
            return view_func(request, *args, **kwargs)

        try:
            player = user.player
        except Exception:
            player = None

        if not player or not player.is_verified:
            if (
                request.method == "POST"
                and request.content_type
                and "application/json" in request.content_type
            ):
                return JsonResponse(
                    {
                        "success": False,
                        "error": "Ваш аккаунт ещё не подтверждён администратором.",
                        "redirect": reverse("profile_edit"),
                    },
                    status=403,
                )
            messages.warning(
                request,
                "Ваш аккаунт ещё не подтверждён администратором. "
                "Регистрация на турниры и спарринги будет доступна после подтверждения.",
            )
            # Без next: верификацию пользователь не может пройти сам, и возврат
            # отправил бы его в то же заблокированное действие.
            return redirect("profile_edit")

        return view_func(request, *args, **kwargs)

    return wrapper
