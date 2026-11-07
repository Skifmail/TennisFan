"""Ответ поддержки дублируется в Telegram через общую очередь доставки."""

from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.models import SupportMessage, SupportThread, UserTelegramLink
from apps.core.support_notifications import (
    notify_user_support_reply_in_bot,
    send_user_support_reply_async,
)
from apps.notifications.models import Delivery
from tests.support.factories import make_player


@override_settings(
    TELEGRAM_BOT_SITE_BASE_URL="https://tennisfan.ru",
    NOTIFICATION_CENTER_ENABLED=False,
)
class TelegramSupportReplyTests(TestCase):
    """Telegram-очередь получает ответ поддержки с кнопкой на диалог."""

    def setUp(self) -> None:
        self.player = make_player(email_suffix="tg-support-reply")
        UserTelegramLink.objects.create(user=self.player.user, user_bot_chat_id=555123)
        self.thread = SupportThread.objects.create(
            user=self.player.user, last_message_at=timezone.now()
        )
        self.message = SupportMessage.objects.create(
            thread=self.thread,
            user=self.player.user,
            text="Ответ администратора <важно> & 'тест'",
            is_from_admin=True,
        )

    def test_notify_support_reply_creates_telegram_delivery_with_button(self) -> None:
        """Сообщение ставится в общую очередь с event_key и кнопкой."""
        self.assertTrue(notify_user_support_reply_in_bot(self.message))
        delivery = Delivery.objects.get(channel="telegram", event_key="support.reply")
        payload = delivery.telegram_payload
        self.assertEqual(payload["chat_id"], 555123)
        self.assertIn("Ответ поддержки", payload["text"])
        self.assertIn("Обращение №" + str(self.thread.pk), payload["text"])
        self.assertIn("&lt;важно&gt;", payload["text"])
        button = payload["reply_markup"]["inline_keyboard"][0][0]
        self.assertEqual(button["text"], "➡️ Открыть диалог")
        self.assertEqual(
            button["url"],
            "https://tennisfan.ru" + reverse("support_feedback"),
        )

    def test_guest_thread_does_not_create_telegram_delivery(self) -> None:
        """Гостевой диалог без пользователя не ставит задание в Telegram."""
        guest_thread = SupportThread.objects.create(
            guest_email="guest@support.test", last_message_at=timezone.now()
        )
        guest_message = SupportMessage.objects.create(
            thread=guest_thread, text="Ответ", is_from_admin=True
        )
        self.assertFalse(notify_user_support_reply_in_bot(guest_message))
        self.assertFalse(Delivery.objects.filter(event_key="support.reply").exists())

    def test_send_user_support_reply_async_enqueues_telegram_delivery(self) -> None:
        """Асинхронный ответ сохраняет Telegram-задание до отправки email."""
        with patch("apps.core.support_notifications.threading.Thread") as thread_cls:
            send_user_support_reply_async(self.message)
        thread_cls.return_value.start.assert_called_once()
        self.assertTrue(
            Delivery.objects.filter(
                channel="telegram", event_key="support.reply"
            ).exists()
        )
