"""Лента спаррингов раскрывает контакты автора только после создания матча."""

from __future__ import annotations

from typing import Literal

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.core.models import City
from apps.sparring.feed import rank_sparring_feed
from apps.sparring.models import SparringRequest, SparringResponse
from apps.users.models import Player, User
from tests.support.factories import make_player, make_subscription
from tests.support.sparring_author_contacts import (
    SparringAuthorContactsScenario,
    make_author_contacts_scenario,
)

pytestmark = pytest.mark.django_db

CONTACT_HINT = "Чтобы увидеть контакты автора"
PENDING_HINT = "Контакты автора откроются после создания матча"
AUTHOR_URLS = (
    "https://t.me/author_player",
    "https://wa.me/79999876543",
    "https://max.ru/u/author_player",
    "tel:+79991234567",
)


@pytest.fixture
def scenario(client: Client) -> SparringAuthorContactsScenario:
    """Авторизовать зрителя с доступом к спаррингам и заполненным профилем."""
    result = make_author_contacts_scenario()
    client.force_login(result.respondent.user)
    return result


def _response(
    scenario: SparringAuthorContactsScenario, *, status: str = "pending"
) -> SparringResponse:
    """Сохранить собственный отклик без отправки уведомлений во внешние сервисы."""
    return SparringResponse.objects.create(
        sparring_request=scenario.sparring,
        respondent=scenario.respondent,
        contact_method="telegram",
        status=status,
    )


@pytest.mark.parametrize("audience", ["guest", "free", "paid"])
def test_feed_before_response_shows_hint_without_author_contacts(
    client: Client, scenario: SparringAuthorContactsScenario, audience: str
) -> None:
    """Гость и оба тарифа видят подсказку, но не скрытые ссылки или телефон в HTML."""
    if audience == "guest":
        client.logout()
    elif audience == "free":
        scenario.respondent.user.subscription.delete()

    page = client.get(reverse("sparring_list"), secure=True)
    html = page.content.decode()

    assert page.status_code == 200
    assert CONTACT_HINT in html
    for contact in (*AUTHOR_URLS, "8 (999) 123-45-67"):
        assert contact not in html


def test_other_players_response_does_not_unlock_author_contacts(
    client: Client, scenario: SparringAuthorContactsScenario
) -> None:
    """Флаг «Есть отклики» не даёт зрителю права читать контакты чужой заявки."""
    other = make_player(email_suffix="author-contacts-other")
    SparringResponse.objects.create(
        sparring_request=scenario.sparring, respondent=other, contact_method="telegram"
    )

    html = client.get(reverse("sparring_list"), secure=True).content.decode()

    assert "Есть отклики" in html
    assert CONTACT_HINT in html
    assert all(contact not in html for contact in AUTHOR_URLS)


def test_pending_response_hides_contacts_and_prevents_resubmission(
    client: Client, scenario: SparringAuthorContactsScenario
) -> None:
    """Ожидающий отклик не раскрывает контакты и блокирует повторную отправку."""
    _response(scenario, status="pending")

    html = client.get(reverse("sparring_list"), secure=True).content.decode()

    assert all(contact not in html for contact in AUTHOR_URLS)
    assert "Отклик отправлен" in html
    assert PENDING_HINT in html
    assert CONTACT_HINT not in html
    assert f'data-id="{scenario.sparring.pk}" disabled' in html


def test_accepted_response_unlocks_author_contacts(
    client: Client, scenario: SparringAuthorContactsScenario
) -> None:
    """Принятый отклик означает созданный матч и раскрывает контакты автора."""
    _response(scenario, status="accepted")

    html = client.get(reverse("sparring_list"), secure=True).content.decode()

    assert all(contact in html for contact in AUTHOR_URLS)
    assert "Контакты автора" in html
    assert "8 (999) 123-45-67" in html
    assert "@author_player" not in html
    assert CONTACT_HINT not in html
    assert "data-confirm-call" in html
    assert "Матч создан" in html
    assert f'data-id="{scenario.sparring.pk}" disabled' in html
    assert f'action="{reverse("chat:direct_start", args=[scenario.author.pk])}"' in html
    assert "Написать в чат" in html


