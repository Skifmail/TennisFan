"""Однодневная постоплата: заранее объявленный календарь и запуск накануне."""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from pytest_mock import MockerFixture

from apps.clubs.forms import ClubTournamentCreateForm
from apps.tournaments.admin import TournamentAdminForm, TVDTournamentAdminForm
from apps.tournaments.entry_payment import get_registration_payment_options
from apps.tournaments.models import (
    Tournament,
    TournamentDuration,
    TournamentPostpaymentInvoice,
)
from apps.tournaments.postpayment import (
    open_postpayment_window,
    sync_postpayment_invoices_deadline,
)
from apps.tournaments.views import _save_tournament_registration
from apps.users.models import Player, SkillLevel, User
from tests.support.factories import (
    make_club,
    make_payment_record,
    make_player,
    make_subscription,
)

pytestmark = pytest.mark.usefixtures("deny_external_network")


@pytest.fixture(autouse=True)
def single_day_postpayment_notifications(mocker: MockerFixture) -> None:
    """Не обращаться к внешним каналам при проверке настоящего платёжного сценария."""
    for target in (
        "apps.tournaments.postpayment._send_postpayment_opened_notification",
        "apps.tournaments.postpayment.notify_tournament_postpayment_window_opened",
        "apps.tournaments.postpayment.send_to_user_by_user",
        "apps.tournaments.views._notify_registration_success",
        "apps.tournaments.start_after_fill._notify_min_fill",
        "apps.payments.views._notify_successful_tournament_payment",
        "apps.telegram_bot.notifications.notify_tournament_start",
        "apps.telegram_bot.notifications.notify_match_created",
        "apps.telegram_bot.notifications.notify_bracket_formed",
    ):
        mocker.patch(target)


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["admin", "groups", "club"])
@pytest.mark.parametrize("start_after_fill", [False, True])
def test_single_day_creation_forms_allow_postpayment(
    kind: str, start_after_fill: bool
) -> None:
    """Все три формы допускают постоплату независимо от предварительного набора."""
    start = timezone.localdate() + timedelta(days=30)
    data: dict[str, Any] = {
        "name": "Кубок через месяц",
        "slug": f"single-postpayment-{kind}",
        "city": "Санкт-Петербург",
        "sport": "tennis",
        "format": "single_elimination",
        "variant": "singles",
        "gender": "male",
        "duration": "single",
        "tournament_type": "regular",
        "status": "upcoming",
        "entry_fee": "1000",
        "allowed_categories": ["amateur"],
        "start_date": start.isoformat(),
        "start_time": "10:00",
        "registration_deadline": f"{(start - timedelta(days=1)).isoformat()}T10:00",
        "start_after_fill": start_after_fill,
        "max_participants": "8",
        "match_days_per_round": "7",
        "allow_postpayment": "on",
        "postpayment_deadline_hours": "24",
        "fan_points_r1": "10",
        "fan_points_r2": "25",
        "fan_points_sf": "45",
        "fan_points_final": "70",
        "fan_points_winner": "100",
    }
    form: Any
    if kind == "club":
        form = ClubTournamentCreateForm(data=data, club=make_club())
    elif kind == "groups":
        form = TVDTournamentAdminForm(data=data)
    else:
        form = TournamentAdminForm(data=data)
    assert form.is_valid(), form.errors
    tournament = form.save()
    tournament.refresh_from_db()
    assert tournament.allow_postpayment is True
    assert tournament.duration == TournamentDuration.SINGLE_DAY
    assert tournament.is_one_day is True
    if not start_after_fill:
        assert (tournament.start_date, tournament.end_date) == (start, start)


