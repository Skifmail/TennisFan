"""Разрешение имени отдельно от участия и отзыв на исторических страницах."""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from apps.juniors.competition import register
from apps.juniors.matches import record_result
from apps.juniors.models import ChildConsent, GuardianLink, LegalRelease
from apps.juniors.ranking import category_key, finalize, leaderboard
from apps.juniors.services import accept_consent, public_child_label, withdraw_consent
from tests.integration.test_junior_competition import participant, setup_tournament
from tests.integration.test_junior_matches import ready_tournament
from tests.support.factories import make_user

pytestmark = pytest.mark.django_db


def test_no_publication_consent_needed_for_entry():
    _, tournament, release = setup_tournament()
    parent, child = participant(1, release)
    assert public_child_label(child) == f"Участник №{child.pk}"
    assert (
        register(
            actor=parent,
            child_id=child.pk,
            tournament_id=tournament.pk,
            regulation_version=1,
        ).status
        == "confirmed"
    )
    assert not ChildConsent.objects.filter(kind="publication").exists()


def test_withdraw_hides_historical_matches_and_rating(client, settings):
    settings.SECURE_SSL_REDIRECT = False
    settings.JUNIOR_LEAGUE_ENABLED = True
    organizer, tournament = ready_tournament(2, "round_robin")
    match = tournament.matches.get()
    entry = match.entry1
    child, parent = entry.child, entry.guardian.user
    release = LegalRelease.objects.get(is_current=True)
    consent = accept_consent(
        actor=parent, child_id=child.pk, release_id=release.pk, kind="publication"
    )
    assert consent.text_snapshot == release.publication_text
    record_result(actor=organizer, match_id=match.pk, scores=[[4, 0]])
    finalize(actor=organizer, tournament_id=tournament.pk)
    url = f"/kids/tournaments/{tournament.pk}/"
    rating_url = f"/kids/ratings/?season={tournament.season_id}&ball=red"
    assert str(child) in client.get(url).content.decode()
    assert str(child) in client.get(rating_url).content.decode()
    points = list(tournament.awards.values_list("child_id", "points"))
    withdraw_consent(actor=parent, child_id=child.pk, kind="publication")
    for page in (url, rating_url):
        response = client.get(page)
        assert response.status_code == 200
        assert str(child) not in response.content.decode()
        assert child.public_label in response.content.decode()
        assert "no-store" in response["Cache-Control"]
    assert list(tournament.awards.values_list("child_id", "points")) == points
    assert any(
        row["label"] == child.public_label
        for row in leaderboard(
            season=tournament.season, ball="red", category=category_key(tournament)
        )
    )
    child.refresh_from_db()
    assert child.status == "active"
    assert ChildConsent.objects.filter(
        guardian=entry.guardian, kind="processing", withdrawn_at__isnull=True
    ).exists()


def test_any_parent_can_stop_publication_and_old_release_can_be_withdrawn(
    client, settings
):
    settings.SECURE_SSL_REDIRECT = False
    settings.JUNIOR_LEAGUE_ENABLED = True
    _, _, release = setup_tournament()
    parent, child = participant(1, release)
    other = make_user(email="other@kids.test", is_staff=True)
    GuardianLink.objects.create(child=child, user=other, status="verified")
    for actor in (parent, other):
        accept_consent(
            actor=actor, child_id=child.pk, release_id=release.pk, kind="publication"
        )
    release.is_current = False
    release.save(update_fields=["is_current"])
    client.force_login(other)
    page = client.get(f"/kids/family/{child.pk}/")
    assert page.status_code == 200
    assert "Показывать имя и фамилию в результатах" in page.content.decode()
    assert (
        client.post(
            f"/kids/family/{child.pk}/consent/",
            {"kind": "publication", "action": "withdraw"},
        ).status_code
        == 302
    )
    assert not ChildConsent.objects.filter(
        guardian__child=child, kind="publication", withdrawn_at__isnull=True
    ).exists()
    assert public_child_label(child) == child.public_label


def test_cannot_publish_pending_profile_or_another_child():
    _, _, release = setup_tournament()
    parent, child = participant(1, release)
    child.status = "draft"
    child.save(update_fields=["status"])
    with pytest.raises(ValidationError):
        accept_consent(
            actor=parent, child_id=child.pk, release_id=release.pk, kind="publication"
        )
    stranger = make_user(email="stranger@kids.test")
    with pytest.raises(PermissionDenied):
        withdraw_consent(actor=stranger, child_id=child.pk, kind="publication")
    assert not ChildConsent.objects.filter(kind="publication").exists()
