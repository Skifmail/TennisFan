"""Единая страница создания кемпа, заезда и пакетов в админке."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.contrib import admin
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.camps.admin_forms import CampCreateForm
from apps.camps.models import Camp, CampSession
from apps.training.models import Coach
from apps.users.models import SkillLevel, User

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


def _coach(name: str, slug: str, *, is_active: bool = True) -> Coach:
    """Создаёт тренера платформы для проверки выбора в админке."""
    return Coach.objects.create(
        name=name,
        slug=slug,
        city="Москва",
        is_active=is_active,
    )


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


def test_camp_admin_levels_are_platform_dropdown(client: Client, manager: User) -> None:
    """Уровень подготовки выбирается из уровней платформы, а не вводится текстом."""
    client.force_login(manager)
    response = client.get(reverse("admin:camps_camp_add"))
    content = response.content.decode()
    assert response.status_code == 200
    assert '<select name="levels"' in content
    assert "Не указан" in content
    for _value, label in SkillLevel.choices:
        assert f'value="{label}"' in content


def test_camp_admin_saves_selected_platform_level(
    client: Client, manager: User
) -> None:
    """Выбранный уровень платформы сохраняется в кемпе и виден на сайте."""
    client.force_login(manager)
    response = client.post(reverse("admin:camps_camp_add"), _payload(levels="Любитель"))
    assert response.status_code == 302
    camp = Camp.objects.get(title="Теннисный кемп в Сочи")
    assert camp.levels == "Любитель"
    public_page = client.get(camp.get_absolute_url()).content.decode()
    assert "Любитель" in public_page


def test_camp_admin_keeps_legacy_free_text_level(
    client: Client, manager: User, camp_session: CampSession
) -> None:
    """Старое свободное значение уровня не теряется при редактировании."""
    camp = camp_session.camp
    camp.levels = "Новички и любители"
    camp.save(update_fields=["levels"])
    client.force_login(manager)
    response = client.get(reverse("admin:camps_camp_change", args=[camp.pk]))
    content = response.content.decode()
    assert response.status_code == 200
    assert 'value="Новички и любители" selected' in content


def test_camp_admin_city_autocomplete_is_connected(
    client: Client, manager: User, camp_session: CampSession
) -> None:
    """Город подсказывается из справочника платформы на обеих страницах."""
    client.force_login(manager)
    create_content = client.get(reverse("admin:camps_camp_add")).content.decode()
    assert "js/city_autocomplete.js" in create_content
    assert 'name="city"' in create_content

    change_content = client.get(
        reverse("admin:camps_campsession_change", args=[camp_session.pk])
    ).content.decode()
    assert "js/city_autocomplete.js" in change_content
    assert 'name="city"' in change_content


def test_camp_admin_create_page_has_coaches_and_organizers(
    client: Client, manager: User
) -> None:
    """На странице создания можно выбрать тренеров и организаторов."""
    client.force_login(manager)
    content = client.get(reverse("admin:camps_camp_add")).content.decode()
    assert 'name="coaches"' in content
    assert 'name="organizers"' in content
    assert "Выберите действующих тренеров платформы" in content
    assert "Выберите участников платформы" in content


def test_camp_admin_saves_coaches_and_organizers(client: Client, manager: User) -> None:
    """Выбранные тренер и организатор сохраняются и видны на сайте."""
    coach = _coach("Анна Тренер", "anna-coach")
    organizer = User.objects.create_user(
        email="organizer@example.test",
        password="test-only",
        first_name="Иван",
        last_name="Организатор",
    )
    client.force_login(manager)
    payload = _payload(
        coaches=[str(coach.pk)],
        organizers=[str(organizer.pk)],
    )
    response = client.post(reverse("admin:camps_camp_add"), payload)
    assert response.status_code == 302
    camp = Camp.objects.get(title="Теннисный кемп в Сочи")
    assert list(camp.coaches.all()) == [coach]
    assert list(camp.organizers.all()) == [organizer]
    public_page = client.get(camp.get_absolute_url()).content.decode()
    assert "Анна Тренер" in public_page
    assert "Иван Организатор" in public_page
    assert "Организаторы" in public_page


def test_camp_admin_only_offers_active_coaches_and_organizers() -> None:
    """В выпадающих списках нет неактивных тренеров и пользователей."""
    inactive_coach = _coach("Скрытый тренер", "hidden-coach", is_active=False)
    inactive_user = User.objects.create_user(
        email="hidden-organizer@example.test",
        password="test-only",
        is_active=False,
    )
    form = CampCreateForm()
    assert inactive_coach not in form.fields["coaches"].queryset
    assert inactive_user not in form.fields["organizers"].queryset


def test_camp_detail_shows_only_active_team(
    client: Client, camp_session: CampSession
) -> None:
    """Публичная страница показывает только действующих тренеров и организаторов."""
    camp = camp_session.camp
    active_coach = _coach("Активный тренер", "active-coach")
    inactive_coach = _coach("Неактивный тренер", "inactive-coach", is_active=False)
    active_organizer = User.objects.create_user(
        email="active-organizer@example.test",
        password="test-only",
        first_name="Активный",
        last_name="Организатор",
    )
    inactive_organizer = User.objects.create_user(
        email="inactive-organizer@example.test",
        password="test-only",
        first_name="Неактивный",
        last_name="Организатор",
        is_active=False,
    )
    camp.coaches.set([active_coach, inactive_coach])
    camp.organizers.set([active_organizer, inactive_organizer])

    content = client.get(camp.get_absolute_url()).content.decode()
    assert "Активный тренер" in content
    assert "Неактивный тренер" not in content
    assert "Активный Организатор" in content
    assert "Неактивный Организатор" not in content


def test_camp_admin_change_page_has_coaches_and_organizers(
    client: Client, manager: User, camp_session: CampSession
) -> None:
    """Страница редактирования кемпа тоже позволяет менять команду."""
    client.force_login(manager)
    content = client.get(
        reverse("admin:camps_camp_change", args=[camp_session.camp_id])
    ).content.decode()
    assert 'name="coaches"' in content
    assert 'name="organizers"' in content


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
