"""Проверки допуска, очереди и неизменности регламентов детских турниров."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.contrib import admin
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.clubs.models import ClubMember
from apps.juniors.competition import promote_waitlist, register, withdraw
from apps.juniors.models import (
    ChildConsent,
    ChildProfile,
    GuardianLink,
    JuniorEntry,
    JuniorSeason,
    JuniorTournament,
    LegalRelease,
)
from apps.juniors.permissions import accessible_competitions, require_organizer
from tests.support.factories import make_club, make_user

pytestmark = pytest.mark.django_db


def setup_tournament(*, fee: str = "0"):
    organizer = make_user(email="organizer@kids.test", is_staff=True, is_superuser=True)
    start = timezone.now() + timedelta(days=7)
    season = JuniorSeason.objects.create(
        name="Тестовый сезон",
        starts_on=date(start.year, 1, 1),
        ends_on=date(start.year, 12, 31),
    )
    tournament = JuniorTournament.objects.create(
        name="Красный старт",
        city="Москва",
        venue="Корт 1",
        organizer=organizer,
        season=season,
        ball="red",
        min_age=5,
        max_age=10,
        starts_at=start,
        registration_ends_at=start - timedelta(days=1),
        capacity=2,
        regulation="Тестовый регламент",
        status="open",
        entry_fee=Decimal(fee),
    )
    release = LegalRelease.objects.create(
        version="test",
        processing_text="Обработка",
        publication_text="Публикация",
        reviewed=True,
        is_current=True,
    )
    return organizer, tournament, release


def participant(number, release):
    user = make_user(email=f"parent-{number}@kids.test")
    kid = ChildProfile.objects.create(
        first_name=f"Имя{number}",
        last_name="ЗакрытаяФамилия",
        birth_date=date(timezone.localdate().year - 7, 1, 1),
        city="Москва",
        gender="female",
        status="active",
        birth_verified_at=timezone.now(),
    )
    link = GuardianLink.objects.create(child=kid, user=user, status="verified")
    ChildConsent.objects.create(
        guardian=link, release=release, kind="processing", text_snapshot="Обработка"
    )
    return user, kid


def test_registration_capacity_queue_and_duplicate() -> None:
    organizer, tournament, release = setup_tournament()
    entries = []
    for number in range(3):
        user, kid = participant(number, release)
        entries.append(
            register(
                actor=user,
                child_id=kid.pk,
                tournament_id=tournament.pk,
                regulation_version=1,
            )
        )
    assert [e.status for e in entries] == ["confirmed", "confirmed", "waitlist"]
    with pytest.raises(ValidationError):
        register(
            actor=user,
            child_id=kid.pk,
            tournament_id=tournament.pk,
            regulation_version=1,
        )
    withdraw(actor=entries[0].guardian.user, entry_id=entries[0].pk)
    assert (
        promote_waitlist(actor=organizer, entry_id=entries[2].pk).status == "confirmed"
    )
    tournament.ball = "green"
    with pytest.raises(ValidationError):
        tournament.full_clean()


def test_expired_payment_reservation_releases_capacity() -> None:
    _, tournament, release = setup_tournament(fee="500")
    user, kid = participant(1, release)
    entry = register(
        actor=user, child_id=kid.pk, tournament_id=tournament.pk, regulation_version=1
    )
    assert entry.status == "payment"
    assert entry.amount == Decimal("500")
    assert entry.regulation_snapshot["fee"] == "500.00"
    JuniorEntry.objects.filter(pk=entry.pk).update(
        reserved_until=timezone.now() - timedelta(seconds=1)
    )
    user2, kid2 = participant(2, release)
    register(
        actor=user2, child_id=kid2.pk, tournament_id=tournament.pk, regulation_version=1
    )
    entry.refresh_from_db()
    assert entry.status == "withdrawn"


@override_settings(JUNIOR_LEAGUE_ENABLED=True, SECURE_SSL_REDIRECT=False)
def test_public_tournament_does_not_disclose_private_name(client) -> None:
    _, tournament, release = setup_tournament()
    user, kid = participant(1, release)
    register(
        actor=user, child_id=kid.pk, tournament_id=tournament.pk, regulation_version=1
    )
    response = client.get(reverse("juniors:tournament", args=[tournament.pk]))
    assert response.status_code == 200
    assert "ЗакрытаяФамилия" not in response.content.decode()
    assert str(kid.birth_date) not in response.content.decode()
    assert "no-store" in response["Cache-Control"]
    assert client.get(reverse("juniors:tournaments")).status_code == 200


@override_settings(JUNIOR_LEAGUE_ENABLED=True, SECURE_SSL_REDIRECT=False)
def test_catalog_filters_validate_dates_and_age(client) -> None:
    _, tournament, _ = setup_tournament()
    response = client.get(reverse("juniors:tournaments"), {"age": "12"})
    assert (
        response.status_code == 200 and tournament.name not in response.content.decode()
    )
    response = client.get(reverse("juniors:tournaments"), {"date_from": "bad-date"})
    assert response.status_code == 200
    assert "jl-error-text" in response.content.decode()


def test_admin_organizer_cannot_see_other_tournament_entries() -> None:
    _, tournament, release = setup_tournament()
    user, kid = participant(1, release)
    entry = register(
        actor=user, child_id=kid.pk, tournament_id=tournament.pk, regulation_version=1
    )
    other = make_user(email="another-organizer@kids.test", is_staff=True)
    request = RequestFactory().get("/")
    request.user = other
    entry_admin = admin.site._registry[JuniorEntry]
    assert not entry_admin.get_queryset(request).filter(pk=entry.pk).exists()


def test_revoked_club_role_removes_competition_access() -> None:
    _, tournament, _ = setup_tournament()
    user = make_user(email="club-organizer@kids.test", is_staff=True)
    user.user_permissions.add(
        Permission.objects.get(
            content_type__app_label="juniors", codename="manage_competition"
        )
    )
    club = make_club(slug="junior-club")
    tournament.club = club
    tournament.organizer = user
    tournament.save()
    with pytest.raises(PermissionDenied):
        require_organizer(user, tournament)
    membership = ClubMember.objects.create(
        club=club, user=user, role="manager", status="active"
    )
    require_organizer(user, tournament)
    assert accessible_competitions(user).filter(pk=tournament.pk).exists()
    membership.status = "removed"
    membership.save()
    with pytest.raises(PermissionDenied):
        require_organizer(user, tournament)
    assert not accessible_competitions(user).filter(pk=tournament.pk).exists()


def test_yellow_ball_rule_uses_competition_category_not_automatic_birthday() -> None:
    _, tournament, _ = setup_tournament()
    tournament.ball = "yellow"
    tournament.min_age = 9
    tournament.max_age = 12
    tournament.full_clean()
    tournament.max_age = 10
    with pytest.raises(ValidationError):
        tournament.full_clean()
