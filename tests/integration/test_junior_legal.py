"""Согласие до записи данных, неактивные проекты и безопасная оплата клубов."""

from datetime import date
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.template.loader import get_template

from apps.juniors.billing import begin_payment
from apps.juniors.competition import register
from apps.juniors.forms import ChildForm
from apps.juniors.models import AuditEvent, ChildConsent, ChildProfile, LegalRelease
from apps.juniors.services import create_child, verify_guardian, withdraw_consent
from tests.integration.test_junior_competition import setup_tournament
from tests.support.factories import make_club, make_user

pytestmark = pytest.mark.django_db


def details(release=None):
    return dict(
        first_name="Анна",
        last_name="Тестовая",
        birth_date=date(2018, 1, 1),
        city="Москва",
        gender="female",
        ball="red",
        processing_accepted=True,
        representative=True,
        accurate=True,
        legal_release=release,
    )


def test_creation_requires_current_consent_and_is_atomic():
    parent = make_user(email="legal@kids.test", email_verified=True)
    with pytest.raises(ValidationError):
        create_child(actor=parent, data=details(), relation="parent")
    assert not ChildProfile.objects.exists()
    assert not AuditEvent.objects.exists()
    release = LegalRelease.objects.create(
        version="test",
        processing_text="Текст",
        publication_text="Имя",
        reviewed=True,
        is_current=True,
    )
    data = details(release)
    data["processing_accepted"] = False
    with pytest.raises(ValidationError):
        create_child(actor=parent, data=data, relation="parent")
    data["processing_accepted"] = True
    child = create_child(actor=parent, data=data, relation="parent")
    assert child.status == "draft"
    consent = ChildConsent.objects.get(guardian__child=child)
    assert consent.text_snapshot == "Текст"
    assert consent.guardian.status == "pending"
    assert not ChildConsent.objects.filter(kind="publication").exists()
    reviewer = make_user(email="reviewer@kids.test", is_staff=True, is_superuser=True)
    verify_guardian(
        actor=reviewer,
        link_id=consent.guardian_id,
        approved=True,
        evidence="Предъявление документов",
    )
    child.refresh_from_db()
    assert child.status == "active"


def test_pending_guardian_can_withdraw_initial_consent():
    parent = make_user(email="withdraw@kids.test", email_verified=True)
    release = LegalRelease.objects.create(
        version="test", reviewed=True, is_current=True
    )
    child = create_child(actor=parent, data=details(release), relation="parent")
    withdraw_consent(actor=parent, child_id=child.pk, kind="processing")
    assert not ChildConsent.objects.filter(withdrawn_at__isnull=True).exists()


def test_preparation_does_not_activate_or_overwrite():
    current = LegalRelease.objects.create(
        version="current", reviewed=True, is_current=True
    )
    for _ in range(2):
        call_command("prepare_junior_legal", stdout=StringIO())
    assert LegalRelease.objects.count() == 2
    draft = LegalRelease.objects.exclude(pk=current.pk).get()
    assert not draft.reviewed and not draft.is_current
    assert "561417897115" in draft.processing_text
    current.refresh_from_db()
    assert current.is_current


def test_club_fee_rejected_before_gateway_or_child_lookup(settings):
    organizer, tournament, _ = setup_tournament(fee="100")
    tournament.club = make_club()
    tournament.save()
    with pytest.raises(ValidationError, match="Платная запись"):
        register(
            actor=organizer,
            child_id=999,
            tournament_id=tournament.pk,
            regulation_version=tournament.regulation_version,
        )
    from apps.juniors.models import ChildProfile, GuardianLink, JuniorEntry

    child = ChildProfile.objects.create(
        first_name="А",
        last_name="Т",
        birth_date=date(2018, 1, 1),
        city="Москва",
        gender="female",
    )
    link = GuardianLink.objects.create(child=child, user=organizer)
    entry = JuniorEntry.objects.create(
        child=child, guardian=link, tournament=tournament, status="payment", amount=100
    )
    settings.JUNIOR_PAYMENTS_ENABLED = True
    with patch("apps.juniors.billing.create_payment") as gateway:
        with pytest.raises(ValidationError, match="реквизиты клуба"):
            begin_payment(
                actor=organizer, entry_id=entry.pk, return_url="https://test.invalid/"
            )
        gateway.assert_not_called()


