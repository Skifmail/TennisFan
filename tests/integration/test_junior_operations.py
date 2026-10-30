"""Фоновые задачи: однократные напоминания и безопасное хранение черновиков."""

from datetime import date, timedelta
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.utils import timezone

from apps.juniors.billing import issue_payment
from apps.juniors.models import (
    ChildProfile,
    GuardianLink,
    JuniorPayment,
    JuniorTournament,
)
from apps.users.models import Notification
from tests.integration.test_junior_competition import participant, setup_tournament
from tests.integration.test_junior_matches import ready_tournament

pytestmark = pytest.mark.django_db


def test_background_reminders_are_not_duplicated() -> None:
    _, tournament = ready_tournament(2, "round_robin")
    JuniorTournament.objects.filter(pk=tournament.pk).update(
        starts_at=timezone.now() + timedelta(hours=12)
    )
    before = Notification.objects.filter(event_key="junior.updated").count()
    call_command("process_junior_league")
    assert Notification.objects.filter(event_key="junior.updated").count() == before + 2
    call_command("process_junior_league")
    assert Notification.objects.filter(event_key="junior.updated").count() == before + 2


def test_draft_retention_dry_run_and_anonymization() -> None:
    _, _, release = setup_tournament()
    user, active = participant(1, release)
    draft = ChildProfile.objects.create(
        first_name="Неиспользуемый",
        last_name="Черновик",
        birth_date=date(timezone.localdate().year - 7, 1, 1),
        city="Москва",
        gender="female",
        created_at=timezone.now() - timedelta(days=60),
    )
    GuardianLink.objects.create(child=draft, user=user)
    call_command("prune_junior_drafts", days=30)
    draft.refresh_from_db()
    assert draft.status == "draft"
    call_command("prune_junior_drafts", days=30, apply=True)
    draft.refresh_from_db()
    active.refresh_from_db()
    assert draft.status == "archived" and draft.last_name == ""
    assert not draft.guardians.exists()
    assert active.status == "active"


def test_uncertain_payment_is_not_reissued_after_safe_window() -> None:
    payment = JuniorPayment(amount=500, created_at=timezone.now() - timedelta(days=2))
    with (
        patch("apps.juniors.billing.create_payment") as create,
        pytest.raises(ValidationError),
    ):
        issue_payment(payment)
    create.assert_not_called()