def test_rejected_response_hides_contacts_and_allows_a_new_response(
    client: Client, scenario: SparringAuthorContactsScenario
) -> None:
    """Отклонённый отклик не раскрывает контакты и не выдаётся за принятый."""
    _response(scenario, status="rejected")

    html = client.get(reverse("sparring_list"), secure=True).content.decode()

    assert all(contact not in html for contact in AUTHOR_URLS)
    assert "Ваш отклик отклонён" in html
    assert "Чтобы откликнуться снова" in html
    assert CONTACT_HINT not in html
    assert "Откликнуться снова" in html
    assert f'data-id="{scenario.sparring.pk}" disabled' not in html


def test_successful_reply_returns_no_contacts_and_keeps_them_hidden_after_reload(
    client: Client, scenario: SparringAuthorContactsScenario
) -> None:
    """POST создаёт отклик, но не возвращает и не показывает контакты автора."""
    submitted = client.post(
        reverse("sparring_respond", args=[scenario.sparring.pk]),
        "{}",
        content_type="application/json",
        secure=True,
    )

    assert submitted.status_code == 200
    data = submitted.json()
    assert data["success"] is True
    assert "contacts_html" not in data
    assert "contact_urls" not in data
    assert "контакты" in data["message"].lower()
    assert all(contact not in submitted.content.decode() for contact in AUTHOR_URLS)
    assert (
        SparringResponse.objects.filter(
            sparring_request=scenario.sparring, respondent=scenario.respondent
        ).count()
        == 1
    )

    html = client.get(reverse("sparring_list"), secure=True).content.decode()
    assert all(contact not in html for contact in AUTHOR_URLS)
    assert PENDING_HINT in html


def test_retrying_rejected_response_returns_to_pending_without_contacts(
    client: Client, scenario: SparringAuthorContactsScenario
) -> None:
    """Повторный отклик возвращает ожидание, не дублируя запись и не раскрывая контакты."""
    response = _response(scenario, status="rejected")

    submitted = client.post(
        reverse("sparring_respond", args=[scenario.sparring.pk]),
        "{}",
        content_type="application/json",
        secure=True,
    )

    assert submitted.status_code == 200
    assert "contacts_html" not in submitted.json()
    response.refresh_from_db()
    assert response.status == "pending"
    responses = SparringResponse.objects.filter(sparring_request=scenario.sparring)
    assert responses.count() == 1


@pytest.mark.parametrize(
    ("reason", "status"),
    [
        ("guest", 302),
        ("free", 403),
        ("own", 400),
        ("closed", 404),
        ("unverified", 403),
        ("incomplete", 400),
    ],
)
def test_rejected_http_action_never_returns_author_contacts(
    client: Client,
    scenario: SparringAuthorContactsScenario,
    reason: Literal["guest", "free", "own", "closed", "unverified", "incomplete"],
    status: int,
) -> None:
    """Ошибки доступа и валидации не раскрывают контакты даже через прямой POST."""
    if reason == "guest":
        client.logout()
    elif reason == "free":
        scenario.respondent.user.subscription.delete()
    elif reason == "own":
        make_subscription(
            scenario.author.user,
            tier_name="author-contacts-own-silver",
            tier_kwargs={"has_sparring": True},
        )
        client.force_login(scenario.author.user)
    elif reason == "closed":
        SparringRequest.objects.filter(pk=scenario.sparring.pk).update(status="closed")
    elif reason == "unverified":
        User.objects.filter(pk=scenario.respondent.user_id).update(email_verified=False)
    elif reason == "incomplete":
        User.objects.filter(pk=scenario.respondent.user_id).update(phone="")

    submitted = client.post(
        reverse("sparring_respond", args=[scenario.sparring.pk]),
        "{}",
        content_type="application/json",
        secure=True,
    )

    assert submitted.status_code == status
    assert "contacts_html" not in submitted.content.decode()
    assert all(contact not in submitted.content.decode() for contact in AUTHOR_URLS)
    responses = SparringResponse.objects.filter(sparring_request=scenario.sparring)
    assert not responses.exists()


