"""Обязательные уведомления сохраняются атомарно вместе с игровыми действиями."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock, patch

from django.core import mail
from django.db import transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.core.models import UserTelegramLink
from apps.notifications.models import Delivery, NotificationPreference
from apps.sparring.doubles_services import (
    accept_join_request,
    create_doubles_request,
    create_join_request,
)
from apps.sparring.models import (
    DoublesJoinRequestStatus,
    DoublesMatchRequestStatus,
    SparringInvitation,
    TeamSide,
)
from apps.sparring.services import accept_sparring_invitation
from apps.telegram_bot.notifications import _send_notification_email_to_user
from apps.tournaments.models import Match, MatchResultProposal
from apps.tournaments.proposal_service import submit_match_result
from apps.users.models import Notification
from tests.support.factories import make_player, make_user
from tests.support.notifications import seed_event_policies


@override_settings(
    NOTIFICATION_CENTER_ENABLED=False,
    USER_NOTIFICATIONS_EMAIL_ENABLED=True,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    TELEGRAM_BOT_SITE_BASE_URL="https://tennisfan.ru",
)
class TelegramNotificationAtomicityTests(TestCase):
    """Проверки настоящих сервисов уведомлений, без подмены оценки соперника."""

    def setUp(self) -> None:
        """Создать связанных игроков и исключить обращения к внешней сети."""
        self._mock("apps.telegram_bot.services.is_configured", return_value=True)
        self._mock("apps.sparring.utils.user_has_sparring_access", return_value=True)
        self.player1 = make_player(email_suffix="mandatory-1", points=3000)
        self.player2 = make_player(email_suffix="mandatory-2", points=2500)
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
        """Ограничить подмену временем одного теста."""
        patcher = patch(target, **kwargs)
        self.addCleanup(patcher.stop)
        mocked = patcher.start()
        assert isinstance(mocked, MagicMock)
        return mocked

    def _submit(self) -> MatchResultProposal:
        """Применить настоящий результат с обоими видами уведомлений."""
        return submit_match_result(
            self.match.pk,
            self.player1,
            result="win",
            player1_set1=6,
            player2_set1=4,
            player1_set2=6,
            player2_set2=3,
        )

    def _assert_result_rolled_back(self) -> None:
        """Проверить результат, рейтинг и все ранее сохранённые уведомления."""
        self.match.refresh_from_db()
        self.player1.refresh_from_db()
        self.assertEqual(self.match.status, Match.MatchStatus.SCHEDULED)
        self.assertEqual(self.player1.matches_played, 0)
        self.assertEqual(self.player1.total_points, 3000)
        self.assertFalse(self.match.result_proposals.exists())
        self.assertFalse(Delivery.objects.exists())
        self.assertFalse(Notification.objects.exists())
        self.assertFalse(mail.outbox)

    def test_rating_inbox_failure_rolls_back_result(self) -> None:
        """Оценка соперника не теряется после успешной записи основного уведомления."""
        with patch(
            "apps.player_ratings.notifications.create_notification",
            side_effect=RuntimeError("rating inbox failed"),
        ):
            with self.assertRaises(RuntimeError):
                self._submit()
        self._assert_result_rolled_back()

    def test_rating_delivery_failure_rolls_back_result(self) -> None:
        """Сбой позднего Telegram-задания откатывает результат и предыдущие задания."""
        original = Delivery.objects.create

        def create(**kwargs: Any) -> Delivery:
            """Отказать только на этапе Telegram-приглашения оценить соперника."""
            if kwargs.get("event_key") == "match.rate_players":
                raise RuntimeError("rating delivery failed")
            return original(**kwargs)

        with patch(
            "apps.telegram_bot.delivery.Delivery.objects.create", side_effect=create
        ):
            with self.assertRaises(RuntimeError):
                self._submit()
        self._assert_result_rolled_back()

    def test_second_recipient_failure_rolls_back_first_recipient(self) -> None:
        """Нельзя зафиксировать результат только с уведомлением одной стороны."""
        original = Delivery.objects.create

        def create(**kwargs: Any) -> Delivery:
            """Отказать на втором участнике основного уведомления."""
            if kwargs.get("user_id") == self.player2.user_id:
                raise RuntimeError("second recipient failed")
            return original(**kwargs)

        with patch(
            "apps.telegram_bot.delivery.Delivery.objects.create", side_effect=create
        ):
            with self.assertRaises(RuntimeError):
                self._submit()
        self._assert_result_rolled_back()

    def test_partial_rating_failure_rolls_back_result_and_first_player(self) -> None:
        """Сбой рейтинга второй стороны откатывает уже изменённую первую сторону."""
        from apps.users.sport_rating import commit_rating

        calls = 0

        def commit(player: Any, points: float, sport: str) -> None:
            """Дать первой записи пройти и воспроизвести поздний отказ расчёта."""
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("second rating failed")
            commit_rating(player, points, sport)

        with patch("apps.users.sport_rating.commit_rating", side_effect=commit):
            with self.assertRaises(RuntimeError):
                self._submit()
        self.assertEqual(calls, 2)
        self._assert_result_rolled_back()

    def test_invitation_acceptance_and_notifications_are_one_transaction(self) -> None:
        """Сервис принятия не оставляет матч без уведомления пригласившего."""
        invitation = SparringInvitation.objects.create(
            inviter=self.player1, invitee=self.player2
        )
        with patch(
            "apps.telegram_bot.notifications.create_notification",
            side_effect=RuntimeError("invitation inbox failed"),
        ):
            with self.assertRaises(RuntimeError):
                accept_sparring_invitation(invitation.pk, self.player2.user_id)
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, SparringInvitation.Status.PENDING)
        self.assertIsNone(invitation.match_id)
        self.assertEqual(Match.objects.count(), 1)
        self.assertFalse(Delivery.objects.exists())
        self.assertFalse(mail.outbox)

    def test_team_ready_notification_failure_rolls_back_join_acceptance(self) -> None:
        """Полный состав и кнопка формирования матчей фиксируются вместе."""
        partner1 = make_player(email_suffix="mandatory-partner1")
        partner2 = make_player(email_suffix="mandatory-partner2")
        request = create_doubles_request(
            created_by=self.player1, partner=partner1, kind="team"
        )
        join = create_join_request(
            match_request_id=request.pk,
            created_by=self.player2,
            target_side=TeamSide.OPPONENT,
            players=[self.player2, partner2],
        )
        with patch(
            "apps.sparring.doubles_services.create_notification",
            side_effect=RuntimeError("team ready inbox failed"),
        ):
            with self.assertRaises(RuntimeError):
                accept_join_request(
                    match_request_id=request.pk,
                    join_request_id=join.pk,
                    accepted_by=self.player1,
                )
        request.refresh_from_db()
        join.refresh_from_db()
        self.assertEqual(request.status, DoublesMatchRequestStatus.FORMING)
        self.assertEqual(join.status, DoublesJoinRequestStatus.PENDING)
        self.assertFalse(request.teams.filter(side=TeamSide.OPPONENT).exists())
        self.assertFalse(Delivery.objects.exists())
        self.assertFalse(mail.outbox)


@override_settings(
    NOTIFICATION_CENTER_ENABLED=True,
    USER_NOTIFICATIONS_EMAIL_ENABLED=True,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    ONESIGNAL_SITE_ORIGIN="https://tennisfan.ru",
)
class TelegramEmailAtomicityTests(TestCase):
    """Почтовый источник сохраняет ЛК и push до коммита без повторного события."""

    def setUp(self) -> None:
        """Создать отдельного получателя с явным согласием на push."""
        seed_event_policies()
        self.user = make_user(email="email-atomicity@test.local")
        NotificationPreference.objects.create(user=self.user, push_enabled=True)
        mail.outbox.clear()

    def _send(self) -> bool:
        """Вызвать настоящий адаптер с почтовым событием и приватным текстом."""
        return _send_notification_email_to_user(
            self.user,
            "<b>Подписка заканчивается</b>\nПриватные подробности письма",
            event_key="subscription.expiring",
        )

    def _assert_saved_event(self) -> None:
        """Проверить ровно одну запись ЛК и одно связанное push-задание."""
        note = Notification.objects.get(
            user=self.user, event_key="subscription.expiring"
        )
        delivery = Delivery.objects.get(
            user=self.user, event_key="subscription.expiring", channel="push"
        )
        self.assertEqual(delivery.notification_id, note.pk)
        self.assertNotIn("Приватные подробности", note.message)
        self.assertNotIn("Приватные подробности", delivery.body)

    def test_email_event_is_saved_before_smtp_and_not_duplicated_after_commit(
        self,
    ) -> None:
        """SMTP после коммита не создаёт вторую запись ЛК или второе push-задание."""
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            with transaction.atomic():
                self.assertFalse(self._send())
                self._assert_saved_event()
                self.assertFalse(mail.outbox)
            self.assertFalse(mail.outbox)

        self.assertEqual(len(callbacks), 1)
        self.assertEqual(len(mail.outbox), 1)
        self._assert_saved_event()

    def test_rollback_removes_email_event_push_and_smtp_callback(self) -> None:
        """Поздний отказ действия откатывает оба канала и отменяет отправку письма."""
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            with self.assertRaisesRegex(RuntimeError, "later action failed"):
                with transaction.atomic():
                    self._send()
                    self._assert_saved_event()
                    raise RuntimeError("later action failed")

        self.assertFalse(callbacks)
        self.assertFalse(Notification.objects.filter(user=self.user).exists())
        self.assertFalse(Delivery.objects.filter(user=self.user).exists())
        self.assertFalse(mail.outbox)

    @override_settings(USER_NOTIFICATIONS_EMAIL_ENABLED=False)
    def test_old_email_flag_off_keeps_transactional_inbox_and_push(self) -> None:
        """Исторический запрет email не откладывает независимые ЛК и push."""
        with self.captureOnCommitCallbacks(execute=True):
            with transaction.atomic():
                self.assertFalse(self._send())
                self._assert_saved_event()
                self.assertFalse(mail.outbox)

        self._assert_saved_event()
        self.assertFalse(mail.outbox)

    def test_smtp_failure_keeps_saved_inbox_and_push(self) -> None:
        """Отказ транспорта после коммита не удаляет сохранённые локальные каналы."""
        with (
            patch(
                "apps.telegram_bot.notifications.send_mail",
                side_effect=RuntimeError("smtp failed"),
            ) as send_mail,
            self.captureOnCommitCallbacks(execute=True),
        ):
            self._send()
            self._assert_saved_event()
            send_mail.assert_not_called()

        send_mail.assert_called_once()
        self._assert_saved_event()
        self.assertFalse(mail.outbox)

    def test_capture_failure_propagates_before_smtp_is_scheduled(self) -> None:
        """Отказ обязательного push откатывает запись ЛК и не планирует SMTP."""
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            with patch(
                "apps.notifications.services._queue_push",
                side_effect=RuntimeError("push queue failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "push queue failed"):
                    with transaction.atomic():
                        self._send()

        self.assertFalse(callbacks)
        self.assertFalse(Notification.objects.filter(user=self.user).exists())
        self.assertFalse(Delivery.objects.filter(user=self.user).exists())
        self.assertFalse(mail.outbox)
