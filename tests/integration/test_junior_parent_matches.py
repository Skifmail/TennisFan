"""Родительский ввод результатов, управление, доставка матчей и защита чужих детей."""

from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.juniors.lifecycle import remove_entry, update_registration
from apps.juniors.matches import record_parent_result, record_result, schedule_match
from apps.juniors.models import AuditEvent, JuniorAward
from apps.users.models import Notification
from tests.integration.test_junior_competition import participant, setup_tournament
from tests.integration.test_junior_matches import ready_tournament
from tests.support.factories import make_user

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def league_settings(settings):
    settings.JUNIOR_LEAGUE_ENABLED = True
    settings.JUNIOR_LEAGUE_READ_ONLY = False
    settings.SECURE_SSL_REDIRECT = False


def submit(actor, match, **kwargs):
    return record_parent_result(
        actor=actor,
        match_id=match.pk,
        scores=kwargs.pop("scores", [[4, 1]]),
        expected_version=kwargs.pop("expected_version", match.result_version),
        **kwargs,
    )


def test_manage_button_and_server_permissions_match(client):
    organizer, tournament = ready_tournament(2)
    match = tournament.matches.get()
    client.force_login(organizer)
    page = client.get(reverse("juniors:tournament", args=[tournament.pk]))
    assert reverse("juniors:manage", args=[tournament.pk]) in page.content.decode()
    client.force_login(match.entry1.guardian.user)
    page = client.get(reverse("juniors:tournament", args=[tournament.pk]))
    assert reverse("juniors:manage", args=[tournament.pk]) not in page.content.decode()
    assert (
        client.get(reverse("juniors:manage", args=[tournament.pk])).status_code == 403
    )
    assert (
        client.post(
            reverse("juniors:manage", args=[tournament.pk]), {"action": "finalize"}
        ).status_code
        == 403
    )


def test_parent_finds_match_submits_score_and_both_families_receive_it(client):
    _, tournament = ready_tournament(2)
    match = tournament.matches.get()
    parent = match.entry1.guardian.user
    opponent = match.entry2.guardian.user
    client.force_login(parent)
    listing = client.get(reverse("juniors:my_matches"))
    html = listing.content.decode()
    assert str(match.entry1.child) in html
    assert str(match.entry2.child) not in html
    url = reverse("juniors:match", args=[match.pk])
    assert url in html
    detail = client.get(url)
    assert "Сохранить результат" in detail.content.decode()
    assert "Детская лига находится в разработке" not in detail.content.decode()
    assert (
        client.post(
            url, {"score": "4:1", "version": match.result_version + 1}
        ).status_code
        == 302
    )
    match.refresh_from_db()
    tournament.refresh_from_db()
    assert match.status == "completed"
    assert tournament.status == "completed"
    assert JuniorAward.objects.filter(tournament=tournament).count() == 2
    assert AuditEvent.objects.filter(actor=parent, action="match.recorded").exists()
    for user in (parent, opponent):
        assert Notification.objects.filter(
            user=user, url=url, message__contains="Сохранён"
        ).exists()
    client.force_login(opponent)
    assert "4:1" in client.get(url).content.decode()
    assert "Сохранить результат" not in client.get(url).content.decode()
    assert "Сообщить об ошибке" in client.get(url).content.decode()
    assert (
        client.post(
            url, {"action": "appeal", "message": "Перепутаны стороны"}
        ).status_code
        == 302
    )
    assert match.appeals.filter(status="open").exists()
    assert Notification.objects.filter(
        user=tournament.organizer, message__contains="возражение"
    ).exists()


@pytest.mark.parametrize(
    "role", ["stranger", "pending", "revoked", "inactive", "suspended"]
)
def test_unrelated_and_unverified_parent_cannot_access_or_write(client, role):
    _, tournament = ready_tournament(2)
    match = tournament.matches.get()
    parent = match.entry1.guardian.user
    if role == "stranger":
        parent = make_user(email="outsider@matches.test")
    elif role in ("pending", "revoked"):
        link = match.entry1.guardian
        link.status = role
        link.save()
    elif role == "inactive":
        parent.is_active = False
        parent.save()
    else:
        child = match.entry1.child
        child.status = "suspended"
        child.save()
    with pytest.raises(PermissionDenied):
        submit(parent, match)
    if role != "inactive":
        client.force_login(parent)
        url = reverse("juniors:match", args=[match.pk])
        assert client.get(url).status_code == 403
        assert client.post(url, {"score": "4:1", "version": 1}).status_code == 403
    match.refresh_from_db()
    assert match.status == "pending"


