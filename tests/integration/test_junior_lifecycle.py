"""Права второго представителя, возражения и сохранение истории при взрослении."""

from datetime import date

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import override_settings
from django.utils import timezone

from apps.juniors.appeals import resolve_appeal, submit_appeal
from apps.juniors.competition import register
from apps.juniors.lifecycle import (
    add_representative,
    cancel_tournament,
    correct_profile,
    reassign_entry,
    revoke_guardian,
    transfer_to_adult,
)
from apps.juniors.matches import record_result
from apps.juniors.models import (
    ChildConsent,
    ChildProfile,
    GuardianLink,
    JuniorAward,
    JuniorPayment,
)
from apps.juniors.notifications import notify_family
from apps.juniors.permissions import guardian_for
from apps.juniors.ranking import finalize
from apps.juniors.services import verify_guardian
from apps.juniors.statistics import child_statistics
from apps.notifications.models import Delivery, NotificationPreference
from tests.integration.test_junior_competition import participant, setup_tournament
from tests.integration.test_junior_matches import ready_tournament
from tests.support.factories import make_user

pytestmark = pytest.mark.django_db


def test_second_representative_is_not_automatically_verified() -> None:
    verifier, _, release = setup_tournament()
    first, kid = participant(1, release)
    second = make_user(email="second@junior.test")
    link = add_representative(
        actor=verifier, child_id=kid.pk, user_id=second.pk, relation="parent"
    )
    with pytest.raises(PermissionDenied):
        guardian_for(second, kid)
    verify_guardian(
        actor=verifier,
        link_id=link.pk,
        approved=True,
        evidence="Оригиналы и совершеннолетие проверены лично",
    )
    assert guardian_for(second, kid).status == "verified"
    revoke_guardian(actor=verifier, link_id=link.pk, reason="Полномочия прекращены")
    assert guardian_for(first, kid)
    with pytest.raises(PermissionDenied):
        guardian_for(second, kid)


def test_appeal_blocks_finalization_and_statistics_exclude_walkover() -> None:
    organizer, tournament = ready_tournament(2, "round_robin")
    match = tournament.matches.get()
    record_result(
        actor=organizer, match_id=match.pk, scores=[], walkover_side=1, reason="Неявка"
    )
    parent = match.entry1.guardian.user
    appeal = submit_appeal(
        actor=parent,
        child_id=match.entry1.child_id,
        match_id=match.pk,
        message="Просим проверить результат",
    )
    with pytest.raises(ValidationError):
        finalize(actor=organizer, tournament_id=tournament.pk)
    resolve_appeal(actor=organizer, appeal_id=appeal.pk, response="Результат проверен")
    finalize(actor=organizer, tournament_id=tournament.pk)
    assert JuniorAward.objects.filter(points__gt=0).count() == 0
    red = child_statistics(match.entry1.child)["by_ball"][0]
    assert red["played"] == 0 and red["technical"] == 1
    cancel_tournament(
        actor=organizer, tournament_id=tournament.pk, reason="Решение организатора"
    )
    assert not JuniorAward.objects.exists()


def test_cannot_change_ball_with_live_entry() -> None:
    organizer, tournament = ready_tournament(2, "round_robin")
    kid = tournament.entries.first().child
    with pytest.raises(ValidationError):
        correct_profile(
            actor=organizer,
            child_id=kid.pk,
            data={"ball": "green"},
            evidence="Проверка тренером",
            reason="Переход",
        )


def test_adult_transfer_revokes_parent_but_preserves_child_id() -> None:
    verifier = make_user(
        email="adult-verifier@junior.test", is_superuser=True, is_staff=True
    )
    parent = make_user(email="adult-parent@junior.test")
    target = make_user(email="adult-owner@junior.test")
    kid = ChildProfile.objects.create(
        first_name="Анна",
        last_name="Иванова",
        birth_date=date(timezone.localdate().year - 19, 1, 1),
        city="Москва",
        gender="female",
        status="active",
    )
    GuardianLink.objects.create(child=kid, user=parent, status="verified")
    transferred = transfer_to_adult(
        actor=verifier,
        child_id=kid.pk,
        user_id=target.pk,
        evidence="Получатель предъявил документ и подтвердил передачу аккаунту",
    )
    assert transferred.pk == kid.pk and transferred.adult_user_id == target.pk
    with pytest.raises(PermissionDenied):
        guardian_for(parent, transferred)


@override_settings(NOTIFICATION_CENTER_ENABLED=True)
def test_notifications_queue_email_and_push_once_for_current_representative() -> None:
    _, _, release = setup_tournament()
    user, kid = participant(1, release)
    NotificationPreference.objects.create(
        user=user, push_enabled=True, email_enabled=True
    )
    notify_family(kid, "Результат опубликован")
    jobs = Delivery.objects.filter(user=user, event_key="junior.updated")
    assert set(jobs.values_list("channel", flat=True)) == {"push", "email"}
    assert jobs.count() == 2
    assert all(kid.last_name not in job.body for job in jobs)


def test_representative_change_keeps_original_payer() -> None:
    organizer, tournament, release = setup_tournament(fee="500")
    parent, kid = participant(1, release)
    entry = register(
        actor=parent, child_id=kid.pk, tournament_id=tournament.pk, regulation_version=1
    )
    payment = JuniorPayment.objects.create(
        entry=entry,
        payer=parent,
        reservation=entry.reserved_until,
        amount=500,
        status="paid",
    )
    entry.status = "confirmed"
    entry.save()
    second = make_user(email="paid-second@kids.test")
    link = GuardianLink.objects.create(child=kid, user=second, status="verified")
    ChildConsent.objects.create(
        guardian=link, release=release, kind="processing", text_snapshot="Тест"
    )
    reassign_entry(
        actor=organizer,
        entry_id=entry.pk,
        guardian_id=link.pk,
        reason="Новый представитель подтвердил участие",
    )
    payment.refresh_from_db()
    entry.refresh_from_db()
    assert entry.guardian_id == link.pk
    assert payment.payer_id == parent.pk
