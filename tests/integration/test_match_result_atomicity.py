"""Атомарное применение результатов без повторного рейтинга и внешних побочных эффектов."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock, patch

from django.core import mail
from django.core.cache import cache
from django.db import transaction
from django.http import JsonResponse
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.models import PlatformActivityEvent, UserTelegramLink
from apps.notifications.models import Delivery
from apps.telegram_bot import views as telegram_views
from apps.telegram_bot.models import TelegramUpdateReceipt
from apps.tournaments.models import Match, MatchResultProposal
from apps.tournaments.proposal_service import ProposalValidationError, apply_proposal
from apps.users.models import Notification
from tests.support.factories import make_player, make_subscription


@override_settings(
    SECURE_SSL_REDIRECT=False,
    ALLOWED_HOSTS=["testserver"],
    TELEGRAM_USER_BOT_WEBHOOK_SECRET="result-atomic-secret",
    TELEGRAM_BOT_SITE_BASE_URL="https://tennisfan.ru",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    USER_NOTIFICATIONS_EMAIL_ENABLED=True,
    NOTIFICATION_CENTER_ENABLED=False,
)
class MatchResultAtomicityTests(TestCase):
    """Проверки общего сервиса, сайта и обоих способов внесения результата в боте."""

    def setUp(self) -> None:
        cache.clear()
        self._mock("apps.telegram_bot.services.is_configured", return_value=True)
        self._mock("apps.player_ratings.notifications.notify_players_to_rate_match")
        self._mock("apps.telegram_bot.views.bot.send_message", return_value=(1, True))
        self._mock("apps.telegram_bot.views._answer_callback")
        self.player1 = make_player(email_suffix="result-atomic-1", points=3000)
        self.player2 = make_player(email_suffix="result-atomic-2", points=2500)
        self.outsider = make_player(email_suffix="result-atomic-outsider")
        for player, chat_id in ((self.player1, 1001), (self.player2, 1002)):
            UserTelegramLink.objects.create(user=player.user, user_bot_chat_id=chat_id)
        self.match = Match.objects.create(
            match_type=Match.MatchType.SPARRING,
            player1=self.player1,
            player2=self.player2,
            deadline=timezone.now() + timedelta(days=7),
        )
        Delivery.objects.all().delete()
        Notification.objects.all().delete()
        mail.outbox.clear()

    def _mock(self, target: str, **kwargs: Any) -> MagicMock:
        """Ограничить внешние вызовы временем одного теста."""
        patcher = patch(target, **kwargs)
        self.addCleanup(patcher.stop)
        mocked = patcher.start()
        assert isinstance(mocked, MagicMock)
        return mocked

    def _proposal(self) -> MatchResultProposal:
        """Сохранить обычную заявку с завершёнными сетами в пользу первой стороны."""
        return MatchResultProposal.objects.create(
            match=self.match,
            proposer=self.player1,
            result=Match.ResultChoice.WIN,
            player1_set1=6,
            player2_set1=4,
            player1_set2=6,
            player2_set2=3,
        )

    def _assert_single_result_notifications(self) -> None:
        """Каждому участнику положено одно событие результата, а не дополнительная заявка."""
        self.assertEqual(Notification.objects.count(), 2)
        self.assertEqual(
            Delivery.objects.filter(event_key="match.result_confirmed").count(), 2
        )
        self.assertFalse(
            Delivery.objects.filter(event_key="match.result_proposed").exists()
        )
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(
            {message.to[0] for message in mail.outbox},
            {self.player1.user.email, self.player2.user.email},
        )
        for notification in Notification.objects.all():
            self.assertIn("Результат матча применён", notification.message)

    def test_replayed_proposal_changes_stats_and_notifies_only_once(self) -> None:
        proposal = self._proposal()
        stale = MatchResultProposal.objects.select_related("match").get(pk=proposal.pk)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertTrue(apply_proposal(proposal))
            self.assertFalse(apply_proposal(stale))
        self.player1.refresh_from_db()
        self.player2.refresh_from_db()
        self.assertEqual(
            (self.player1.matches_played, self.player2.matches_played), (1, 1)
        )
        self._assert_single_result_notifications()

    def test_applied_proposal_cannot_reapply_after_admin_reopens_match(self) -> None:
        proposal = self._proposal()
        apply_proposal(proposal)
        Match.objects.filter(pk=self.match.pk).update(
            status=Match.MatchStatus.SCHEDULED
        )
        with patch(
            "apps.tournaments.proposal_service.charge_fancoin_for_completed_match"
        ) as charge:
            self.assertFalse(apply_proposal(proposal))
        charge.assert_not_called()
        self.match.refresh_from_db()
        self.assertEqual(self.match.status, Match.MatchStatus.SCHEDULED)

    def test_rejected_fresh_proposal_is_not_applied_from_stale_object(self) -> None:
        proposal = self._proposal()
        MatchResultProposal.objects.filter(pk=proposal.pk).update(
            status=Match.ProposalStatus.REJECTED
        )
        with self.assertRaises(ProposalValidationError):
            apply_proposal(proposal)
        self.match.refresh_from_db()
        self.assertEqual(self.match.status, Match.MatchStatus.SCHEDULED)

    def test_cancelled_or_completed_fresh_match_is_not_overwritten(self) -> None:
        proposal = self._proposal()
        for status in (
            Match.MatchStatus.CANCELLED,
            Match.MatchStatus.COMPLETED,
            Match.MatchStatus.WALKOVER,
        ):
            with self.subTest(status=status):
                Match.objects.filter(pk=self.match.pk).update(status=status)
                with self.assertRaises(ProposalValidationError):
                    apply_proposal(proposal)
                self.assertEqual(Match.objects.get(pk=self.match.pk).status, status)

    def test_removed_participant_cannot_apply_stale_proposal(self) -> None:
        proposal = self._proposal()
        Match.objects.filter(pk=self.match.pk).update(player1=self.outsider)
        with self.assertRaises(ProposalValidationError):
            apply_proposal(proposal)
        self.assertEqual(
            Match.objects.get(pk=self.match.pk).status, Match.MatchStatus.SCHEDULED
        )

    def test_saved_proposal_score_is_validated_instead_of_cached_score(self) -> None:
        proposal = self._proposal()
        MatchResultProposal.objects.filter(pk=proposal.pk).update(player1_set1=5)
        with self.assertRaises(ProposalValidationError):
            apply_proposal(proposal)
        self.assertEqual(
            Match.objects.get(pk=self.match.pk).status, Match.MatchStatus.SCHEDULED
        )

    def test_billing_failure_rolls_back_match_rating_and_proposal(self) -> None:
        proposal = self._proposal()
        with patch(
            "apps.tournaments.proposal_service.charge_fancoin_for_completed_match",
            side_effect=RuntimeError("billing failed"),
        ):
            with self.assertRaises(RuntimeError):
                apply_proposal(proposal)
        self.match.refresh_from_db()
        proposal.refresh_from_db()
        self.player1.refresh_from_db()
        self.player2.refresh_from_db()
        self.assertEqual(self.match.status, Match.MatchStatus.SCHEDULED)
        self.assertIsNone(self.match.winner_id)
        self.assertEqual(proposal.status, Match.ProposalStatus.PENDING)
        self.assertEqual(
            (self.player1.matches_played, self.player2.matches_played), (0, 0)
        )
        self.assertEqual(
            (self.player1.total_points, self.player2.total_points), (3000, 2500)
        )
        self.assertFalse(Delivery.objects.exists())
        self.assertFalse(mail.outbox)

    def test_outer_rollback_removes_deliveries_and_never_sends_email(self) -> None:
        proposal = self._proposal()
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                apply_proposal(proposal)
                self.assertFalse(
                    mail.outbox, "Письмо не должно уходить до успешного коммита"
                )
                self.assertEqual(Delivery.objects.count(), 2)
                raise RuntimeError("rollback result")
        self.assertFalse(Delivery.objects.exists())
        self.assertFalse(Notification.objects.exists())
        self.assertFalse(mail.outbox)
        self.assertEqual(
            Match.objects.get(pk=self.match.pk).status, Match.MatchStatus.SCHEDULED
        )

    def test_notification_storage_failure_rolls_back_result_and_rating(self) -> None:
        proposal = self._proposal()
        for target in (
            "apps.tournaments.proposal_service.create_notification",
            "apps.telegram_bot.delivery.Delivery.objects.create",
        ):
            with self.subTest(target=target):
                with patch(
                    target, side_effect=RuntimeError("notification storage failed")
                ):
                    with self.assertRaises(RuntimeError):
                        apply_proposal(proposal)
                self.match.refresh_from_db()
                proposal.refresh_from_db()
                self.player1.refresh_from_db()
                self.assertEqual(self.match.status, Match.MatchStatus.SCHEDULED)
                self.assertIsNone(proposal.applied_at)
                self.assertEqual(self.player1.matches_played, 0)
                self.assertFalse(Notification.objects.exists())
                self.assertFalse(Delivery.objects.exists())
                self.assertFalse(mail.outbox)

    def test_admin_acceptance_keeps_explicit_confirmer(self) -> None:
        proposal = self._proposal()
        proposal.status = Match.ProposalStatus.ACCEPTED
        proposal._confirmed_by_user = self.player2.user
        with self.captureOnCommitCallbacks(execute=True):
            proposal.save(update_fields=["status"])
        self.assertEqual(Match.objects.get(pk=self.match.pk).winner_id, self.player1.pk)
        event = PlatformActivityEvent.objects.get(
            event_type=PlatformActivityEvent.EventType.MATCH_RESULT_CONFIRMED,
            dedupe_key=f"proposal_confirmed:{proposal.pk}",
        )
        self.assertEqual(event.actor_id, self.player2.user_id)
        self._assert_single_result_notifications()

    def test_fancoin_and_result_notifications_are_not_repeated(self) -> None:
        subscriptions = [
            make_subscription(
                player.user, tier_name="atomic-billing", fancoin_balance=50
            )
            for player in (self.player1, self.player2)
        ]
        proposal = self._proposal()
        with self.captureOnCommitCallbacks(execute=True):
            apply_proposal(proposal)
            balances = [
                type(subscription).objects.get(pk=subscription.pk).fancoin_balance
                for subscription in subscriptions
            ]
            apply_proposal(MatchResultProposal.objects.get(pk=proposal.pk))
        self.assertEqual(
            [
                type(subscription).objects.get(pk=subscription.pk).fancoin_balance
                for subscription in subscriptions
            ],
            balances,
        )
        self._assert_single_result_notifications()

    def test_partial_save_from_stale_match_preserves_calculated_result(self) -> None:
        """Редактирование одного дедлайна не откатывает результат из старой копии матча."""
        stale = Match.objects.get(pk=self.match.pk)
        apply_proposal(self._proposal())
        calculated = Match.objects.get(pk=self.match.pk)
        stale.deadline = timezone.now() + timedelta(days=20)
        stale.save(update_fields=["deadline"])
        self.match.refresh_from_db()
        self.player1.refresh_from_db()
        self.assertEqual(self.match.status, Match.MatchStatus.COMPLETED)
        self.assertEqual(self.match.winner_id, self.player1.pk)
        self.assertEqual(self.match.rating_status, Match.RatingCalcStatus.CALCULATED)
        self.assertEqual(
            self.match.rating_delta_player1, calculated.rating_delta_player1
        )
        self.assertEqual(
            self.player1.total_points, 3000 + calculated.rating_delta_player1
        )
        self.assertEqual(self.player1.matches_played, 1)

    def test_partial_result_save_persists_completion_metadata(self) -> None:
        """Явный update_fields сохраняет автоматически рассчитанные состояние и время результата."""
        self.match.winner = self.player1
        self.match.status = Match.MatchStatus.COMPLETED
        self.match.player1_set1, self.match.player2_set1 = 6, 4
        self.match.save(
            update_fields=["winner", "status", "player1_set1", "player2_set1"]
        )
        self.match.refresh_from_db()
        self.assertIsNotNone(self.match.completed_datetime)
        self.assertEqual(self.match.rating_status, Match.RatingCalcStatus.CALCULATED)

    def test_site_applies_one_result_notification_per_participant(self) -> None:
        self.client.force_login(self.player1.user)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("propose_result", args=[self.match.pk]),
                {"result": "win", "p1s1": "6", "p2s1": "4", "p1s2": "6", "p2s2": "3"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Match.objects.get(pk=self.match.pk).winner_id, self.player1.pk)
        self._assert_single_result_notifications()

    def test_site_billing_failure_does_not_leave_proposal_or_activity(self) -> None:
        self.client.force_login(self.player1.user)
        events_before = PlatformActivityEvent.objects.count()
        with patch(
            "apps.tournaments.proposal_service.charge_fancoin_for_completed_match",
            side_effect=RuntimeError("billing failed"),
        ):
            with self.assertRaises(RuntimeError):
                self.client.post(
                    reverse("propose_result", args=[self.match.pk]),
                    {
                        "result": "win",
                        "p1s1": "6",
                        "p2s1": "4",
                        "p1s2": "6",
                        "p2s2": "3",
                    },
                )
        self.assertFalse(self.match.result_proposals.exists())
        self.assertEqual(PlatformActivityEvent.objects.count(), events_before)
        self.assertFalse(Delivery.objects.exists())
        self.assertFalse(mail.outbox)

    def _webhook_response(self, payload: dict[str, Any]) -> JsonResponse:
        """Вызвать настоящий Django-webhook без Telegram-сети."""
        request = RequestFactory().post(
            "/telegram/user-bot-webhook/",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="result-atomic-secret",
        )
        return telegram_views.user_bot_webhook(request)

    def _webhook(self, payload: dict[str, Any]) -> None:
        """Проверить успешную обработку обновления."""
        self.assertEqual(self._webhook_response(payload).status_code, 200)

    def _score_update(self, score: str) -> dict[str, Any]:
        """Подготовить личное сообщение с пользовательской перспективой счёта."""
        return {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "date": 1,
                "from": {"id": 1001},
                "chat": {"id": 1001, "type": "private"},
                "text": score,
            },
        }

    def _proposal_callback(
        self, proposal: MatchResultProposal, action: str
    ) -> dict[str, Any]:
        """Подготовить нажатие старой кнопки соперником."""
        return {
            "update_id": 1,
            "callback_query": {
                "id": "legacy-callback",
                "from": {"id": 1002},
                "data": f"proposal_{action}_{proposal.pk}",
                "message": {
                    "message_id": 1,
                    "date": 1,
                    "chat": {"id": 1002, "type": "private"},
                },
            },
        }

    def test_invalid_bot_score_leaves_no_activity_and_can_be_corrected(self) -> None:
        cache_key = telegram_views.CACHE_KEY_RESULT_ENTRY % 1001
        cache.set(cache_key, self.match.pk)
        events_before = PlatformActivityEvent.objects.count()
        self._webhook(self._score_update("5:4 6:3"))
        self.assertFalse(self.match.result_proposals.exists())
        self.assertEqual(PlatformActivityEvent.objects.count(), events_before)
        self.assertEqual(cache.get(cache_key), self.match.pk)
        self.assertFalse(Delivery.objects.exists())

    def test_bot_rollback_after_score_keeps_input_state_for_retry(self) -> None:
        cache_key = telegram_views.CACHE_KEY_RESULT_ENTRY % 1001
        cache.set(cache_key, self.match.pk)
        with patch(
            "apps.telegram_bot.views.bot.send_message",
            side_effect=RuntimeError("reply failed"),
        ):
            response = self._webhook_response(self._score_update("6:4 6:3"))
        self.assertEqual(response.status_code, 503)
        self.assertFalse(self.match.result_proposals.exists())
        self.assertFalse(TelegramUpdateReceipt.objects.exists())
        self.assertEqual(cache.get(cache_key), self.match.pk)
        self.assertFalse(Delivery.objects.exists())
        self.assertFalse(mail.outbox)

    def test_legacy_confirm_technical_failure_is_retryable_and_keeps_buttons(
        self,
    ) -> None:
        proposal = self._proposal()
        with (
            patch(
                "apps.tournaments.proposal_service.charge_fancoin_for_completed_match",
                side_effect=RuntimeError("billing failed"),
            ),
            patch(
                "apps.telegram_bot.views._edit_message_remove_reply_markup"
            ) as remove,
        ):
            response = self._webhook_response(
                self._proposal_callback(proposal, "confirm")
            )
        self.assertEqual(response.status_code, 503)
        remove.assert_not_called()
        self.assertFalse(TelegramUpdateReceipt.objects.exists())
        self.assertEqual(
            MatchResultProposal.objects.get(pk=proposal.pk).status,
            Match.ProposalStatus.PENDING,
        )

    def test_stale_reject_does_not_delete_concurrently_applied_proposal(self) -> None:
        proposal = self._proposal()
        link = UserTelegramLink.objects.get(user=self.player2.user)

        def apply_before_link_read(chat_id: int | None) -> UserTelegramLink:
            """Имитировать применение после чтения заявки, но до проверки полномочий."""
            apply_proposal(proposal)
            return link

        with patch(
            "apps.telegram_bot.views._get_link_by_chat_id",
            side_effect=apply_before_link_read,
        ):
            self._webhook(self._proposal_callback(proposal, "reject"))
        fresh = MatchResultProposal.objects.get(pk=proposal.pk)
        self.assertIsNotNone(fresh.applied_at)
        self.assertEqual(fresh.status, Match.ProposalStatus.ACCEPTED)
        self.assertFalse(
            Delivery.objects.filter(event_key="match.proposal_updated").exists()
        )

    def test_bot_score_applies_one_result_notification_per_participant(self) -> None:
        from django.core.cache import cache

        cache.set(telegram_views.CACHE_KEY_RESULT_ENTRY % 1001, self.match.pk)
        with self.captureOnCommitCallbacks(execute=True):
            self._webhook(
                {
                    "update_id": 1,
                    "message": {
                        "message_id": 1,
                        "date": 1,
                        "from": {"id": 1001},
                        "chat": {"id": 1001, "type": "private"},
                        "text": "6:4 6:3",
                    },
                }
            )
        self._assert_single_result_notifications()

    def test_bot_no_show_applies_one_result_notification_per_participant(self) -> None:
        with self.captureOnCommitCallbacks(execute=True):
            self._webhook(
                {
                    "update_id": 1,
                    "callback_query": {
                        "id": "atomic-callback",
                        "from": {"id": 1001},
                        "data": f"result_type_{self.match.pk}_walkover_win",
                        "message": {
                            "message_id": 1,
                            "date": 1,
                            "chat": {"id": 1001, "type": "private"},
                        },
                    },
                }
            )
        self._assert_single_result_notifications()