def test_invalid_score_stale_page_and_repeated_posts_do_not_overwrite():
    _, tournament = ready_tournament(2)
    match = tournament.matches.get()
    parent = match.entry1.guardian.user
    with pytest.raises(ValidationError):
        submit(parent, match, scores=[[0, 0]])
    with pytest.raises(ValidationError, match="изменился"):
        submit(parent, match, expected_version=999)
    submit(parent, match)
    with pytest.raises(ValidationError):
        submit(match.entry2.guardian.user, match, scores=[[0, 4]])
    assert AuditEvent.objects.filter(action="match.recorded").count() == 1
    assert JuniorAward.objects.count() == 2


def test_next_round_notifies_parents_and_stale_lineup_is_rejected():
    organizer, tournament = ready_tournament(4)
    semis = list(tournament.matches.filter(round_number=1))
    final = tournament.matches.get(round_number=2)
    old_version = final.result_version
    for match in semis:
        submit(match.entry1.guardian.user, match)
    final.refresh_from_db()
    assert final.entry1_id and final.entry2_id
    assert final.result_version > old_version
    url = reverse("juniors:match", args=[final.pk])
    assert (
        Notification.objects.filter(url=url, message__contains="следующий матч").count()
        == 2
    )
    parent = final.entry1.guardian.user
    with pytest.raises(ValidationError):
        submit(parent, final, expected_version=old_version)
    record_result(
        actor=organizer, match_id=semis[0].pk, scores=[[0, 4]], reason="Ошибка в счёте"
    )
    with pytest.raises((PermissionDenied, ValidationError)):
        submit(parent, final)


def test_parent_can_report_walkover_and_organizer_can_correct():
    organizer, tournament = ready_tournament(2)
    match = tournament.matches.get()
    submit(
        match.entry2.guardian.user,
        match,
        scores=[],
        walkover_side=1,
        reason="Соперник не явился",
    )
    match.refresh_from_db()
    assert match.status == "walkover"
    assert match.winner_id == match.entry1_id
    record_result(
        actor=organizer,
        match_id=match.pk,
        scores=[[1, 4]],
        reason="Матч всё-таки сыграли",
    )
    match.refresh_from_db()
    assert match.winner_id == match.entry2_id
    assert AuditEvent.objects.filter(action="match.corrected", actor=organizer).exists()


def test_match_schedule_notifications_link_to_match():
    organizer, tournament = ready_tournament(2)
    match = tournament.matches.get()
    schedule_match(
        actor=organizer,
        match_id=match.pk,
        scheduled_at=tournament.starts_at + timedelta(hours=1),
    )
    assert (
        Notification.objects.filter(
            url=reverse("juniors:match", args=[match.pk]),
            message__contains="Расписание",
        ).count()
        == 2
    )


def test_registration_controls_and_removal_preserve_payments():
    organizer, tournament, release = setup_tournament()
    update_registration(actor=organizer, tournament_id=tournament.pk, close=True)
    tournament.refresh_from_db()
    assert tournament.status == "closed"
    assert tournament.registration_ends_at <= timezone.now()
    update_registration(
        actor=organizer,
        tournament_id=tournament.pk,
        deadline=timezone.now() + timedelta(hours=2),
    )
    tournament.refresh_from_db()
    assert tournament.status == "open"
    parent, child = participant(1, release)
    from apps.juniors.competition import register

    entry = register(
        actor=parent,
        child_id=child.pk,
        tournament_id=tournament.pk,
        regulation_version=1,
    )
    with pytest.raises(PermissionDenied):
        remove_entry(actor=parent, entry_id=entry.pk, reason="Хочу снять")
    remove_entry(actor=organizer, entry_id=entry.pk, reason="Просьба родителя")
    entry.refresh_from_db()
    assert entry.status == "withdrawn"
    assert AuditEvent.objects.filter(action="entry.removed", actor=organizer).exists()
    with pytest.raises(ValidationError):
        update_registration(
            actor=organizer,
            tournament_id=tournament.pk,
            deadline=tournament.starts_at + timedelta(days=1),
        )


