"""Единый подсчёт мест, однократные напоминания и безопасная диагностика запуска."""

import json
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.juniors.competition import occupied_seats, register
from apps.juniors.matches import record_result, schedule_match
from apps.juniors.models import (
    AuditEvent,
    ChildConsent,
    GuardianLink,
    JuniorEntry,
    JuniorMatch,
    JuniorTournament,
    LegalRelease,
)
from apps.juniors.reminders import send_match_reminders
from apps.juniors.views import upcoming_tournaments
from apps.users.models import Notification
from tests.integration.test_junior_competition import participant, setup_tournament
from tests.integration.test_junior_matches import ready_tournament

pytestmark = pytest.mark.django_db


def test_home_catalog_and_registration_count_preliminary_places_consistently(
    client, settings
):
    settings.JUNIOR_LEAGUE_ENABLED = True
    settings.SECURE_SSL_REDIRECT = False
    _, tournament, release = setup_tournament()
    parent, kid = participant(1, release)
    kid.status = "draft"
    kid.birth_verified_at = None
    kid.save()
    GuardianLink.objects.filter(child=kid).update(status="pending")
    entry = register(
        actor=parent, child_id=kid.pk, tournament_id=tournament.pk, regulation_version=1
    )
    assert entry.status == "preliminary"
    assert occupied_seats(tournament) == 1
    assert upcoming_tournaments(now=timezone.now())[0]["free_places"] == 1
    tournament.capacity = 1
    tournament.save()
    response = client.get("/kids/tournaments/", {"available": "on"})
    assert tournament.name not in response.content.decode()
    assert upcoming_tournaments(now=timezone.now())[0]["free_places"] == 0
    entry.status = "payment"
    entry.reserved_until = timezone.now() - timedelta(seconds=1)
    entry.save()
    assert occupied_seats(tournament) == 0
    assert upcoming_tournaments(now=timezone.now())[0]["free_places"] == 1
    assert (
        tournament.name
        in client.get("/kids/tournaments/", {"available": "on"}).content.decode()
    )


def test_match_reminder_is_once_and_repeated_after_actual_reschedule():
    organizer, tournament = ready_tournament(2)
    match = tournament.matches.get()
    planned = timezone.now() + timedelta(hours=12)
    JuniorTournament.objects.filter(pk=tournament.pk).update(
        starts_at=timezone.now() - timedelta(days=1)
    )
    schedule_match(actor=organizer, match_id=match.pk, scheduled_at=planned)
    assert send_match_reminders() == 1
    assert send_match_reminders() == 0
    notes = Notification.objects.filter(
        url=f"/kids/matches/{match.pk}/", message__contains="Напоминаем о матче"
    )
    assert notes.count() == 2
    schedule_match(actor=organizer, match_id=match.pk, scheduled_at=planned)
    assert send_match_reminders() == 0
    schedule_match(
        actor=organizer, match_id=match.pk, scheduled_at=planned + timedelta(hours=1)
    )
    assert send_match_reminders() == 1
    assert notes.count() == 4
    record_result(actor=organizer, match_id=match.pk, scores=[[4, 1]])
    assert send_match_reminders() == 0


@pytest.mark.parametrize(
    "state",
    ["unscheduled", "past", "far_future", "cancelled", "suspended", "withdrawn"],
)
def test_match_reminder_skips_unavailable_or_outside_window(state):
    _, tournament = ready_tournament(2)
    match = tournament.matches.get()
    match.scheduled_at = timezone.now() + timedelta(hours=12)
    if state == "unscheduled":
        match.scheduled_at = None
    elif state == "past":
        match.scheduled_at = timezone.now() - timedelta(seconds=1)
    elif state == "far_future":
        match.scheduled_at = timezone.now() + timedelta(days=2)
    elif state == "cancelled":
        JuniorTournament.objects.filter(pk=tournament.pk).update(status="cancelled")
    elif state == "suspended":
        kid = match.entry1.child
        kid.status = "suspended"
        kid.save()
    else:
        JuniorEntry.objects.filter(pk=match.entry1_id).update(status="withdrawn")
    match.save()
    assert send_match_reminders() == 0
    assert not Notification.objects.filter(
        message__contains="Напоминаем о матче"
    ).exists()


def test_upstream_correction_clears_match_schedule_and_reminder():
    organizer, tournament = ready_tournament(4)
    semis = list(tournament.matches.filter(round_number=1))
    for semifinal in semis:
        record_result(actor=organizer, match_id=semifinal.pk, scores=[[4, 1]])
    final = tournament.matches.get(round_number=2)
    JuniorMatch.objects.filter(pk=final.pk).update(
        scheduled_at=timezone.now() + timedelta(hours=1),
        reminder_sent_at=timezone.now(),
    )
    record_result(
        actor=organizer,
        match_id=semis[0].pk,
        scores=[[1, 4]],
        reason="Исправление сторон",
    )
    final.refresh_from_db()
    assert final.scheduled_at is None and final.reminder_sent_at is None


def test_launch_audit_does_not_approve_documents_or_create_consents(settings):
    from pathlib import Path
    from tempfile import gettempdir

    settings.JUNIOR_PRIVATE_MEDIA_ROOT = (
        Path(gettempdir()) / "junior-audit-missing-directory"
    )
    before = (
        LegalRelease.objects.count(),
        ChildConsent.objects.count(),
        AuditEvent.objects.count(),
    )
    output = StringIO()
    call_command("check_junior_launch", stdout=output)
    report = json.loads(output.getvalue())
    assert report["read_only_audit"] is True
    assert report["pending_migrations"] == []
    assert report["configured_jobs"]["process_junior_league"]
    assert report["private_photos"]["outside_public_roots"]
    assert report["legal"]["current_count"] == 0
    assert before == (
        LegalRelease.objects.count(),
        ChildConsent.objects.count(),
        AuditEvent.objects.count(),
    )
