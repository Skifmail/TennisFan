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