def test_parent_post_requires_csrf():
    _, tournament = ready_tournament(2)
    match = tournament.matches.get()
    client = Client(enforce_csrf_checks=True)
    client.force_login(match.entry1.guardian.user)
    assert (
        client.post(
            reverse("juniors:match", args=[match.pk]), {"score": "4:1", "version": 1}
        ).status_code
        == 403
    )


def test_development_mode_preserves_read_only_access(client, settings):
    settings.JUNIOR_LEAGUE_READ_ONLY = True
    _, tournament = ready_tournament(2)
    match = tournament.matches.get()
    client.force_login(match.entry1.guardian.user)
    url = reverse("juniors:match", args=[match.pk])
    page = client.get(url)
    assert page.status_code == 200
    assert "Сохранить результат" not in page.content.decode()
    assert client.post(url, {"score": "4:1", "version": 1}).status_code == 403


def test_match_notifications_queue_all_enabled_channels(settings):
    from apps.notifications.models import Delivery, NotificationPreference

    settings.NOTIFICATION_CENTER_ENABLED = True
    organizer, tournament = ready_tournament(2)
    match = tournament.matches.get()
    parents = [match.entry1.guardian.user, match.entry2.guardian.user]
    for parent in parents:
        NotificationPreference.objects.update_or_create(
            user=parent, defaults={"push_enabled": True, "email_enabled": True}
        )
    schedule_match(
        actor=organizer,
        match_id=match.pk,
        scheduled_at=tournament.starts_at + timedelta(hours=1),
    )
    url = reverse("juniors:match", args=[match.pk])
    for parent in parents:
        jobs = Delivery.objects.filter(
            user=parent, notification__url=url, body__contains="Расписание"
        )
        assert set(jobs.values_list("channel", flat=True)) == {"email", "push"}
        assert jobs.count() == 2
        assert all(job.url.endswith(url) for job in jobs)


def test_manual_draw_validates_current_roster_and_keeps_selected_pairs(client):
    from apps.juniors.competition import register
    from apps.juniors.matches import start_tournament
    from apps.juniors.models import JuniorTournament

    organizer, tournament, release = setup_tournament()
    tournament.format = "elimination"
    tournament.capacity = 4
    tournament.save()
    entries = []
    for index in range(3):
        parent, child = participant(index, release)
        entries.append(
            register(
                actor=parent,
                child_id=child.pk,
                tournament_id=tournament.pk,
                regulation_version=1,
            )
        )
    JuniorTournament.objects.filter(pk=tournament.pk).update(
        registration_ends_at=timezone.now() - timedelta(seconds=1)
    )
    with pytest.raises(ValidationError):
        start_tournament(
            actor=organizer,
            tournament_id=tournament.pk,
            entry_order=[entries[0].pk] * 3,
        )
    assert not tournament.matches.exists()
    client.force_login(organizer)
    manage_url = reverse("juniors:manage", args=[tournament.pk])
    html = client.get(manage_url).content.decode()
    assert "Составить первый круг вручную" in html
    assert "Начать турнир · жеребьёвка" in html
    response = client.post(
        manage_url,
        {
            "action": "start_manual",
            "slot_0": entries[0].pk,
            "slot_1": entries[1].pk,
            "slot_2": entries[2].pk,
        },
    )
    assert response.status_code == 302
    first = tournament.matches.get(round_number=1, position=0)
    assert (first.entry1_id, first.entry2_id) == (entries[0].pk, entries[2].pk)
    bye = tournament.matches.get(round_number=1, position=1)
    assert bye.status == "bye" and bye.winner_id == entries[1].pk
    assert (
        AuditEvent.objects.get(action="tournament.started").details["draw_mode"]
        == "manual"
    )


def test_club_manager_can_manage_from_site_without_django_staff_access(client):
    from apps.clubs.models import ClubMember
    from tests.support.factories import make_club

    _, tournament, _ = setup_tournament()
    manager = make_user(email="junior-club-manager@matches.test")
    club = make_club(slug="children-managed-club")
    tournament.club = club
    tournament.save()
    role = ClubMember.objects.create(
        club=club, user=manager, role="manager", status="active"
    )
    client.force_login(manager)
    url = reverse("juniors:manage", args=[tournament.pk])
    assert (
        url
        in client.get(
            reverse("juniors:tournament", args=[tournament.pk])
        ).content.decode()
    )
    assert client.get(url).status_code == 200
    assert client.post(url, {"action": "close_registration"}).status_code == 302
    role.status = "removed"
    role.save()
    assert client.get(url).status_code == 403
