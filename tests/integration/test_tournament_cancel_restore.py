"""Интеграционные тесты: отмена турнира и восстановление статуса."""

from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone

from apps.subscriptions.fancoin import TOURNAMENT_REGISTRATION_COST
from apps.subscriptions.models import (
    FancoinTransaction,
    SubscriptionTier,
    UserSubscription,
)
from apps.tournaments.cancel import (
    cancel_tournament,
    restore_tournament_after_cancellation,
)
from apps.tournaments.models import (
    Tournament,
    TournamentRegistrationCoverage,
    TournamentStatus,
)
from apps.tournaments.postpayment import (
    _SUBSCRIPTION_SLOT_COVERAGE,
    build_participant_payment_statuses,
    mark_registration_covered,
)
from apps.users.models import Player, SkillLevel, User


class TournamentCancelRestoreTestCase(TestCase):
    """Отмена возвращает FT и снимает покрытие; восстановление списывает снова."""

    def setUp(self) -> None:
        self.user = User.objects.create_user(email="cancel@test.local", password="x")
        self.player = Player.objects.create(
            user=self.user, skill_level=SkillLevel.AMATEUR
        )
        self.tournament = Tournament.objects.create(
            name="Cancel restore test",
            slug="cancel-restore-test",
            city="Москва",
            start_date=date.today(),
            format="single_elimination",
            entry_fee=500,
            allow_postpayment=True,
            is_one_day=False,
            max_participants=32,
            min_participants=2,
            status=TournamentStatus.UPCOMING,
        )
        self.tournament.allowed_categories.create(category=SkillLevel.AMATEUR)
        self.tournament.participants.add(self.player)
        tier = SubscriptionTier.objects.create(
            name=f"tier-{self.user.pk}",
            display_name="Test",
            fancoin_per_purchase=15,
            duration_days=30,
            is_visible=True,
            is_unlimited=False,
        )
        self.sub = UserSubscription.objects.create(
            user=self.user,
            tier=tier,
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(days=30),
            fancoin_balance=0,
            is_active=True,
        )
        mark_registration_covered(
            self.tournament,
            self.user,
            _SUBSCRIPTION_SLOT_COVERAGE,
        )

    def test_cancel_refunds_ft_and_removes_coverage(self) -> None:
        """При отмене FT возвращаются и покрытие снимается."""
        self.sub.fancoin_balance = 0
        self.sub.save(update_fields=["fancoin_balance"])
        cancel_tournament(self.tournament)
        self.tournament.refresh_from_db()
        self.sub.refresh_from_db()
        self.assertEqual(self.tournament.status, TournamentStatus.CANCELLED)
        self.assertEqual(self.sub.fancoin_balance, TOURNAMENT_REGISTRATION_COST)
        self.assertFalse(
            self.tournament.registration_coverages.filter(user=self.user).exists()
        )
        self.assertTrue(
            FancoinTransaction.objects.filter(
                user=self.user,
                reason=FancoinTransaction.Reason.TOURNAMENT_CANCEL,
                tournament=self.tournament,
            ).exists()
        )
        rows = {
            row.user_id: row
            for row in build_participant_payment_statuses(self.tournament)
        }
        self.assertNotEqual(rows[self.user.id].status, "Покрыто FT (подписка)")

    def test_restore_resettles_ft_from_balance(self) -> None:
        """После восстановления устаревшее покрытие сбрасывается и FT списываются снова."""
        cancel_tournament(self.tournament)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.fancoin_balance, TOURNAMENT_REGISTRATION_COST)

        # Имитация старого бага: покрытие осталось после возврата FT.
        mark_registration_covered(
            self.tournament,
            self.user,
            _SUBSCRIPTION_SLOT_COVERAGE,
        )
        self.tournament.status = TournamentStatus.UPCOMING
        self.tournament.save(update_fields=["status", "updated_at"])

        self.sub.refresh_from_db()
        self.assertEqual(self.sub.fancoin_balance, 0)
        self.assertTrue(
            self.tournament.registration_coverages.filter(
                user=self.user,
                coverage_type=TournamentRegistrationCoverage.CoverageType.SUBSCRIPTION_SLOT,
            ).exists()
        )
        self.assertTrue(
            FancoinTransaction.objects.filter(
                user=self.user,
                reason=FancoinTransaction.Reason.TOURNAMENT_REGISTRATION,
                tournament=self.tournament,
            ).exists()
        )

    def test_restore_helper_clears_stale_coverage(self) -> None:
        """Явный restore снимает фантомное покрытие и списывает FT."""
        self.sub.fancoin_balance = TOURNAMENT_REGISTRATION_COST
        self.sub.save(update_fields=["fancoin_balance"])
        settled = restore_tournament_after_cancellation(self.tournament)
        self.assertEqual(settled, 1)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.fancoin_balance, 0)
