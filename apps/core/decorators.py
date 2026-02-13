"""
Общие декораторы для проекта.
"""

from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse


def login_required_with_message(
    message: str = "Данная информация доступна только для зарегистрированных пользователей."
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
                login_url = getattr(settings, 'LOGIN_URL', 'login')
                next_url = request.get_full_path()
                return redirect(f"{reverse(login_url)}?next={next_url}")
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
            
        required_fields = {
            'first_name': user.first_name,
            'last_name': user.last_name,
            'phone': user.phone,
            'birth_date': player.birth_date,
        }
        
        missing = [k for k, v in required_fields.items() if not v]
        
        if missing:
            messages.warning(
                request, 
                "Для выполнения этого действия необходимо заполнить профиль (Имя, Фамилия, Телефон, Дата рождения)."
            )
            return redirect('profile_edit')
            
        return view_func(request, *args, **kwargs)
    return wrapper
