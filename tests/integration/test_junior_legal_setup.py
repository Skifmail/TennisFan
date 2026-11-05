"""Включение готовых согласий одной кнопкой без ручного создания юридических версий."""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client
from django.urls import reverse

from apps.juniors.legal import document_bundle
from apps.juniors.models import (
    AuditEvent,
    ChildConsent,
    ChildProfile,
    GuardianLink,
    LegalRelease,
)
from apps.juniors.services import activate_legal_release, verify_guardian
from tests.integration.test_junior_competition import participant, setup_tournament
from tests.integration.test_junior_family import child
from tests.support.factories import make_user

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def setup_settings(settings):
    settings.SECURE_SSL_REDIRECT = False
    settings.JUNIOR_LEAGUE_ENABLED = True


def owner():
    return make_user(email="legal-owner@kids.test", is_staff=True, is_superuser=True)


def test_empty_admin_connects_defaults_without_accepting_for_parents(client):
    client.force_login(owner())
    response = client.get(reverse("admin:juniors_legalrelease_changelist"))
    assert response.status_code == 200
    html = response.content.decode()
    assert "Согласия включены" in html
    assert "Копировать" not in html or "создавать версии вручную не нужно" in html
    assert reverse("admin:juniors_legalrelease_add") not in html
    assert LegalRelease.objects.get().is_current
    assert not ChildConsent.objects.exists()
    assert client.get(reverse("admin:juniors_activate_legal")).status_code == 405
    assert LegalRelease.objects.count() == 1


def test_admin_activation_is_idempotent_and_does_not_accept_for_parents(client):
    admin = owner()
    client.force_login(admin)
    url = reverse("admin:juniors_activate_legal")
    version, processing, publication = document_bundle()
    for _ in range(2):
        assert client.post(url, {"version": version}).status_code == 302
    release = LegalRelease.objects.get()
    assert release.reviewed and release.is_current
    assert release.processing_text == processing
    assert release.publication_text == publication
    assert not ChildConsent.objects.exists()
    assert AuditEvent.objects.filter(actor=admin, action="legal.activated").count() == 1
    html = client.get(reverse("admin:juniors_legalrelease_changelist")).content.decode()
    assert "Согласия включены" in html
    assert 'class="button junior-legal-enable"' not in html


def test_activation_preserves_existing_consents_and_processing_status():
    admin, _, old_release = setup_tournament()
    parent, kid = participant(1, old_release)
    consent = ChildConsent.objects.get(guardian__child=kid)
    snapshot = consent.text_snapshot
    activate_legal_release(actor=admin, expected_version=document_bundle()[0])
    old_release.refresh_from_db()
    consent.refresh_from_db()
    kid.refresh_from_db()
    assert not old_release.is_current
    assert old_release.reviewed
    assert consent.release_id == old_release.pk
    assert consent.text_snapshot == snapshot
    assert consent.withdrawn_at is None
    assert kid.status == "active"
    assert ChildConsent.objects.count() == 1
    assert LegalRelease.objects.filter(is_current=True).count() == 1


def test_activation_checks_permission_csrf_and_stale_document_version(client):
    admin = owner()
    with pytest.raises(ValidationError, match="обновились"):
        activate_legal_release(actor=admin, expected_version="outdated")
    assert not LegalRelease.objects.exists()
    staff = make_user(email="legal-staff@kids.test", is_staff=True)
    with pytest.raises(PermissionDenied):
        activate_legal_release(actor=staff, expected_version=document_bundle()[0])
    client.force_login(staff)
    assert (
        client.post(
            reverse("admin:juniors_activate_legal"), {"version": document_bundle()[0]}
        ).status_code
        == 403
    )
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(admin)
    assert (
        csrf_client.post(
            reverse("admin:juniors_activate_legal"), {"version": document_bundle()[0]}
        ).status_code
        == 403
    )
    assert not LegalRelease.objects.exists()


def test_existing_verified_profile_activates_after_parent_consent(client):
    admin = owner()
    parent = make_user(email="legacy-parent@kids.test", is_staff=True)
    kid = child()
    link = GuardianLink.objects.create(user=parent, child=kid)
    verify_guardian(
        actor=admin,
        link_id=link.pk,
        approved=True,
        evidence="Оригиналы проверены лично",
    )
    client.force_login(admin)
    assert (
        client.post(
            reverse("admin:juniors_activate_legal"), {"version": document_bundle()[0]}
        ).status_code
        == 302
    )
    kid.refresh_from_db()
    assert kid.status == "draft"
    client.force_login(parent)
    page = client.get(reverse("juniors:child", args=[kid.pk]))
    assert "Подтвердить согласие" in page.content.decode()
    release = LegalRelease.objects.get(is_current=True)
    assert (
        client.post(
            reverse("juniors:consent", args=[kid.pk]),
            {"kind": "processing", "release": release.pk, "accepted": "yes"},
        ).status_code
        == 302
    )
    assert ChildProfile.objects.get(pk=kid.pk).status == "active"
