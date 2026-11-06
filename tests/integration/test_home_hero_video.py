"""Лёгкая webp-заглушка hero-видео на главной странице."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_home_hero_video_has_light_poster(client: Client) -> None:
    """Главная показывает webp-заглушку до загрузки видео."""
    body = client.get(reverse("home"), secure=True).content.decode()

    assert "home-hero-poster.webp" in body
    assert 'class="hero-video-poster"' in body
    assert "hero_video.mp4" in body
    assert "data-deferred-src" in body


def test_home_hero_poster_exists_in_static() -> None:
    """Постер hero закоммичен в статику, иначе collectstatic упадёт на манифесте."""
    poster = Path(settings.BASE_DIR) / "static/images/home-hero-poster.webp"

    assert poster.is_file(), f"Нет файла {poster}"
