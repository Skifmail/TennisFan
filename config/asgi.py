"""ASGI-приложение HTTP и защищённых WebSocket городских чатов."""

import os
from typing import cast

from asgiref.typing import ASGIApplication
from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.core.asgi import get_asgi_application
from django.urls import path

import logging_config  # noqa: F401

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django_application = get_asgi_application()

from apps.chat.consumers import CityChatConsumer  # noqa: E402


def create_application() -> ASGIApplication:
    """Создаёт маршрутизатор с текущими настройками допустимых хостов.

    Returns:
        HTTP-приложение и защищённые WebSocket-маршруты.
    """
    return cast(
        ASGIApplication,
        ProtocolTypeRouter(
            {
                "http": django_application,
                "websocket": AllowedHostsOriginValidator(
                    AuthMiddlewareStack(
                        URLRouter(
                            [
                                path(
                                    "ws/chats/<int:room_id>/",
                                    CityChatConsumer.as_asgi(),
                                ),
                            ]
                        )
                    )
                ),
            }
        ),
    )


application = create_application()