class SingleDayPostpaymentTestCase(TestCase):
    """Проверить реальные сохранение, регистрацию и окно однодневного турнира."""

    def setUp(self) -> None:
        """Объявить дату игры заранее и подготовить участника без оплаченного взноса."""
        self.start_at = timezone.make_aware(datetime(2030, 6, 1, 10))
        self.opened_at = self.start_at - timedelta(days=1)
        self.user = User.objects.create_user(
            email="single-postpay@test.local", password="x"
        )
        self.player = Player.objects.create(
            user=self.user, skill_level=SkillLevel.AMATEUR
        )
        self.tournament = Tournament.objects.create(
            name="Однодневный заранее",
            slug="single-day-postpayment",
            city="Москва",
            duration=TournamentDuration.SINGLE_DAY,
            start_date=self.start_at.date(),
            start_time=self.start_at.time(),
            registration_deadline=self.opened_at,
            entry_fee=Decimal("1000"),
            allow_postpayment=True,
            postpayment_deadline_hours=48,
            max_participants=8,
        )
        self.tournament.allowed_categories.create(category=SkillLevel.AMATEUR)

    def _open_window(self, opened_at: datetime | None = None) -> None:
        """Открыть окно с настоящим участником в контролируемый момент времени."""
        self.tournament.participants.add(self.player)
        with patch(
            "django.utils.timezone.now", return_value=opened_at or self.opened_at
        ):
            open_postpayment_window(self.tournament)
        self.tournament.refresh_from_db()

    def test_single_day_save_preserves_enabled_postpayment(self) -> None:
        """Сохранение однодневной продолжительности не выключает постоплату молча."""
        self.tournament.refresh_from_db()
        self.assertTrue(self.tournament.allow_postpayment)
        self.assertTrue(self.tournament.is_one_day)

    def test_single_day_registration_offers_postpayment(self) -> None:
        """До открытия окна участник выбирает постоплату вместо немедленного платежа."""
        options = get_registration_payment_options(self.tournament, self.user)
        self.assertEqual([option.value for option in options], ["postpayment"])
        self.assertFalse(self.tournament.postpayment_invoices.exists())
        self.assertFalse(self.tournament.entry_payments.exists())

    def test_single_day_window_opened_yesterday_ends_by_announced_start(self) -> None:
        """Даже окно на 48 часов, открытое накануне, заканчивается к старту игры."""
        self.tournament.participants.add(self.player)
        with (
            patch("django.utils.timezone.now", return_value=self.opened_at),
            patch("apps.tournaments.postpayment._send_postpayment_opened_notification"),
            patch(
                "apps.tournaments.postpayment.notify_tournament_postpayment_window_opened"
            ),
        ):
            created, fancoin_settled = open_postpayment_window(self.tournament)
        self.assertEqual((created, fancoin_settled), (1, 0))
        self.tournament.refresh_from_db()
        self.assertEqual(self.tournament.postpayment_window_started_at, self.opened_at)
        self.assertEqual(self.tournament.start_date, self.start_at.date())
        self.assertEqual(self.tournament.start_time, self.start_at.time())
        self.assertEqual(self.tournament.end_date, self.start_at.date())
        self.assertEqual(
            self.tournament.postpayment_invoices.get(user=self.user).due_at,
            self.start_at,
        )
        self.assertEqual(
            self.tournament.get_postpayment_window_ends_at(), self.start_at
        )

    def test_single_day_invoice_is_payable_only_after_window_opens(self) -> None:
        """Предпросмотр не требует предоплаты, но принимает счёт открытого окна."""
        self.tournament.participants.add(self.player)
        self.client.force_login(self.user)
        url = reverse("payment_preview")
        params: dict[str, str | int] = {"type": "tournament", "id": self.tournament.pk}
        with patch("django.utils.timezone.now", return_value=self.opened_at):
            before = self.client.get(url, params, secure=True)
        self.assertEqual(before.status_code, 302)
        self.assertFalse(self.tournament.entry_payments.exists())
        with (
            patch("django.utils.timezone.now", return_value=self.opened_at),
            patch("apps.tournaments.postpayment._send_postpayment_opened_notification"),
            patch(
                "apps.tournaments.postpayment.notify_tournament_postpayment_window_opened"
            ),
        ):
            open_postpayment_window(self.tournament)
        params["invoice"] = self.tournament.postpayment_invoices.get(user=self.user).pk
        with patch("django.utils.timezone.now", return_value=self.opened_at):
            after = self.client.get(url, params, secure=True)
        self.assertEqual(after.status_code, 200)

    def test_single_day_shorter_window_keeps_configured_deadline(self) -> None:
        """Ограничение временем игры не удлиняет более короткое окно оплаты."""
        self.tournament.postpayment_deadline_hours = 12
        self.tournament.save(update_fields=["postpayment_deadline_hours"])
        self._open_window()
        expected = self.opened_at + timedelta(hours=12)
        self.assertEqual(self.tournament.get_postpayment_window_ends_at(), expected)
        self.assertEqual(self.tournament.postpayment_invoices.get().due_at, expected)

    def test_single_day_cannot_open_window_at_or_after_announced_start(self) -> None:
        """Просроченный запуск не создаёт счета, не закрывает набор и не меняет календарь."""
        self.tournament.participants.add(self.player)
        before = Tournament.objects.filter(pk=self.tournament.pk).values().get()
        for now in (self.start_at, self.start_at + timedelta(seconds=1)):
            with (
                self.subTest(now=now),
                patch("django.utils.timezone.now", return_value=now),
                self.assertRaisesRegex(ValueError, "начал"),
            ):
                open_postpayment_window(self.tournament)
            self.assertEqual(
                Tournament.objects.filter(pk=self.tournament.pk).values().get(), before
            )
            self.assertFalse(self.tournament.postpayment_invoices.exists())
            self.assertFalse(self.tournament.registration_coverages.exists())

    def test_single_day_requires_announced_time_before_opening_window(self) -> None:
        """Историческая запись без времени игры требует явного исправления перед оплатой."""
        self.tournament.participants.add(self.player)
        self.tournament.start_time = None
        self.tournament.save(update_fields=["start_time"])
        with (
            patch("django.utils.timezone.now", return_value=self.opened_at),
            self.assertRaisesRegex(ValueError, "дату и время"),
        ):
            open_postpayment_window(self.tournament)
        self.assertFalse(self.tournament.postpayment_invoices.exists())

    def test_single_day_time_change_updates_only_pending_invoice_deadline(self) -> None:
        """Новое время игры ограничивает действующий счёт, не переписывая закрытые счета."""
        self._open_window()
        history: list[dict[str, Any]] = []
        for status in ("paid", "cancelled"):
            user = make_player(email_suffix=f"single-history-{status}").user
            invoice = TournamentPostpaymentInvoice.objects.create(
                tournament=self.tournament,
                user=user,
                amount=Decimal("812.34"),
                status=status,
                due_at=self.start_at,
                paid_at=self.opened_at if status == "paid" else None,
            )
            history.append(
                TournamentPostpaymentInvoice.objects.filter(pk=invoice.pk)
                .values()
                .get()
            )
        self.tournament.start_time = time(9)
        with patch("django.utils.timezone.now", return_value=self.opened_at):
            self.tournament.save(update_fields=["start_time"])
        expected = self.start_at - timedelta(hours=1)
        pending = self.tournament.postpayment_invoices.get(user=self.user)
        self.assertEqual(pending.due_at, expected)
        self.assertEqual(pending.amount, Decimal("1000"))
        self.assertEqual(self.tournament.get_postpayment_window_ends_at(), expected)
        for before in history:
            self.assertEqual(
                TournamentPostpaymentInvoice.objects.filter(pk=before["id"])
                .values()
                .get(),
                before,
            )

    def test_single_day_date_change_updates_active_invoice_deadline(self) -> None:
        """Перенос даты пересчитывает срок от прежнего открытия, не запуская окно заново."""
        self._open_window()
        assert self.tournament.start_date is not None
        self.tournament.start_date += timedelta(days=1)
        self.tournament.end_date = self.tournament.start_date
        with patch("django.utils.timezone.now", return_value=self.opened_at):
            self.tournament.save(update_fields=["start_date", "end_date"])
        self.tournament.refresh_from_db()
        self.assertEqual(self.tournament.postpayment_window_started_at, self.opened_at)
        self.assertEqual(
            self.tournament.postpayment_invoices.get().due_at,
            self.opened_at + timedelta(hours=48),
        )

    def test_single_day_duration_change_caps_existing_invoice_deadline(self) -> None:
        """Переход к одному дню ограничивает уже открытое окно временем игры."""
        self.tournament.duration = TournamentDuration.MULTI_DAY
        self.tournament.save(update_fields=["duration"])
        self._open_window()
        self.assertEqual(
            self.tournament.postpayment_invoices.get().due_at,
            self.opened_at + timedelta(hours=48),
        )
        self.tournament.duration = TournamentDuration.SINGLE_DAY
        with patch("django.utils.timezone.now", return_value=self.opened_at):
            self.tournament.save(update_fields=["duration"])
        self.tournament.refresh_from_db()
        self.assertTrue(self.tournament.allow_postpayment)
        self.assertEqual(
            self.tournament.postpayment_invoices.get().due_at, self.start_at
        )

    def test_single_day_partial_hours_save_uses_only_persisted_calendar(self) -> None:
        """Несохранённое время игры не влияет на срок счета при сохранении других настроек."""
        self._open_window()
        self.tournament.start_time = time(18)
        self.tournament.postpayment_deadline_hours = 72
        with patch("django.utils.timezone.now", return_value=self.opened_at):
            self.tournament.save(update_fields=["postpayment_deadline_hours"])
        self.tournament.refresh_from_db()
        self.assertEqual(self.tournament.start_time, time(10))
        self.assertEqual(
            self.tournament.postpayment_invoices.get().due_at, self.start_at
        )

    def test_single_day_unrelated_save_does_not_sync_unsaved_payment_settings(
        self,
    ) -> None:
        """Частичная правка описания не протаскивает часы и дату из объекта в счета."""
        self._open_window()
        before = self.tournament.postpayment_invoices.values().get()
        self.tournament.postpayment_deadline_hours = 1
        self.tournament.start_time = time(18)
        self.tournament.description = "Обновлённое описание"
        with patch("django.utils.timezone.now", return_value=self.opened_at):
            self.tournament.save(update_fields=["description"])
        self.assertEqual(self.tournament.postpayment_invoices.values().get(), before)

    def test_single_day_direct_sync_ignores_unsaved_calendar(self) -> None:
        """Сервис синхронизации перечитывает сохранённые настройки под блокировкой."""
        self._open_window()
        self.tournament.start_time = time(18)
        with patch("django.utils.timezone.now", return_value=self.opened_at):
            sync_postpayment_invoices_deadline(self.tournament)
        self.assertEqual(
            self.tournament.postpayment_invoices.get().due_at, self.start_at
        )

    def test_single_day_invoice_keeps_ruble_discount_without_fancoin_debit(
        self,
    ) -> None:
        """Постоплатный счёт учитывает скидку подписки, но не разрешает оплату однодневного FT."""
        with patch("django.utils.timezone.now", return_value=self.opened_at):
            subscription = make_subscription(
                self.user,
                tier_name="single-postpay-discount",
                fancoin_balance=100,
                tier_kwargs={"one_day_tournament_discount": 25},
            )
        self._open_window()
        invoice = self.tournament.postpayment_invoices.get(user=self.user)
        self.assertEqual(invoice.amount, Decimal("750.00"))
        subscription.refresh_from_db()
        self.assertEqual(subscription.fancoin_balance, 100)
        self.assertFalse(self.tournament.registration_coverages.exists())

    def _make_monthly_player(self, suffix: str) -> Player:
        """Создать участника, готового зарегистрироваться без немедленной оплаты."""
        player = make_player(
            email_suffix=f"monthly-{suffix}",
            email_verified=True,
        )
        player.gender = "male"
        player.skill_level = SkillLevel.AMATEUR
        player.save(update_fields=["gender", "skill_level"])
        return player

    def _make_monthly_tournament(self, variant: str) -> Tournament:
        """Объявить однодневный турнир за месяц с явными датой и временем игры."""
        start_date = timezone.localdate() + timedelta(days=30)
        deadline = timezone.make_aware(
            datetime.combine(start_date - timedelta(days=1), time(10))
        )
        is_doubles = variant == "doubles"
        tournament = Tournament.objects.create(
            name=f"Однодневный за месяц ({variant})",
            slug=f"single-day-month-ahead-{variant}",
            city="Москва",
            duration=TournamentDuration.SINGLE_DAY,
            format="single_elimination",
            variant=variant,
            start_date=start_date,
            start_time=time(10),
            registration_deadline=deadline,
            entry_fee=Decimal("1200"),
            allow_postpayment=True,
            postpayment_deadline_hours=48,
            max_participants=None if is_doubles else 8,
            min_participants=None if is_doubles else 2,
            max_teams=4 if is_doubles else None,
            min_teams=2 if is_doubles else None,
        )
        tournament.allowed_categories.create(category=SkillLevel.AMATEUR)
        return tournament

    def _pay_monthly_invoice(
        self,
        tournament: Tournament,
        invoice: TournamentPostpaymentInvoice,
        index: int,
        variant: str,
    ) -> None:
        """Оплатить счёт через настоящий webhook, сохранив сумму выставленного счёта."""
        record = make_payment_record(
            invoice.user,
            yookassa_payment_id=f"monthly-{variant}-{index}",
            payment_type="tournament",
            item_id=str(tournament.pk),
            amount=invoice.amount,
            status="pending",
            metadata={
                "total_amount": f"{invoice.amount:.2f}",
                "postpayment_invoice_id": str(invoice.pk),
            },
        )
        with (
            patch(
                "apps.payments.yookassa_client.get_payment_status",
                return_value="succeeded",
            ),
            TestCase.captureOnCommitCallbacks(execute=True),
        ):
            response = self.client.post(
                reverse("payment_webhook"),
                json.dumps(
                    {
                        "event": "payment.succeeded",
                        "object": {"id": record.yookassa_payment_id},
                    }
                ),
                content_type="application/json",
                secure=True,
            )
        self.assertEqual(response.status_code, 200)
        record.refresh_from_db()
        self.assertEqual(record.status, "succeeded")
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, TournamentPostpaymentInvoice.Status.PAID)
        self.assertEqual(invoice.amount, record.amount)

    def _run_monthly_scenario(self, variant: str) -> None:
        """Пройти путь от объявления за месяц до оплаты и готовой сетки."""
        tournament = self._make_monthly_tournament(variant)
        players = [
            self._make_monthly_player(f"{variant}-{index}")
            for index in range(4 if variant == "doubles" else 2)
        ]
        announced = (tournament.start_date, tournament.start_time, tournament.end_date)
        assert tournament.start_date is not None
        assert tournament.start_time is not None
        announced_start = timezone.make_aware(
            datetime.combine(tournament.start_date, tournament.start_time)
        )
        opened_at = announced_start - timedelta(days=1)

        if variant == "doubles":
            for first, second in ((players[0], players[1]), (players[2], players[3])):
                _save_tournament_registration(
                    tournament,
                    first,
                    "postpayment",
                    partner=second,
                    partner_payment_method="postpayment",
                )
        else:
            for player in players:
                _save_tournament_registration(tournament, player, "postpayment")

        tournament.refresh_from_db()
        if variant == "doubles":
            self.assertEqual(tournament.teams.count(), 2)
            self.assertEqual(tournament.teams.filter(player2__isnull=False).count(), 2)
        else:
            self.assertEqual(tournament.participants.count(), len(players))
        self.assertFalse(tournament.entry_payments.exists())
        self.assertFalse(tournament.postpayment_invoices.exists())

        with patch("django.utils.timezone.now", return_value=opened_at):
            created, fancoin_settled = open_postpayment_window(tournament)
        self.assertEqual((created, fancoin_settled), (len(players), 0))
        tournament.refresh_from_db()
        self.assertEqual(
            (tournament.start_date, tournament.start_time, tournament.end_date),
            announced,
        )
        self.assertEqual(tournament.postpayment_window_started_at, opened_at)
        invoices = list(tournament.postpayment_invoices.order_by("pk"))
        self.assertEqual(len(invoices), len(players))
        for invoice in invoices:
            self.assertEqual(invoice.amount, Decimal("1200"))
            self.assertEqual(invoice.due_at, announced_start)

        for index, invoice in enumerate(invoices):
            self._pay_monthly_invoice(tournament, invoice, index, variant)

        tournament.refresh_from_db()
        self.assertTrue(tournament.bracket_generated)
        self.assertTrue(tournament.matches.exists())
        self.assertEqual(
            (tournament.start_date, tournament.start_time, tournament.end_date),
            announced,
        )
        self.assertEqual(tournament.entry_payments.count(), len(players))
        self.assertFalse(
            tournament.postpayment_invoices.exclude(
                status=TournamentPostpaymentInvoice.Status.PAID
            ).exists()
        )

    def test_single_day_month_ahead_singles_reach_bracket_after_payment(self) -> None:
        """Одиночный турнир за месяц сохраняет объявленный день после оплаты накануне."""
        self._run_monthly_scenario("singles")

    def test_single_day_month_ahead_doubles_keep_pairs_and_calendar(self) -> None:
        """Парный турнир за месяц сохраняет составы пар и календарь после оплаты."""
        self._run_monthly_scenario("doubles")
