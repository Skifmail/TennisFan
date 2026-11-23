"""Одиночная связь отклика: обратное чтение, защита от дублей и личный кабинет."""

from __future__ import annotations

from typing import cast
from unittest.mock import patch

import pytest
from django.contrib import admin
from django.db import IntegrityError, connection, transaction
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.sparring.admin import SparringResponseAdmin
from apps.sparring.models import SparringRequest, SparringResponse
from apps.sparring.services import (
    cancel_sparring_response,
    confirm_sparring_response,
    respond_to_sparring,
)
from apps.tournaments.models import Match
from apps.users.models import Notification
from tests.support.factories import make_player, make_subscription

pytestmark = pytest.mark.django_db


@pytest.fixture
def response() -> SparringResponse:
    """Создать ожидающий отклик двух игроков с доступом к спаррингам."""
    author = make_player(
        email_suffix="relation-author", first_name="Автор", last_name="Заявки"
    )
    respondent = make_player(
        email_suffix="relation-respondent", first_name="Игрок", last_name="Отклика"
    )
    for player in (author, respondent):
        make_subscription(
            player.user,
            tier_name=f"relation-{player.pk}",
            tier_kwargs={"has_sparring": True},
        )
    request = SparringRequest.objects.create(player=author, city="Москва")
    return SparringResponse.objects.create(
        sparring_request=request, respondent=respondent, contact_method="telegram"
    )


def _create_match(response: SparringResponse, *, linked: bool = True) -> Match:
    """Создать матч, не принимая отклик и не закрывая исходную заявку."""
    return Match.objects.create(
        match_type=Match.MatchType.SPARRING,
        player1=response.sparring_request.player,
        player2=response.respondent,
        sparring_response_id=response.pk if linked else None,
    )


def test_reverse_relation_and_admin_handle_missing_and_existing_match(
    response: SparringResponse,
) -> None:
    """Обратная связь и колонка админки работают до и после создания матча."""
    model_admin = SparringResponseAdmin(SparringResponse, admin.site)
    assert getattr(response, "match", None) is None
    assert model_admin.has_match(response) is False

    match = _create_match(response)
    response.refresh_from_db()

    assert response.match.pk == match.pk
    assert match.sparring_response_id == response.pk
    assert model_admin.has_match(response) is True
    assert SparringResponse.objects.get(match=match).pk == response.pk


def test_database_rejects_second_match_for_same_response(
    response: SparringResponse,
) -> None:
    """Ограничение БД по-прежнему не позволяет связать два матча с одним откликом."""
    match = _create_match(response)

    with pytest.raises(IntegrityError), transaction.atomic():
        _create_match(response)

    assert Match.objects.filter(sparring_response=response).count() == 1
    response.refresh_from_db()
    assert response.match.pk == match.pk


def test_unlinked_matches_and_set_null_keep_match_history(
    response: SparringResponse,
) -> None:
    """Несколько матчей без отклика допустимы; удаление отклика не удаляет матч."""
    match = _create_match(response)
    unlinked = [_create_match(response, linked=False) for _ in range(2)]

    response.delete()

    match.refresh_from_db()
    assert match.sparring_response_id is None
    assert (
        Match.objects.filter(pk__in=[match.pk, *[item.pk for item in unlinked]]).count()
        == 3
    )


@pytest.mark.parametrize("operation", ["confirm", "cancel", "respond"])
def test_services_reject_pending_response_with_existing_match(
    response: SparringResponse, operation: str
) -> None:
    """Наличие матча блокирует повторные действия даже при устаревшем статусе отклика."""
    match = _create_match(response)
    before_notifications = Notification.objects.count()

    with pytest.raises(ValueError, match="обработан|принят"):
        if operation == "confirm":
            confirm_sparring_response(
                response.pk, response.sparring_request.player.user_id
            )
        elif operation == "cancel":
            cancel_sparring_response(response.pk, response.respondent.user_id)
        else:
            respond_to_sparring(
                response.sparring_request_id,
                response.respondent,
                contact_method="telegram",
            )

    response.refresh_from_db()
    response.sparring_request.refresh_from_db()
    assert response.status == SparringResponse.ResponseStatus.PENDING
    assert response.sparring_request.status == SparringRequest.Status.ACTIVE
    assert response.sparring_request.match_id is None
    assert response.match.pk == match.pk
    assert Match.objects.filter(sparring_response=response).count() == 1
    assert Notification.objects.count() == before_notifications


def test_my_requests_preserves_match_link_and_prefetches_reverse_relation(
    response: SparringResponse, client: Client
) -> None:
    """Кабинет показывает ссылку принятого отклика и читает все матчи без N+1."""
    match = _create_match(response)
    SparringResponse.objects.filter(pk=response.pk).update(
        status=SparringResponse.ResponseStatus.ACCEPTED
    )
    missing_player = make_player(email_suffix="relation-missing")
    missing = SparringResponse.objects.create(
        sparring_request=response.sparring_request,
        respondent=missing_player,
        status=SparringResponse.ResponseStatus.ACCEPTED,
    )
    client.force_login(response.sparring_request.player.user)

    document = client.get(reverse("sparring_my_requests"), secure=True)

    assert document.status_code == 200
    assert (
        document.content.decode().count(
            f'href="{reverse("match_detail", args=[match.pk])}"'
        )
        == 1
    )
    requests = document.context["sparring_requests"]
    with CaptureQueriesContext(connection) as queries:
        matches = {
            current.pk: cast(Match | None, getattr(current, "match", None))
            for request in requests
            for current in request.responses.all()
        }
    assert len(queries) == 0
    assert matches[missing.pk] is None
    linked_match = matches[response.pk]
    assert linked_match is not None and linked_match.pk == match.pk


def test_accepting_response_keeps_single_match_on_repeated_confirmation(
    response: SparringResponse,
) -> None:
    """Успешное принятие создаёт один матч, повторное не создаёт матч и уведомления."""
    with patch("apps.telegram_bot.notifications.bot.is_configured", return_value=False):
        match = confirm_sparring_response(
            response.pk, response.sparring_request.player.user_id
        )
    before_notifications = Notification.objects.count()

    with pytest.raises(ValueError):
        confirm_sparring_response(response.pk, response.sparring_request.player.user_id)

    response.refresh_from_db()
    assert response.match.pk == match.pk
    assert response.status == SparringResponse.ResponseStatus.ACCEPTED
    assert Match.objects.filter(sparring_response=response).count() == 1
    assert Notification.objects.count() == before_notifications
