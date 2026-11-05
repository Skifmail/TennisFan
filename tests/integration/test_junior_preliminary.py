"""Предварительные заявки: места, проверка, оплата и отказ в допуске."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import override_settings
from django.utils import timezone

from apps.juniors.billing import begin_payment
from apps.juniors.competition import (
    expire_reservations,
    occupied_seats,
    promote_waitlist,
    register,
    withdraw,
)
from apps.juniors.matches import start_tournament
from apps.juniors.models import (
    ChildConsent,
    JuniorEntry,
    JuniorPayment,
    JuniorTournament,
)
from apps.juniors.services import ensure_default_legal_release, verify_guardian
from tests.integration.test_junior_competition import participant, setup_tournament

pytestmark = pytest.mark.django_db


def pending(number, release):
    parent, kid = participant(number, release)
    kid.status = "draft"
    kid.birth_verified_at = None
    kid.save()
    link = kid.guardians.get()
    link.status = "pending"
    link.save()
    return parent, kid, link


def apply(parent, kid, tournament):
    return register(
        actor=parent, child_id=kid.pk, tournament_id=tournament.pk, regulation_version=1
    )


@override_settings(JUNIOR_PAYMENTS_ENABLED=True)
def test_verification_opens_payment_even_after_application_deadline():
    admin, tournament, release = setup_tournament(fee="500")
    parent, kid, link = pending(1, release)
    entry = apply(parent, kid, tournament)
    assert entry.status == "preliminary"
    assert entry.reserved_until is None
    assert occupied_seats(tournament) == 1
    with pytest.raises((ValidationError, PermissionDenied)):
        begin_payment(
            actor=parent,
            entry_id=entry.pk,
            return_url="https://tennisfan.ru/kids/payments/",
        )
    assert not JuniorPayment.objects.exists()
    JuniorTournament.objects.filter(pk=tournament.pk).update(
        registration_ends_at=timezone.now() - timedelta(minutes=1)
    )
    with pytest.raises(ValidationError, match="представителей"):
        start_tournament(actor=admin, tournament_id=tournament.pk)
    verify_guardian(
        actor=admin, link_id=link.pk, approved=True, evidence="Проверены оригиналы"
    )
    entry.refresh_from_db()
    kid.refresh_from_db()
    assert kid.status == "active"
    assert entry.status == "payment"
    deadline = entry.reserved_until
    verify_guardian(
        actor=admin, link_id=link.pk, approved=True, evidence="Проверены оригиналы"
    )
    entry.refresh_from_db()
    assert entry.reserved_until == deadline
    with patch(
        "apps.juniors.billing.create_payment",
        return_value=("prelim-1", "https://yookassa.ru/pay/1"),
    ):
        payment = begin_payment(
            actor=parent,
            entry_id=entry.pk,
            return_url="https://tennisfan.ru/kids/payments/",
        )
    assert payment.entry_id == entry.pk


def test_preliminary_holds_capacity_and_waitlist_preserves_order():
    admin, tournament, release = setup_tournament()
    tournament.capacity = 1
    tournament.save()
    p1, c1, link = pending(1, release)
    first = apply(p1, c1, tournament)
    p2, c2, _ = pending(2, release)
    second = apply(p2, c2, tournament)
    assert second.status == "waitlist"
    with pytest.raises(ValidationError):
        apply(p1, c1, tournament)
    withdraw(actor=p1, entry_id=first.pk)
    assert promote_waitlist(actor=admin, entry_id=second.pk).status == "preliminary"
    assert occupied_seats(tournament) == 1


def test_rejection_releases_seat_and_no_show_expires():
    admin, tournament, release = setup_tournament()
    p1, c1, link = pending(1, release)
    entry = apply(p1, c1, tournament)
    verify_guardian(
        actor=admin,
        link_id=link.pk,
        approved=False,
        evidence="",
        reason="Не подтверждено родство",
    )
    entry.refresh_from_db()
    assert entry.status == "withdrawn"
    assert occupied_seats(tournament) == 0
    p2, c2, _ = pending(2, release)
    entry2 = apply(p2, c2, tournament)
    JuniorTournament.objects.filter(pk=tournament.pk).update(
        starts_at=timezone.now() - timedelta(seconds=1)
    )
    tournament.refresh_from_db()
    expire_reservations(tournament)
    assert JuniorEntry.objects.get(pk=entry2.pk).status == "withdrawn"


def test_preliminary_requires_consent_and_valid_sporting_category():
    _, tournament, release = setup_tournament()
    parent, kid, link = pending(1, release)
    kid.ball = "green"
    kid.save()
    with pytest.raises(ValidationError, match="мячу"):
        apply(parent, kid, tournament)
    kid.ball = "red"
    kid.save()
    ChildConsent.objects.filter(guardian=link).delete()
    with pytest.raises(ValidationError, match="согласие"):
        apply(parent, kid, tournament)


def test_default_documents_preserve_current_approval_and_do_not_accept_for_family():
    _, _, release = setup_tournament()
    assert ensure_default_legal_release().pk == release.pk
    assert not ChildConsent.objects.exists()


def test_revoking_guardian_frees_preliminary_place():
    from apps.juniors.lifecycle import revoke_guardian

    admin, tournament, release = setup_tournament()
    parent, kid, link = pending(1, release)
    entry = apply(parent, kid, tournament)
    revoke_guardian(actor=admin, link_id=link.pk, reason="Полномочия прекращены")
    entry.refresh_from_db()
    assert entry.status == "withdrawn"
    assert occupied_seats(tournament) == 0


def test_pending_applicant_receives_cancellation_notice():
    from apps.juniors.lifecycle import cancel_tournament

    admin, tournament, release = setup_tournament()
    parent, kid, _ = pending(1, release)
    entry = apply(parent, kid, tournament)
    with patch("apps.juniors.notifications.notify_representative") as notify:
        cancel_tournament(
            actor=admin, tournament_id=tournament.pk, reason="Закрыта площадка"
        )
    assert notify.call_count == 1
    assert notify.call_args.args[0].pk == parent.pk
    assert "отменён" in notify.call_args.args[2]
    entry.refresh_from_db()
    assert entry.status == "withdrawn"
