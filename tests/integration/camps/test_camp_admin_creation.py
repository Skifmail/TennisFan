"""Единая страница создания кемпа, заезда и пакетов в админке."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.contrib import admin
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.camps.models import Camp, CampSession
from apps.users.models import User

pytestmark = pytest.mark.django_db


def _payload(**overrides: Any) -> dict[str, Any]:
    """Собирает данные формы создания кемпа с одним пакетом."""
    start = timezone.localdate() + timedelta(days=30)
    data: dict[str, Any] = {
        "title": "Теннисный кемп в Сочи",
        "slug": "",
        "subtitle": "Теннис у моря",
        "audience": "adults",
        "description": "Программа кемпа",
        "start_date": start.isoformat(),
        "end_date": (start + timedelta(days=6)).isoformat(),
        "city": "Сочи",
        "venue": "Корт у моря",
        "capacity": "12",
        "country": "Россия",
        "minimum_players": "1",
        "min_age": "18",
        "tennis_hours": "0",
        "players_per_court": "4",
        "auto_reserve": "on",
        "terms": "Подтверждение координатором",
        "packages-TOTAL_FORMS": "3",
        "packages-INITIAL_FORMS": "0",
        "packages-MIN_NUM_FORMS": "0",
        "packages-MAX_NUM_FORMS": "1000",
        "packages-0-name": "Стандарт",
        "packages-0-description": "Тренировки и питание",
        "packages-0-price": "45000",
        "packages-0-currency": "RUB",
        "packages-0-is_active": "on",
        "packages-1-currency": "RUB",
        "packages-1-is_active": "on",
        "packages-2-currency": "RUB",
        "packages-2-is_active": "on",
        "publish": "on",
    }
    data.update(overrides)
    return data


def test_camp_admin_create_page_shows_three_steps(
    client: Client, manager: User
) -> None:
    """Страница создания объясняет порядок: кемп, заезд и пакеты."""
    client.force_login(manager)
    response = client.get(reverse("admin:camps_camp_add"))
    content = response.content.decode()
    assert response.status_code == 200
    assert "1. Кемп" in content
    assert "2. Заезд" in content
    assert "3. Пакеты участия" in content
    assert "Опубликовать кемп на сайте" in content
    assert content.count("<h1>Создать кемп</h1>") == 1
    assert "Одна страница вместо трёх разделов" not in content


def test_camp_admin_creates_published_camp_with_session_and_package(
    client: Client, manager: User
) -> None:
    """Одна отправка создаёт опубликованный кемп, заезд и пакет."""
    client.force_login(manager)
    response = client.post(reverse("admin:camps_camp_add"), _payload())
    assert response.status_code == 302
    camp = Camp.objects.get(title="Теннисный кемп в Сочи")
    assert camp.is_published is True
    session = camp.sessions.get()
    assert session.status == CampSession.Status.OPEN
    assert session.city == "Сочи"
    package = session.packages.get()
    assert package.name == "Стандарт"
    assert package.is_active is True


def test_camp_admin_creates_draft_when_publish_unchecked(
    client: Client, manager: User
) -> None:
    """Без галочки публикации кемп и заезд сохраняются черновиком."""
    payload = _payload()
    payload.pop("publish")
    client.force_login(manager)
    response = client.post(reverse("admin:camps_camp_add"), payload)
    assert response.status_code == 302
    camp = Camp.objects.get(title="Теннисный кемп в Сочи")
    assert camp.is_published is False
    assert camp.sessions.get().status == CampSession.Status.DRAFT


def test_camp_admin_requires_at_least_one_package(
    client: Client, manager: User
) -> None:
    """Без пакетов кемп не создаётся: форма показывает понятную ошибку."""
    payload = _payload()
    payload["packages-0-name"] = ""
    payload["packages-0-description"] = ""
    payload["packages-0-price"] = ""
    client.force_login(manager)
    response = client.post(reverse("admin:camps_camp_add"), payload)
    assert response.status_code == 200
    content = response.content.decode()
    assert "Добавьте хотя бы один пакет участия." in content
    assert "Проверьте поля с ошибками ниже." in content
    assert not Camp.objects.filter(title="Теннисный кемп в Сочи").exists()


def test_camp_admin_opens_extra_block_when_error_is_hidden(
    client: Client, manager: User
) -> None:
    """Ошибка в свёрнутом блоке автоматически раскрывает его."""
    start = timezone.localdate() + timedelta(days=30)
    payload = _payload(registration_deadline=(start + timedelta(days=1)).isoformat())
    client.force_login(manager)
    response = client.post(reverse("admin:camps_camp_add"), payload)
    content = response.content.decode()
    assert response.status_code == 200
    assert "Приём заявок должен закончиться не позднее начала заезда." in content
    assert '<details class="camp-admin-details" open>' in content
    assert not Camp.objects.filter(title="Теннисный кемп в Сочи").exists()


def test_camp_admin_requires_active_package_for_publish(
    client: Client, manager: User
) -> None:
    """Публикация невозможна, если все пакеты скрыты с сайта."""
    payload = _payload()
    payload.pop("packages-0-is_active")
    client.force_login(manager)
    response = client.post(reverse("admin:camps_camp_add"), payload)
    assert response.status_code == 200
    assert "Для публикации нужен хотя бы один пакет" in response.content.decode()
    assert not Camp.objects.filter(title="Теннисный кемп в Сочи").exists()


def test_camp_admin_generates_latin_slug_from_russian_title(
    client: Client, manager: User
) -> None:
    """Пустой адрес страницы создаётся из русского названия в латинице."""
    client.force_login(manager)
    client.post(reverse("admin:camps_camp_add"), _payload())
    camp = Camp.objects.get(title="Теннисный кемп в Сочи")
    assert camp.slug == "tennisnyy-kemp-v-sochi"


def test_camp_admin_menu_hides_sessions_and_packages(manager: User) -> None:
    """Заезды и пакеты не дублируются отдельными пунктами меню."""
    request = RequestFactory().get("/admin/")
    request.user = manager
    app_list = admin.site.get_app_list(request)
    camps_app = next(app for app in app_list if app["app_label"] == "camps")
    model_names = {model["object_name"] for model in camps_app["models"]}
    assert "Camp" in model_names
    assert "CampSession" not in model_names
    assert "CampPackage" not in model_names


def test_camp_admin_change_page_lists_sessions_with_actions(
    client: Client, manager: User, camp_session: CampSession
) -> None:
    """Страница кемпа показывает заезды и кнопки управления."""
    client.force_login(manager)
    response = client.get(
        reverse("admin:camps_camp_change", args=[camp_session.camp_id])
    )
    content = response.content.decode()
    assert response.status_code == 200
    assert "Даты и пакеты" in content
    assert "Заявки" in content
    assert "Сочи" in content
    assert "Как управлять кемпом" in content


def test_camp_admin_changelist_explains_single_creation_flow(
    client: Client, manager: User
) -> None:
    """Список кемпов ведёт на единую страницу создания."""
    client.force_login(manager)
    response = client.get(reverse("admin:camps_camp_changelist"))
    content = response.content.decode()
    assert response.status_code == 200
    assert "Создать кемп" in content
    assert "Отдельно создавать заезд и пакеты не нужно" in content


def test_camp_admin_session_add_preselects_camp(
    client: Client, manager: User, camp_session: CampSession
) -> None:
    """Кнопка «Добавить заезд» открывает форму с выбранным кемпом."""
    client.force_login(manager)
    url = reverse("admin:camps_campsession_add")
    response = client.get(f"{url}?camp={camp_session.camp_id}")
    content = response.content.decode()
    assert response.status_code == 200
    assert f'value="{camp_session.camp_id}" selected' in content
    assert "Как заполнить заезд" in content
    assert "Пакеты участия (обязательно)" in content