def test_earned_contacts_remain_visible_when_subscription_is_no_longer_active(
    client: Client, scenario: SparringAuthorContactsScenario
) -> None:
    """Окончание тарифа не прячет контакты уже созданного матча."""
    _response(scenario, status="accepted")
    scenario.respondent.user.subscription.delete()

    html = client.get(reverse("sparring_list"), secure=True).content.decode()

    assert all(contact in html for contact in AUTHOR_URLS)
    assert "Матч создан" in html
    assert CONTACT_HINT not in html


def test_author_is_not_prompted_to_respond_to_own_request(
    client: Client, scenario: SparringAuthorContactsScenario
) -> None:
    """Владелец видит редактирование, а не подсказку про отклик самому себе."""
    client.force_login(scenario.author.user)

    html = client.get(reverse("sparring_list"), secure=True).content.decode()

    assert "Редактировать" in html
    assert CONTACT_HINT not in html
    assert "sparring-card__author-contacts" not in html


def test_author_without_contacts_has_platform_chat_fallback_after_response(
    client: Client, scenario: SparringAuthorContactsScenario
) -> None:
    """Пустые внешние контакты не создают нерабочих ссылок и не скрывают чат сайта."""
    Player.objects.filter(pk=scenario.author.pk).update(
        telegram="", whatsapp="", max_contact=""
    )
    User.objects.filter(pk=scenario.author.user_id).update(phone="")
    _response(scenario, status="accepted")

    html = client.get(reverse("sparring_list"), secure=True).content.decode()

    assert "Автор не указал внешние контакты" in html
    assert "Напишите ему в чате на сайте" in html
    assert "Написать в чат" in html
    assert 'class="sparring-contact ' not in html
    assert CONTACT_HINT not in html


def test_pending_response_does_not_render_author_contact_markup(
    client: Client, scenario: SparringAuthorContactsScenario
) -> None:
    """Ожидание матча не подставляет в HTML даже экранированные контакты автора."""
    User.objects.filter(pk=scenario.author.user_id).update(phone="<b>123</b>")
    _response(scenario, status="pending")

    html = client.get(reverse("sparring_list"), secure=True).content.decode()

    assert "&lt;b&gt;123&lt;/b&gt;" not in html
    assert "<b>123</b>" not in html
    assert PENDING_HINT in html


def test_accepted_author_contact_markup_escapes_player_supplied_text(
    client: Client, scenario: SparringAuthorContactsScenario
) -> None:
    """Принятый отклик показывает контакт из профиля только как экранированный текст."""
    User.objects.filter(pk=scenario.author.user_id).update(phone="<b>123</b>")
    _response(scenario, status="accepted")

    html = client.get(reverse("sparring_list"), secure=True).content.decode()

    assert "&lt;b&gt;123&lt;/b&gt;" in html
    assert "<b>123</b>" not in html


def test_prefetched_feed_discloses_author_contacts_without_queries_per_card(
    scenario: SparringAuthorContactsScenario,
) -> None:
    """Десять принятых карточек используют prefetch; SQL загружает только справочник городов."""
    for index in range(9):
        request = SparringRequest.objects.create(
            player=scenario.author, city="Москва", description=f"Игра {index}"
        )
        SparringResponse.objects.create(
            sparring_request=request,
            respondent=scenario.respondent,
            contact_method="telegram",
            status=SparringResponse.ResponseStatus.ACCEPTED,
        )
    _response(scenario, status="accepted")
    requests = list(
        SparringRequest.objects.filter(player=scenario.author)
        .select_related("player__user")
        .prefetch_related("responses")
    )

    with CaptureQueriesContext(connection) as queries:
        feed = rank_sparring_feed(
            singles=requests,
            doubles=[],
            viewer_city="",
            viewer_id=scenario.respondent.pk,
        )
        contacts = [entry.author_contacts for entry in feed]

    assert len(feed) == 10
    assert all(len(links) == 4 for links in contacts)
    assert len(queries) == 1
    assert City._meta.db_table in queries.captured_queries[0]["sql"]
