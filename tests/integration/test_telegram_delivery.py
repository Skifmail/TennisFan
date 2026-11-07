"""Сохраняемая доставка Telegram, повторы и честные статусы рассылок."""

from __future__ import annotations

import os
import uuid
from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import requests
from django.core import mail
from django.core.management import call_command
from django.db import transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.clubs.notifications import _send_club_telegram
from apps.core.models import UserTelegramLink
from apps.notifications.models import Delivery
from apps.notifications.worker import process_deliveries
from apps.telegram_bot import services
from apps.telegram_bot.models import TelegramBroadcast, TelegramDeliveryState
from apps.telegram_bot.notifications import send_to_user_by_user
from tests.support.factories import make_user


@override_settings(
    TELEGRAM_USER_BOT_TOKEN="test-delivery-token",
    NOTIFICATION_CENTER_ENABLED=False,
    USER_NOTIFICATIONS_EMAIL_ENABLED=True,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class TelegramDeliveryTests(TestCase):
    """Внешняя сеть замокана; проверяются реальные записи и worker."""

    def setUp(self) -> None:
        self.user = make_user(email="telegram-delivery@test.local")
        self.link = UserTelegramLink.objects.create(
            user=self.user, user_bot_chat_id=10001
        )
        environment = patch.dict(os.environ, {"TELEGRAM_ENABLED": "true"})
        environment.start()
        self.addCleanup(environment.stop)
        transport = patch("apps.telegram_bot.services.requests.post")
        self.post: MagicMock = transport.start()
        self.addCleanup(transport.stop)
        self.post.return_value = self._response(
            {"ok": True, "result": {"message_id": 54321}}
        )

    def _response(self, payload: dict[str, Any], status: int = 200) -> MagicMock:
        response = MagicMock(spec=requests.Response)
        response.status_code = status
        response.ok = status < 400
        response.json.return_value = payload
        response.text = "Ответ Telegram"
        response.headers = {}
        return response

    def _enqueue(self, text: str = "Уведомление") -> Delivery:
        from apps.telegram_bot.delivery import enqueue_message

        item = enqueue_message(self.user, text)
        assert item is not None
        return item

    def _process(self, limit: int = 100) -> int:
        from apps.telegram_bot.delivery import process_telegram_deliveries

        return process_telegram_deliveries(limit=limit)

    def test_notification_is_persisted_before_network_and_preserves_keyboard(
        self,
    ) -> None:
        keyboard = {
            "inline_keyboard": [[{"text": "Матчи", "callback_data": "menu_my_matches"}]]
        }
        self.assertTrue(
            send_to_user_by_user(self.user, "<b>Матч</b>", keyboard, skip_email=True)
        )
        self.post.assert_not_called()
        item = Delivery.objects.get(channel="telegram")
        self.assertEqual(item.status, "queued")
        payload = item.telegram_payload
        assert payload is not None
        self.assertEqual(payload["chat_id"], 10001)
        self.assertEqual(payload["reply_markup"], keyboard)
        self.assertEqual(len(mail.outbox), 0)

    def test_telegram_only_club_notification_never_counts_email_as_success(
        self,
    ) -> None:
        self.link.delete()
        self.assertFalse(_send_club_telegram(self.user, "Клубное уведомление"))
        self.assertEqual(len(mail.outbox), 0)

    def test_send_message_requires_ok_true_even_with_http_200(self) -> None:
        self.post.return_value = self._response({"ok": False, "error_code": 400})
        self.assertEqual(services.send_message(10001, "Тест"), (None, False))

    def test_worker_is_independent_of_push_email_feature_flag(self) -> None:
        item = self._enqueue()
        call_command("process_notifications")
        item.refresh_from_db()
        self.assertEqual(item.status, "accepted")
        self.assertEqual(item.provider_id, "54321")
        self.post.assert_called_once()

    def test_push_email_worker_never_treats_telegram_as_email(self) -> None:
        item = self._enqueue()
        with override_settings(NOTIFICATION_CENTER_ENABLED=True):
            self.assertEqual(process_deliveries(limit=10), 0)
        item.refresh_from_db()
        self.assertEqual(item.status, "queued")
        self.assertEqual(len(mail.outbox), 0)
        self.post.assert_not_called()

    def test_rate_limit_honours_retry_after_and_pauses_other_messages(self) -> None:
        first = self._enqueue("Первое")
        second = self._enqueue("Второе")
        self.post.return_value = self._response(
            {"ok": False, "error_code": 429, "parameters": {"retry_after": 125}}, 429
        )
        before = timezone.now()
        self._process()
        first.refresh_from_db()
        self.assertEqual(first.status, "queued")
        self.assertEqual(first.error_code, "telegram_rate_limited")
        self.assertGreaterEqual(first.available_at, before + timedelta(seconds=125))
        self.assertEqual(first.attempts, 1)
        self.post.assert_called_once()
        self.assertEqual(self._process(), 0)
        second.refresh_from_db()
        self.assertEqual(second.attempts, 0)

    def test_temporary_rejection_is_retried_with_a_limit(self) -> None:
        item = self._enqueue()
        self.post.return_value = self._response({"ok": False, "error_code": 503}, 503)
        for _ in range(5):
            Delivery.objects.filter(pk=item.pk).update(available_at=timezone.now())
            self._process(limit=1)
        item.refresh_from_db()
        self.assertEqual(item.status, "failed")
        self.assertEqual(item.attempts, 5)
        self.assertEqual(item.error_code, "telegram_server_error")

    def test_blocked_bot_is_not_retried(self) -> None:
        item = self._enqueue()
        self.post.return_value = self._response(
            {
                "ok": False,
                "error_code": 403,
                "description": "Forbidden: bot was blocked by the user",
            },
            403,
        )
        self._process()
        item.refresh_from_db()
        self.assertEqual(item.status, "failed")
        self.assertEqual(item.error_code, "telegram_forbidden")
        self.assertEqual(self._process(), 0)

    def test_read_timeout_has_uncertain_outcome_without_blind_repeat(self) -> None:
        item = self._enqueue()
        self.post.side_effect = requests.ReadTimeout(
            "URL с секретом не должен логироваться"
        )
        self._process()
        item.refresh_from_db()
        self.assertEqual(item.status, "uncertain")
        self.assertEqual(item.error_code, "telegram_transport_uncertain")
        self.assertEqual(self._process(), 0)

    def test_interrupted_worker_is_closed_without_duplicate_send(self) -> None:
        item = self._enqueue()
        Delivery.objects.filter(pk=item.pk).update(
            status="processing",
            attempts=1,
            available_at=timezone.now() - timedelta(minutes=10),
        )
        self._process()
        item.refresh_from_db()
        self.assertEqual(item.status, "uncertain")
        self.assertEqual(item.error_code, "telegram_worker_interrupted")
        self.post.assert_not_called()

    def test_expired_and_disconnected_recipients_are_skipped(self) -> None:
        expired = self._enqueue("Устарело")
        disconnected = self._enqueue("Отключено")
        Delivery.objects.filter(pk=expired.pk).update(expires_at=timezone.now())
        self.link.user_bot_chat_id = None
        self.link.save(update_fields=["user_bot_chat_id"])
        self._process()
        for item in (expired, disconnected):
            item.refresh_from_db()
            self.assertEqual(item.status, "skipped")
        self.post.assert_not_called()

    def test_rebinding_never_sends_old_context_to_new_chat(self) -> None:
        item = self._enqueue()
        self.link.user_bot_chat_id = 20002
        self.link.save(update_fields=["user_bot_chat_id"])
        self._process()
        item.refresh_from_db()
        self.assertEqual(item.status, "skipped")
        self.post.assert_not_called()

    def test_stale_claim_cannot_send_after_terminal_status(self) -> None:
        """Копия задания в памяти не обходит уже сохранённый финальный статус."""
        from apps.telegram_bot.delivery import _claim, _process

        item = self._enqueue()
        claimed = _claim()
        assert claimed is not None
        Delivery.objects.filter(pk=item.pk).update(status="uncertain")
        _process(claimed)
        self.post.assert_not_called()
        item.refresh_from_db()
        self.assertEqual(item.status, "uncertain")

    def test_stale_claim_reads_current_expiration_before_send(self) -> None:
        """Срок действия читается из БД после захвата задания, а не из старой копии."""
        from apps.telegram_bot.delivery import _claim, _process

        item = self._enqueue()
        claimed = _claim()
        assert claimed is not None
        Delivery.objects.filter(pk=item.pk).update(expires_at=timezone.now())
        _process(claimed)
        self.post.assert_not_called()
        item.refresh_from_db()
        self.assertEqual(item.status, "skipped")

    def test_claim_cannot_send_after_losing_global_sender_lease(self) -> None:
        """Свежее задание само по себе не разрешает отправку после смены владельца sender."""
        from apps.telegram_bot.delivery import _claim, _process

        item = self._enqueue()
        claimed = _claim()
        assert claimed is not None
        other_key = uuid.uuid4()
        TelegramDeliveryState.objects.filter(bot="user").update(lease_key=other_key)
        _process(claimed)
        self.post.assert_not_called()
        item.refresh_from_db()
        self.assertEqual(item.status, "uncertain")
        self.assertEqual(item.error_code, "telegram_sender_lease_lost")
        self.assertEqual(
            TelegramDeliveryState.objects.get(bot="user").lease_key, other_key
        )

    def test_old_worker_does_not_release_a_new_attempt_of_the_same_message(
        self,
    ) -> None:
        """Повторная попытка имеет свою границу аренды даже при том же ключе Delivery."""
        item = self._enqueue()
        renewed_until = timezone.now() + timedelta(minutes=20)

        def replace_claim(claimed: Delivery) -> None:
            """Имитировать новую попытку после долгой остановки старого процесса."""
            Delivery.objects.filter(pk=claimed.pk).update(
                attempts=claimed.attempts + 1, available_at=renewed_until
            )
            TelegramDeliveryState.objects.filter(bot="user").update(
                lease_until=renewed_until
            )

        with patch("apps.telegram_bot.delivery._process", side_effect=replace_claim):
            self.assertEqual(self._process(limit=1), 1)
        state = TelegramDeliveryState.objects.get(bot="user")
        self.assertEqual(state.lease_key, item.key)
        self.assertEqual(state.lease_until, renewed_until)

    def test_disabling_api_keeps_pending_deliveries_for_later(self) -> None:
        item = self._enqueue()
        with patch.dict(os.environ, {"TELEGRAM_ENABLED": "false"}):
            self.assertEqual(self._process(), 0)
        item.refresh_from_db()
        self.assertEqual(item.status, "queued")
        self.assertEqual(item.attempts, 0)

    def test_rollback_discards_delivery_together_with_business_action(self) -> None:
        with self.assertRaises(ValueError):
            with transaction.atomic():
                self._enqueue()
                raise ValueError("Отмена действия")
        self.assertFalse(Delivery.objects.filter(channel="telegram").exists())
        self.post.assert_not_called()

    def test_broadcast_does_not_claim_sent_when_no_recipients(self) -> None:
        from apps.telegram_bot.delivery import enqueue_broadcast

        self.link.delete()
        broadcast = TelegramBroadcast.objects.create(text="Рассылка")
        self.assertEqual(enqueue_broadcast(broadcast.pk), 0)
        broadcast.refresh_from_db()
        self.assertIsNone(broadcast.sent_at)
        self.assertEqual(broadcast.status, "empty")

    def test_broadcast_partial_failure_is_visible_and_not_sent(self) -> None:
        from apps.telegram_bot.delivery import enqueue_broadcast

        other = make_user(email="telegram-other@test.local")
        UserTelegramLink.objects.create(user=other, user_bot_chat_id=20002)
        broadcast = TelegramBroadcast.objects.create(text="Рассылка")
        self.assertEqual(enqueue_broadcast(broadcast.pk), 2)
        self.assertEqual(enqueue_broadcast(broadcast.pk), 0)
        self.post.side_effect = [
            self._response({"ok": True, "result": {"message_id": 1}}),
            self._response({"ok": False, "error_code": 403}, 403),
        ]
        self._process()
        broadcast.refresh_from_db()
        self.assertIsNone(broadcast.sent_at)
        self.assertIsNotNone(broadcast.finished_at)
        self.assertEqual(broadcast.status, "partial")
        self.assertEqual(broadcast.deliveries.filter(status="accepted").count(), 1)

    def test_successful_broadcast_sets_sent_only_after_provider_accepts_all(
        self,
    ) -> None:
        from apps.telegram_bot.delivery import enqueue_broadcast

        broadcast = TelegramBroadcast.objects.create(text="Рассылка")
        enqueue_broadcast(broadcast.pk)
        broadcast.refresh_from_db()
        self.assertIsNone(broadcast.sent_at)
        self._process()
        broadcast.refresh_from_db()
        self.assertEqual(broadcast.status, "sent")
        self.assertIsNotNone(broadcast.sent_at)

    def test_photo_is_read_from_storage_not_a_relative_url(self) -> None:
        from io import BytesIO

        from apps.telegram_bot.delivery import enqueue_message

        item = enqueue_message(
            self.user, "Подпись", photo_name="telegram_broadcasts/photo.jpg"
        )
        assert item is not None
        with patch(
            "apps.telegram_bot.delivery.default_storage.open",
            return_value=BytesIO(b"photo"),
        ) as storage:
            self._process()
        storage.assert_called_once_with("telegram_broadcasts/photo.jpg", "rb")
        self.assertIn("sendPhoto", self.post.call_args.args[0])
        item.refresh_from_db()
        self.assertEqual(item.status, "accepted")

    def test_message_key_is_idempotent_after_delivery(self) -> None:
        """Повтор бизнес-события с тем же ключом не отправляется второй раз."""
        from apps.telegram_bot.delivery import enqueue_message

        key = uuid.uuid4()
        first = enqueue_message(self.user, "Событие", key=key)
        self._process()
        repeated = enqueue_message(self.user, "Событие", key=key)
        assert first is not None and repeated is not None
        self.assertEqual(first.pk, repeated.pk)
        self.assertEqual(self._process(), 0)
        self.post.assert_called_once()

    def test_another_worker_cannot_send_while_sender_is_leased(self) -> None:
        """Даже другое задание ждёт завершения общей аренды отправителя."""
        first = self._enqueue("Первое")
        second = self._enqueue("Второе")
        concurrent_runs: list[int] = []

        def transport(*args: Any, **kwargs: Any) -> MagicMock:
            concurrent_runs.append(self._process(limit=1))
            return self._response({"ok": True, "result": {"message_id": 123}})

        self.post.side_effect = transport
        self.assertEqual(self._process(limit=1), 1)
        self.assertEqual(concurrent_runs, [0])
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, "accepted")
        self.assertEqual(second.attempts, 0)
        self.assertIsNone(TelegramDeliveryState.objects.get(bot="user").lease_key)

    def test_unexplained_gateway_error_is_not_a_confirmed_rejection(self) -> None:
        """Ответ прокси без ok=false не доказывает, что Telegram не принял сообщение."""
        item = self._enqueue()
        self.post.return_value = self._response({}, 502)
        self._process()
        item.refresh_from_db()
        self.assertEqual(item.status, "uncertain")
        self.assertEqual(self._process(), 0)

    def test_interrupted_broadcast_gets_a_terminal_honest_status(self) -> None:
        """Восстановление worker завершает рассылку, даже когда нет новых отправок."""
        from apps.telegram_bot.delivery import enqueue_broadcast

        broadcast = TelegramBroadcast.objects.create(text="Рассылка")
        enqueue_broadcast(broadcast.pk)
        broadcast.deliveries.update(
            status="processing",
            attempts=1,
            available_at=timezone.now() - timedelta(minutes=10),
        )
        self._process()
        broadcast.refresh_from_db()
        self.assertEqual(broadcast.status, "failed")
        self.assertIsNone(broadcast.sent_at)
        self.assertIsNotNone(broadcast.finished_at)
        self.assertEqual(broadcast.deliveries.get().status, "uncertain")
        self.post.assert_not_called()

    def test_prepared_broadcasts_do_not_starve_new_preparation(self) -> None:
        """Готовые рассылки не расходуют бюджет подготовки новых получателей."""
        from apps.telegram_bot.delivery import enqueue_broadcast, request_broadcast

        pending = TelegramBroadcast.objects.create(text="Новая")
        request_broadcast(pending.pk)
        for _ in range(3):
            prepared = TelegramBroadcast.objects.create(text="Подготовленная")
            enqueue_broadcast(prepared.pk)
        self._process(limit=1)
        self.assertEqual(pending.deliveries.count(), 1)

    def test_broadcast_preparation_has_one_total_budget(self) -> None:
        """Несколько рассылок не умножают лимит подготовки за запуск."""
        from apps.telegram_bot.delivery import request_broadcast

        for _ in range(3):
            broadcast = TelegramBroadcast.objects.create(text="Рассылка")
            request_broadcast(broadcast.pk)
        self._process(limit=2)
        self.assertEqual(
            Delivery.objects.filter(telegram_broadcast__isnull=False).count(), 2
        )
