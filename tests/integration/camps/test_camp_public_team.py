"""Публичная команда кемпа: организаторы, тренеры и связь с организатором."""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from apps.camps.models import CampSession
from apps.training.models import Coach
from apps.users.models import Player, User

pytestmark = pytest.mark.django_db


def _coach(
    *,
    name: str = "Анна Тренер",
    slug: str = "anna-coach",
    city: str = "Сочи",
    specialization: str = "Техника подачи",
    experience_years: int = 8,
    bio: str = "Помогает игрокам увереннее действовать у сетки.",
) -> Coach:
    """Создаёт действующего тренера с заполненной публичной карточкой."""
    return Coach.objects.create(
        name=name,
        slug=slug,
        city=city,
        specialization=specialization,
        experience_years=experience_years,
        bio=bio,
    )


def _player_user(
    *,
    email: str,
    first_name: str,
    last_name: str,
    city: str = "Москва",
) -> User:
    """Создаёт активного участника платформы с профилем игрока."""
    user = User.objects.create_user(
        email=email,
        password="test-only",
        first_name=first_name,
        last_name=last_name,
    )
    Player.objects.create(user=user, city=city)
    return user


def test_organizers_block_precedes_coaches(
    client: Client, camp_session: CampSession
) -> None:
    """Блок организаторов показывается выше блока тренеров."""
    camp = camp_session.camp
    camp.coaches.set([_coach()])
    camp.organizers.set(
        [
            _player_user(
                email="organizer@example.test",
                first_name="Иван",
                last_name="Организатор",
            )
        ]
    )

    content = client.get(camp.get_absolute_url()).content.decode()

    assert 'id="organizers"' in content
    assert 'id="team"' in content
    assert content.index('id="organizers"') < content.index('id="team"')


def test_coach_card_shows_brief_info_from_published_profile(
    client: Client, camp_session: CampSession
) -> None:
    """Карточка тренера показывает специализацию, город, опыт и краткое био."""
    camp = camp_session.camp
    camp.coaches.set([_coach()])

    content = client.get(camp.get_absolute_url()).content.decode()

    assert "Техника подачи" in content
    assert "Сочи" in content
    assert "Опыт: 8 лет" in content
    assert "Помогает игрокам увереннее действовать у сетки." in content


def test_coach_card_shows_more_button_only_for_truncated_bio(
    client: Client, camp_session: CampSession
) -> None:
    """Кнопка «Подробнее» появляется только там, где биография обрезана."""
    camp = camp_session.camp
    short_coach = _coach(
        name="Короткая биография",
        slug="short-bio",
        bio="Короткий текст о тренере.",
    )
    long_coach = _coach(
        name="Длинная биография",
        slug="long-bio",
        bio=" ".join(["Подробный"] * 25),
    )
    camp.coaches.set([short_coach, long_coach])

    content = client.get(camp.get_absolute_url()).content.decode()

    assert "Подробнее →" in content
    assert content.count("camp-coach__more") == 1


def test_organizer_card_has_direct_chat_button_for_authenticated_user(
    client: Client, camp_session: CampSession
) -> None:
    """Авторизованный участник видит кнопку перехода в личный чат."""
    camp = camp_session.camp
    organizer = _player_user(
        email="organizer@example.test",
        first_name="Иван",
        last_name="Организатор",
    )
    camp.organizers.set([organizer])
    viewer = _player_user(
        email="viewer@example.test",
        first_name="Пётр",
        last_name="Зритель",
    )
    client.force_login(viewer)

    content = client.get(camp.get_absolute_url()).content.decode()

    assert "Связаться" in content
    assert (
        f'action="{reverse("chat:direct_start", args=[organizer.player.pk])}"'
        in content
    )


def test_organizer_card_asks_anonymous_visitor_to_sign_in(
    client: Client, camp_session: CampSession
) -> None:
    """Гость видит кнопку «Связаться» и после входа вернётся на страницу кемпа."""
    camp = camp_session.camp
    camp.organizers.set(
        [
            _player_user(
                email="organizer@example.test",
                first_name="Иван",
                last_name="Организатор",
            )
        ]
    )

    content = client.get(camp.get_absolute_url()).content.decode()

    assert "Связаться" in content
    assert f'href="{reverse("login")}?next=' in content


def test_organizer_without_player_profile_has_no_contact_button(
    client: Client, camp_session: CampSession
) -> None:
    """Организатор без профиля игрока отображается, но без ссылки на чат."""
    camp = camp_session.camp
    organizer = User.objects.create_user(
        email="no-profile@example.test",
        password="test-only",
        first_name="Без",
        last_name="Профиля",
    )
    camp.organizers.set([organizer])
    viewer = _player_user(
        email="viewer@example.test",
        first_name="Пётр",
        last_name="Зритель",
    )
    client.force_login(viewer)

    content = client.get(camp.get_absolute_url()).content.decode()

    assert "Без Профиля" in content
    assert "chat/direct/" not in content
