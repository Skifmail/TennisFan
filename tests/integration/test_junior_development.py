"""Серверная блокировка действий и доступность просмотра детской лиги."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.template.loader import get_template
from django.test import RequestFactory, override_settings
from django.urls import resolve, reverse

from apps.juniors.middleware import JuniorDevelopmentMiddleware
from tests.support.factories import make_user


@pytest.mark.parametrize(
    "authenticated,staff,active",
    [(False, False, False), (True, False, True), (True, True, False)],
)
def test_actions_blocked_for_non_admins(authenticated, staff, active):
    middleware = JuniorDevelopmentMiddleware(lambda request: HttpResponse())
    for route in (
        "/kids/family/add/",
        "/kids/join/",
        "/kids/family/1/edit/",
        "/kids/tournaments/1/manage/",
        "/kids/matches/1/result/",
        "/kids/entries/1/pay/",
        "/kids/family/1/consent/",
        "/kids/entries/1/withdraw/",
        "/kids/family/1/matches/1/appeal/",
        "/kids/payments/00000000-0000-0000-0000-000000000001/",
    ):
        for method in ("get", "post"):
            request = getattr(RequestFactory(), method)(route)
            request.user = SimpleNamespace(
                is_authenticated=authenticated, is_staff=staff, is_active=active
            )
            request.resolver_match = resolve(route)
            with patch(
                "apps.juniors.middleware.render", return_value=HttpResponse(status=403)
            ):
                assert middleware.process_view(request, None, (), {}).status_code == 403


@pytest.mark.parametrize(
    "route",
    [
        "/kids/",
        "/kids/rules/",
        "/kids/ratings/",
        "/kids/tournaments/",
        "/kids/tournaments/1/",
        "/kids/family/",
        "/kids/family/1/",
        "/kids/history/",
        "/kids/payments/",
    ],
)
def test_read_pages_allow_only_read_methods(route):
    middleware = JuniorDevelopmentMiddleware(lambda request: HttpResponse())
    request = RequestFactory().get(route)
    request.user = AnonymousUser()
    request.resolver_match = resolve(route)
    assert middleware.process_view(request, None, (), {}) is None
    request.method = "POST"
    with patch("apps.juniors.middleware.render", return_value=HttpResponse(status=403)):
        assert middleware.process_view(request, None, (), {}).status_code == 403


def test_admin_and_payment_webhook_pass_through():
    middleware = JuniorDevelopmentMiddleware(lambda request: HttpResponse())
    request = RequestFactory().post("/kids/family/add/")
    request.user = SimpleNamespace(is_authenticated=True, is_active=True, is_staff=True)
    request.resolver_match = resolve(request.path)
    assert middleware.process_view(request, None, (), {}) is None
    request.user = AnonymousUser()
    request.resolver_match = resolve("/kids/payments/webhook/")
    assert middleware.process_view(request, None, (), {}) is None


@pytest.mark.django_db
@override_settings(JUNIOR_LEAGUE_ENABLED=True, SECURE_SSL_REDIRECT=False)
def test_family_banner_and_admin_controls(client):
    user = make_user(email="readonly@junior.test")
    client.force_login(user)
    response = client.get(reverse("juniors:family"))
    assert response.status_code == 200
    assert "Детская лига находится в разработке" in response.content.decode()
    assert reverse("juniors:add_child") not in response.content.decode()
    assert client.post(reverse("juniors:add_child"), {}).status_code == 403
    user.is_staff = True
    user.save(update_fields=["is_staff"])
    assert (
        reverse("juniors:add_child")
        in client.get(reverse("juniors:family")).content.decode()
    )


@pytest.mark.parametrize(
    "name",
    [
        "child",
        "payments",
        "tournament",
        "manage",
        "result",
        "home",
        "family",
        "parent_join",
        "child_form",
        "_appeals",
    ],
)
def test_templates_compile(name):
    get_template(f"juniors/{name}.html")
