"""Изоляция семейных профилей и единого раздела администрирования."""

from datetime import date
from unittest.mock import patch

import pytest
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.test import Client, override_settings
from django.urls import reverse

from apps.juniors.models import (
    AuditEvent,
    ChildConsent,
    ChildProfile,
    GuardianLink,
    LegalRelease,
)
from apps.juniors.permissions import guardian_for, require_verifier
from apps.juniors.services import (
    accept_consent,
    public_child_label,
    verify_guardian,
    withdraw_consent,
)
from apps.users.models import Player, User
from tests.support.factories import make_user

pytestmark = pytest.mark.django_db


def child() -> ChildProfile:
    return ChildProfile.objects.create(
        first_name="Анна",
        last_name="Иванова",
        birth_date=date(2018, 5, 10),
        city="Москва",
        gender="female",
    )


def test_family_isolation_and_revocation() -> None:
    kid = child()
    parent = make_user(email="parent@junior.test")
    stranger = make_user(email="stranger@junior.test")
    link = GuardianLink.objects.create(child=kid, user=parent, status="verified")
    assert guardian_for(parent, kid) == link
    with pytest.raises(PermissionDenied):
        guardian_for(stranger, kid)
    link.status = "revoked"
    link.save()
    with pytest.raises(PermissionDenied):
        guardian_for(parent, kid, verified=False)


def test_verifier_requires_separate_permission() -> None:
    staff = make_user(email="staff@junior.test", is_staff=True)
    with pytest.raises(PermissionDenied):
        require_verifier(staff)


def test_all_junior_models_in_one_admin_app() -> None:
    for model in (ChildProfile, GuardianLink, AuditEvent):
        assert model._meta.app_label == "juniors"
        assert admin.site.is_registered(model)
        assert model._meta.app_config.verbose_name == "Детская лига"


@override_settings(JUNIOR_LEAGUE_ENABLED=True, SECURE_SSL_REDIRECT=False)
def test_family_create_and_private_detail(client) -> None:
    parent = make_user(email="family-ui@junior.test")
    client.force_login(parent)
    response = client.post(
        reverse("juniors:add_child"),
        {
            "first_name": "Анна",
            "last_name": "Иванова",
            "birth_date": "2018-05-10",
            "city": "Москва",
            "gender": "female",
            "ball": "red",
            "relation": "parent",
            "representative": "on",
        },
    )
    assert response.status_code == 302
    kid = ChildProfile.objects.get(first_name="Анна")
    detail = client.get(reverse("juniors:child", args=[kid.pk]))
    assert detail.status_code == 200
    assert "no-store" in detail["Cache-Control"]
    assert client.get(reverse("juniors:family")).status_code == 200
    client.force_login(make_user(email="outsider-ui@junior.test"))
    assert client.get(reverse("juniors:child", args=[kid.pk])).status_code == 403


@override_settings(JUNIOR_LEAGUE_ENABLED=False, SECURE_SSL_REDIRECT=False)
def test_feature_disabled(client) -> None:
    assert client.get(reverse("juniors:family")).status_code == 404


def test_verification_consent_and_publication_are_separate() -> None:
    parent = make_user(email="consent-parent@junior.test")
    verifier = make_user(email="verifier@junior.test", is_staff=True, is_superuser=True)
    kid = child()
    link = GuardianLink.objects.create(child=kid, user=parent)
    release = LegalRelease.objects.create(
        version="test-v1",
        processing_text="Тест обработки",
        publication_text="Тест публикации",
        reviewed=True,
        is_current=True,
    )
    verify_guardian(
        actor=verifier,
        link_id=link.pk,
        approved=True,
        evidence="Оригиналы предъявлены лично; совершеннолетие, родство и возраст проверены",
    )
    consent = accept_consent(
        actor=parent, child_id=kid.pk, release_id=release.pk, kind="processing"
    )
    assert (
        accept_consent(
            actor=parent, child_id=kid.pk, release_id=release.pk, kind="processing"
        ).pk
        == consent.pk
    )
    kid.refresh_from_db()
    assert kid.status == "active"
    assert public_child_label(kid) == kid.public_label
    accept_consent(
        actor=parent, child_id=kid.pk, release_id=release.pk, kind="publication"
    )
    assert public_child_label(kid) == "Анна Иванова"
    withdraw_consent(actor=parent, child_id=kid.pk, kind="publication")
    assert public_child_label(kid) == kid.public_label
    assert (
        ChildConsent.objects.filter(
            kind="processing", withdrawn_at__isnull=True
        ).count()
        == 1
    )
    release.processing_text = "Изменённый текст"
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError):
        release.full_clean()


@override_settings(JUNIOR_LEAGUE_ENABLED=True, SECURE_SSL_REDIRECT=False)
def test_parent_registration_does_not_require_tennis_profile(client) -> None:
    with patch("apps.juniors.views.send_email_verification", return_value=True):
        response = client.post(
            reverse("juniors:parent_join"),
            {
                "first_name": "Родитель",
                "last_name": "Тестовый",
                "phone": "+79991234567",
                "email": "new-parent@junior.test",
                "password": "ParentTest!47abc",
                "password_confirm": "ParentTest!47abc",
                "agree_legal": "on",
                "agree_terms": "on",
                "adult_confirm": "on",
            },
        )
    assert response.status_code == 302
    parent = User.objects.get(email="new-parent@junior.test")
    assert not Player.objects.filter(user=parent).exists()
    assert parent.email_verified is False
    assert client.get(reverse("juniors:family")).status_code == 200


@override_settings(JUNIOR_LEAGUE_ENABLED=True, SECURE_SSL_REDIRECT=False)
def test_child_creation_requires_verified_parent_contact(client) -> None:
    parent = make_user(email="unverified-parent@junior.test", email_verified=False)
    client.force_login(parent)
    response = client.post(
        reverse("juniors:add_child"),
        {
            "first_name": "Анна",
            "last_name": "Иванова",
            "birth_date": "2018-05-10",
            "city": "Москва",
            "gender": "female",
            "ball": "red",
            "relation": "parent",
            "representative": "on",
        },
    )
    assert response.status_code == 200
    assert "Подтвердите email" in response.content.decode()
    assert not ChildProfile.objects.exists()


@override_settings(JUNIOR_LEAGUE_ENABLED=True, SECURE_SSL_REDIRECT=False)
def test_child_form_requires_csrf_token() -> None:
    parent = make_user(email="csrf-parent@junior.test")
    client = Client(enforce_csrf_checks=True)
    client.force_login(parent)
    assert client.post(reverse("juniors:add_child"), {}).status_code == 403
    assert not ChildProfile.objects.exists()