def test_templates_and_edit_form():
    for name in ("child_form", "child"):
        get_template(f"juniors/{name}.html")
    get_template("legal/document.html")
    assert "processing_accepted" in ChildForm().fields
    child = ChildProfile.objects.create(
        first_name="А",
        last_name="Т",
        birth_date=date(2018, 1, 1),
        city="Москва",
        gender="female",
    )
    assert "processing_accepted" not in ChildForm(instance=child).fields


def test_documents_and_missing_release_page(client, settings):
    settings.SECURE_SSL_REDIRECT = False
    settings.JUNIOR_LEAGUE_ENABLED = True
    for slug in (
        "family",
        "privacy",
        "processing",
        "publication",
        "payments",
    ):
        response = client.get(f"/kids/documents/{slug}/", follow=True)
        assert response.status_code == 200
        assert b"<h2>" in response.content
    assert client.get("/kids/documents/unknown/").status_code == 404
    staff = make_user(email="legal-view@kids.test", is_staff=True)
    client.force_login(staff)
    response = client.get("/kids/family/add/")
    assert response.status_code == 200
    assert 'name="first_name"' not in response.content.decode()
    assert "после утверждения" in response.content.decode()


@pytest.mark.parametrize("field", ["representative", "accurate"])
def test_missing_declaration_cannot_create_profile(field):
    parent = make_user(email=f"{field}@kids.test", email_verified=True)
    release = LegalRelease.objects.create(
        version="test", reviewed=True, is_current=True
    )
    data = details(release)
    data.pop(field)
    with pytest.raises(ValidationError, match="законный представитель"):
        create_child(actor=parent, data=data, relation="parent")
    assert not ChildProfile.objects.exists()


def test_closed_profile_and_declarations_are_recorded():
    from django.core.exceptions import PermissionDenied

    from apps.juniors.competition import register
    from apps.juniors.services import public_child_label

    organizer, tournament, release = setup_tournament()
    parent = make_user(email="simple@kids.test", email_verified=True)
    child = create_child(actor=parent, data=details(release), relation="parent")
    event = AuditEvent.objects.get(child=child, action="child.created")
    assert "достоверные" in event.details["declarations"]["accurate"]
    assert "18 лет" in event.details["declarations"]["representative"]
    assert public_child_label(child) == child.public_label
    assert child.birth_verified_at is None
    with pytest.raises(PermissionDenied):
        register(
            actor=parent,
            child_id=child.pk,
            tournament_id=tournament.pk,
            regulation_version=tournament.regulation_version,
        )


def test_simple_profile_form_separates_confirmations_and_creates_closed_profile(
    client, settings
):
    settings.SECURE_SSL_REDIRECT = False
    settings.JUNIOR_LEAGUE_ENABLED = True
    user = make_user(email="simple-form@kids.test", is_staff=True, email_verified=True)
    release = LegalRelease.objects.create(
        version="simple",
        processing_text="Обработка профиля",
        reviewed=True,
        is_current=True,
    )
    client.force_login(user)
    response = client.get("/kids/family/add/")
    assert response.status_code == 200
    html = response.content.decode()
    for name in ("representative", "accurate", "processing_accepted"):
        assert html.count(f'name="{name}"') == 1
    assert 'type="file"' not in html
    assert 'name="publication"' not in html
    response = client.post(
        "/kids/family/add/",
        {
            "first_name": "Анна",
            "last_name": "Тестовая",
            "birth_date": "2018-01-01",
            "city": "Москва",
            "gender": "female",
            "ball": "red",
            "relation": "parent",
            "representative": "on",
            "accurate": "on",
            "processing_accepted": "on",
            "legal_release": str(release.pk),
        },
    )
    assert response.status_code == 302
    child = ChildProfile.objects.get()
    assert child.status == "draft"
    assert child.guardians.get().status == "pending"
    assert not ChildConsent.objects.filter(kind="publication").exists()
