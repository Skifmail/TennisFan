"""Групповые турниры проходят те же проверки постоплаты при любом пути запуска."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

from django.core.cache import cache
from django.db.models import QuerySet
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.tournaments.fan import check_and_generate_past_deadline_brackets
from apps.tournaments.models import (
    Match,
    Tournament,
    TournamentDuration,
    TournamentEntryPayment,
    TournamentFormat,
    TournamentPostpaymentInvoice,
    TournamentRegistrationCoverage,
    TournamentStatus,
    TournamentTeam,
    TVDGroup,
)
from apps.tournaments.postpayment import (
    _remove_unpaid_players,
    finalize_postpayment_window,
    open_postpayment_window,
)
from apps.tournaments.start_after_fill import maybe_progress_start_after_fill
from apps.users.models import Player, User
from tests.support.factories import make_player, make_tournament, make_user


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    SECURE_SSL_REDIRECT=False,
    ALLOWED_HOSTS=["testserver"],
)
class TournamentGroupPostpaymentTests(TestCase):
    """Открытое окно нельзя обойти повторной кнопкой, cron или повторным набором."""

    players: list[Player]
    manager: User

    @classmethod
    def setUpTestData(cls) -> None:
        cls.players = [make_player(email_suffix=f"group-postpay-{i}") for i in range(4)]
        cls.manager = make_user(
            email="group-postpay-manager@test.local", is_staff=True, is_superuser=True
        )

    def setUp(self) -> None:
        self.counter = 0
        for target in (
            "apps.tournaments.postpayment._send_postpayment_opened_notification",
            "apps.tournaments.postpayment.notify_tournament_postpayment_window_opened",
            "apps.tournaments.postpayment.send_to_user_by_user",
            "apps.tournaments.start_after_fill._notify_min_fill",
            "apps.telegram_bot.notifications.notify_tournament_start",
            "apps.telegram_bot.notifications.notify_match_created",
            "apps.telegram_bot.notifications.notify_bracket_formed",
        ):
            notification = patch(target)
            notification.start()
            self.addCleanup(notification.stop)
        self.client.force_login(self.manager)
        cache.delete("tournament_generate_brackets_last_run")
        self.addCleanup(cache.delete, "tournament_generate_brackets_last_run")

    def _tournament(self, **overrides: Any) -> Tournament:
        """Создать платный турнир с четырьмя участниками без покрытия оплаты."""
        self.counter += 1
        options: dict[str, Any] = {
            "format": TournamentFormat.WEEKEND_DAY,
            "duration": TournamentDuration.MULTI_DAY,
            "entry_fee": 1000,
            "allow_postpayment": True,
            "registration_deadline": timezone.now() - timedelta(hours=1),
            "min_participants": 4,
            "max_participants": 4,
        }
        options.update(overrides)
        tournament = make_tournament(slug=f"group-postpay-{self.counter}", **options)
        tournament.participants.set(self.players)
        return tournament

    @staticmethod
    def _matches(tournament: Tournament) -> QuerySet[Match]:
        """Выбрать матчи без зависимости от динамических атрибутов модели."""
        return Match.objects.filter(tournament=tournament)

    @staticmethod
    def _invoices(tournament: Tournament) -> QuerySet[TournamentPostpaymentInvoice]:
        """Выбрать инвойсы турнира через типизированный менеджер модели."""
        return TournamentPostpaymentInvoice.objects.filter(tournament=tournament)

    def _assert_waiting(self, tournament: Tournament) -> None:
        """Набор и долг сохранены, группы и матчи ещё не созданы."""
        tournament.refresh_from_db()
        self.assertIsNotNone(tournament.postpayment_window_started_at)
        self.assertFalse(tournament.bracket_generated)
        self.assertFalse(self._matches(tournament).exists())
        self.assertFalse(TVDGroup.objects.filter(tournament=tournament).exists())
        self.assertEqual(tournament.participants.count(), 4)
        self.assertEqual(self._invoices(tournament).filter(status="pending").count(), 4)

    def test_group_postpayment_endpoint_waits_on_repeated_start(self) -> None:
        """Специальная кнопка групп открывает окно один раз и ждёт оплату."""
        tournament = self._tournament()
        url = reverse(
            "tournament_manage_generate_groups", kwargs={"slug": tournament.slug}
        )
        self.assertEqual(self.client.post(url).status_code, 302)
        self._assert_waiting(tournament)
        started = tournament.postpayment_window_started_at
        self.assertEqual(self.client.post(url).status_code, 302)
        self._assert_waiting(tournament)
        self.assertEqual(tournament.postpayment_window_started_at, started)

    def test_group_postpayment_endpoint_requires_management_access(self) -> None:
        """Участник не может открыть окно или создать группы чужого турнира."""
        tournament = self._tournament()
        self.client.force_login(self.players[0].user)
        url = reverse(
            "tournament_manage_generate_groups", kwargs={"slug": tournament.slug}
        )
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertFalse(self._invoices(tournament).exists())
        self.assertFalse(self._matches(tournament).exists())

    def test_group_postpayment_admin_actions_wait_on_repeated_start(self) -> None:
        """Общий и отфильтрованный разделы админки одинаково сохраняют ожидание."""
        for model_name in ("tournament", "tvdtournament"):
            with self.subTest(model=model_name):
                tournament = self._tournament()
                url = reverse(f"admin:tournaments_{model_name}_changelist")
                payload = {
                    "action": "generate_tvd_groups_action",
                    "_selected_action": [str(tournament.pk)],
                }
                self.assertEqual(self.client.post(url, payload).status_code, 302)
                self._assert_waiting(tournament)
                self.assertEqual(self.client.post(url, payload).status_code, 302)
                self._assert_waiting(tournament)

    def test_group_postpayment_cron_handles_all_formats_without_bypassing_window(
        self,
    ) -> None:
        """Автостарт по дедлайну включает группы и повторно не обходит открытое окно."""
        for tournament_format in TournamentFormat.values:
            with self.subTest(format=tournament_format):
                tournament = self._tournament(format=tournament_format)
                cache.delete("tournament_generate_brackets_last_run")
                self.assertEqual(check_and_generate_past_deadline_brackets(), 0)
                self._assert_waiting(tournament)
                cache.delete("tournament_generate_brackets_last_run")
                self.assertEqual(check_and_generate_past_deadline_brackets(), 0)
                self._assert_waiting(tournament)

    def test_group_postpayment_repeat_max_fill_waits_for_payment(self) -> None:
        """Повторное достижение максимума не запускает сетку до оплаты."""
        tournament = self._tournament()
        Tournament.objects.filter(pk=tournament.pk).update(
            start_after_fill=True,
            start_date=None,
            end_date=None,
            registration_deadline=None,
        )
        tournament.refresh_from_db()
        self.assertEqual(
            maybe_progress_start_after_fill(tournament), "postpayment_opened"
        )
        self._assert_waiting(tournament)
        self.assertEqual(
            maybe_progress_start_after_fill(tournament), "postpayment_opened"
        )
        self._assert_waiting(tournament)
        self.assertIsNone(tournament.start_date)

    def test_group_postpayment_finalizer_cannot_start_before_window(self) -> None:
        """Прямой вызов финализатора не разрешает запуск до открытия окна."""
        tournament = self._tournament()
        ok, _ = finalize_postpayment_window(tournament)
        self.assertFalse(ok)
        self.assertFalse(self._matches(tournament).exists())
        self.assertEqual(tournament.participants.count(), 4)

    def test_group_postpayment_finalizer_preserves_unexpired_debts(self) -> None:
        """Ранний вызов финализатора не отменяет инвойсы и не создаёт группы."""
        tournament = self._tournament()
        open_postpayment_window(tournament)
        ok, _ = finalize_postpayment_window(tournament)
        self.assertFalse(ok)
        self._assert_waiting(tournament)

    def test_group_postpayment_free_window_does_not_require_unissued_invoices(
        self,
    ) -> None:
        """Бесплатные участники без счетов не блокируют завершение старого окна."""
        tournament = self._tournament(
            entry_fee=0, postpayment_window_started_at=timezone.now()
        )
        TournamentPostpaymentInvoice.objects.create(
            tournament=tournament,
            user=self.players[0].user,
            amount=1000,
            due_at=timezone.now() + timedelta(hours=1),
            status=TournamentPostpaymentInvoice.Status.PAID,
            paid_at=timezone.now(),
        )
        invoices_before = list(self._invoices(tournament).values())

        ok, message = finalize_postpayment_window(tournament)

        self.assertTrue(ok, message)
        tournament.refresh_from_db()
        self.assertEqual(tournament.status, TournamentStatus.GROUP_STAGE)
        self.assertGreater(self._matches(tournament).count(), 0)
        self.assertEqual(tournament.participants.count(), 4)
        self.assertEqual(list(self._invoices(tournament).values()), invoices_before)

    def test_group_postpayment_missing_invoices_still_block_paid_tournament(
        self,
    ) -> None:
        """В платном турнире отсутствие счёта не означает оплату остальных участников."""
        tournament = self._tournament(postpayment_window_started_at=timezone.now())
        TournamentPostpaymentInvoice.objects.create(
            tournament=tournament,
            user=self.players[0].user,
            amount=1000,
            due_at=timezone.now() + timedelta(hours=1),
            status=TournamentPostpaymentInvoice.Status.PAID,
        )
        invoices_before = list(self._invoices(tournament).values())

        ok, _ = finalize_postpayment_window(tournament)

        self.assertFalse(ok)
        tournament.refresh_from_db()
        self.assertFalse(tournament.bracket_generated)
        self.assertFalse(self._matches(tournament).exists())
        self.assertEqual(tournament.participants.count(), 4)
        self.assertEqual(list(self._invoices(tournament).values()), invoices_before)

    def test_group_postpayment_zero_fee_preserves_existing_debt(self) -> None:
        """Обнуление взноса не отменяет уже выставленные действующие счета."""
        tournament = self._tournament()
        open_postpayment_window(tournament)
        tournament.entry_fee = 0
        tournament.save(update_fields=["entry_fee", "updated_at"])
        invoices_before = list(self._invoices(tournament).order_by("pk").values())

        ok, _ = finalize_postpayment_window(tournament)

        self.assertFalse(ok)
        self._assert_waiting(tournament)
        self.assertEqual(
            list(self._invoices(tournament).order_by("pk").values()), invoices_before
        )

    def test_group_postpayment_finalizer_waits_for_remaining_unexpired_invoice(
        self,
    ) -> None:
        """После удаления просроченного участника нельзя отменять ещё действующий долг."""
        tournament = self._tournament(min_participants=3)
        open_postpayment_window(tournament)
        self._invoices(tournament).filter(user=self.players[0].user).update(
            due_at=timezone.now() - timedelta(minutes=1)
        )
        ok, _ = finalize_postpayment_window(tournament)
        self.assertFalse(ok)
        self.assertFalse(self._matches(tournament).exists())
        self.assertEqual(tournament.participants.count(), 3)
        self.assertEqual(self._invoices(tournament).filter(status="expired").count(), 1)
        self.assertEqual(self._invoices(tournament).filter(status="pending").count(), 3)
        tournament.refresh_from_db()
        self.assertEqual(tournament.status, TournamentStatus.UPCOMING)

    def test_group_postpayment_paid_window_starts_groups_once(self) -> None:
        """Оплаченные инвойсы сохраняются, а повторный финализатор не дублирует группы."""
        tournament = self._tournament()
        open_postpayment_window(tournament)
        self._invoices(tournament).update(
            status=TournamentPostpaymentInvoice.Status.PAID
        )
        ok, message = finalize_postpayment_window(tournament)
        self.assertTrue(ok, message)
        tournament.refresh_from_db()
        self.assertEqual(tournament.status, TournamentStatus.GROUP_STAGE)
        count = self._matches(tournament).count()
        self.assertGreater(count, 0)
        self.assertTrue(finalize_postpayment_window(tournament)[0])
        self.assertEqual(self._matches(tournament).count(), count)
        self.assertEqual(self._invoices(tournament).filter(status="paid").count(), 4)

    def test_group_postpayment_fill_start_records_short_event_dates(self) -> None:
        """После оплаты турнира без фиксированных дат задаётся реальный день старта."""
        tournament = self._tournament(duration=TournamentDuration.WEEKEND)
        Tournament.objects.filter(pk=tournament.pk).update(
            start_after_fill=True,
            start_date=None,
            end_date=None,
            registration_deadline=None,
        )
        tournament.refresh_from_db()
        open_postpayment_window(tournament)
        self._invoices(tournament).update(
            status=TournamentPostpaymentInvoice.Status.PAID
        )
        ok, message = finalize_postpayment_window(tournament)
        self.assertTrue(ok, message)
        tournament.refresh_from_db()
        self.assertEqual(tournament.start_date, timezone.localdate())
        self.assertEqual(tournament.end_date, timezone.localdate() + timedelta(days=1))

    def test_group_postpayment_expired_invoices_do_not_remove_settled_players(
        self,
    ) -> None:
        """Просроченная строка инвойса не отменяет оплату или выданное покрытие."""
        tournament = self._tournament()
        open_postpayment_window(tournament)
        self._invoices(tournament).update(due_at=timezone.now() - timedelta(minutes=1))
        for player in (self.players[0], self.players[3]):
            TournamentEntryPayment.objects.create(
                tournament=tournament, user=player.user
            )
        TournamentRegistrationCoverage.objects.create(
            tournament=tournament,
            user=self.players[1].user,
            coverage_type=TournamentRegistrationCoverage.CoverageType.ADMIN_GRANTED,
        )
        self._invoices(tournament).filter(user=self.players[2].user).update(
            status="paid"
        )
        payments = TournamentEntryPayment.objects.filter(tournament=tournament)
        coverages = TournamentRegistrationCoverage.objects.filter(tournament=tournament)
        payments_before = list(payments.order_by("pk").values())
        coverage_before = list(coverages.values())

        ok, message = finalize_postpayment_window(tournament)
        self.assertTrue(ok, message)
        self.assertEqual(tournament.participants.count(), 4)
        self.assertFalse(self._invoices(tournament).filter(status="expired").exists())
        self.assertEqual(self._invoices(tournament).filter(status="paid").count(), 1)
        self.assertEqual(list(payments.order_by("pk").values()), payments_before)
        self.assertEqual(list(coverages.values()), coverage_before)

    def test_group_postpayment_expiry_removes_only_unpaid_doubles_member(self) -> None:
        """Оплаченный партнёр остаётся неполной командой без удаления истории взноса."""
        tournament = self._tournament(variant="doubles", min_teams=2, max_teams=2)
        protected_team = TournamentTeam.objects.create(
            tournament=tournament, player1=self.players[0], player2=self.players[1]
        )
        incomplete_team = TournamentTeam.objects.create(
            tournament=tournament, player1=self.players[2], player2=self.players[3]
        )
        open_postpayment_window(tournament)
        self._invoices(tournament).update(due_at=timezone.now() - timedelta(minutes=1))
        TournamentEntryPayment.objects.create(
            tournament=tournament, user=self.players[0].user
        )
        TournamentRegistrationCoverage.objects.create(
            tournament=tournament,
            user=self.players[1].user,
            coverage_type=TournamentRegistrationCoverage.CoverageType.ADMIN_GRANTED,
        )
        self._invoices(tournament).filter(user=self.players[2].user).update(
            status="paid"
        )
        paid_history = (
            self._invoices(tournament).filter(user=self.players[2].user).values().get()
        )

        self.assertEqual(_remove_unpaid_players(tournament), 1)
        self.assertEqual(
            set(tournament.participants.values_list("pk", flat=True)),
            {player.pk for player in self.players[:3]},
        )
        self.assertEqual(
            list(
                TournamentTeam.objects.filter(tournament=tournament)
                .values_list("pk", flat=True)
                .order_by("pk")
            ),
            [protected_team.pk, incomplete_team.pk],
        )
        incomplete_team.refresh_from_db()
        self.assertEqual(incomplete_team.player1_id, self.players[2].pk)
        self.assertIsNone(incomplete_team.player2_id)
        self.assertEqual(
            self._invoices(tournament).filter(user=self.players[2].user).values().get(),
            paid_history,
        )
        self.assertEqual(
            list(
                self._invoices(tournament)
                .filter(status="expired")
                .values_list("user_id", flat=True)
            ),
            [self.players[3].user.pk],
        )
