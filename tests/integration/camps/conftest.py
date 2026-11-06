"""Общие изолированные данные и настройки тестов кемпов."""

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.camps.models import Camp, CampPackage, CampSession
from apps.users.models import User


@pytest.fixture(autouse=True)
def camp_settings(settings: Any) -> None:
    """Отключает внешние эффекты и очищает лимиты между тестами."""
    settings.SECURE_SSL_REDIRECT = False
    settings.ALLOWED_HOSTS = ["testserver", "localhost"]
    settings.STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    }
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    }
    settings.NOTIFICATION_CENTER_ENABLED = True
    settings.MIDDLEWARE = [
        row
        for row in settings.MIDDLEWARE
        if row != "apps.core.middleware.StartupMiddleware"
    ]
    cache.clear()


@pytest.fixture
def camp_session() -> CampSession:
    """Создаёт будущий заезд и два места для игроков."""
    camp = Camp.objects.create(
        title="Наш кемп",
        slug="our-camp",
        subtitle="Теннис вместе",
        description="Программа",
        is_published=True,
    )
    start = timezone.localdate() + timedelta(days=30)
    session = CampSession.objects.create(
        camp=camp,
        start_date=start,
        end_date=start + timedelta(days=6),
        city="Сочи",
        venue="Тестовый корт",
        capacity=2,
        terms="Подтверждение координатором",
    )
    session.status = "open"
    session.save()
    CampPackage.objects.create(
        session=session, name="Спорт", description="Тренировки", price=Decimal("45000")
    )
    return session


@pytest.fixture
def manager() -> User:
    """Создаёт сотрудника с полномочиями на изменение заявок."""
    return User.objects.create_superuser(
        email="camp-manager@example.test", password="test-only"
    )
