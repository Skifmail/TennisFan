"""Профиль не раскрывает внешние контакты игрока до создания общего матча."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.tournaments.models import Match
from apps.users.models import Player
from tests.support.factories import make_player

pytestmark = pytest.mark.django_db


def _player(suffix: str, telegram: str) -> Player:
    """Создать игрока с заполненным Telegram."""
    player = make_player(email_suffix=f"profile-privacy-{suffix}")
    Player.objects.filter(pk=player.pk).update(telegram=telegram)
    player.refresh_from_db()
    return player


def test_stranger_profile_hides_messengers(client: Client) -> None:
    """Посторонний зритель видит чат на сайте, но не внешние контакты."""
    viewer = _player("viewer", "viewer_nick")
    target = _player("target", "target_nick")
    client.force_login(viewer.user)

    page = client.get(reverse("profile", args=[target.pk]), secure=True)

    assert page.status_code == 200
    content = page.content.decode()
    assert "https://t.me/target_nick" not in content
    assert "Внешние контакты откроются после создания общего матча" in content


def test_shared_match_unlocks_messengers(client: Client) -> None:
    """После создания общего матча контакты профиля открываются."""
    viewer = _player("viewer-match", "viewer_match")
    target = _player("target-match", "target_match")
    Match.objects.create(
        match_type=Match.MatchType.SPARRING,
        player1=viewer,
        player2=target,
        status=Match.MatchStatus.SCHEDULED,
        deadline=timezone.now() + timedelta(days=7),
    )
    client.force_login(viewer.user)

    page = client.get(reverse("profile", args=[target.pk]), secure=True)

    assert page.status_code == 200
    assert "https://t.me/target_match" in page.content.decode()
