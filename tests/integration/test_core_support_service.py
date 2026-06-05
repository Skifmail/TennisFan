"""Интеграционные тесты: сервис диалогов поддержки."""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.core.models import SupportMessage, SupportThread
from apps.core.support_service import (
    SupportAuthor,
    can_edit_support_message,
    create_admin_reply,
    create_user_message,
    get_unread_count_for_admin,
    get_unread_count_for_user,
    mark_thread_read_by_admin,
    mark_thread_read_by_user,
)
from tests.support.factories import make_user


class SupportServiceFlowTestCase(TestCase):
    """Создание сообщений и счётчики непрочитанных."""

    def setUp(self) -> None:
        self.user = make_user(email="support-svc@test.local")
        self.author = SupportAuthor(
            user_id=self.user.pk,
            guest_email="",
            guest_name="",
            guest_session_key="",
        )

    def test_user_message_increments_admin_unread(self) -> None:
        message = create_user_message(author=self.author, text="Нужна помощь")

        thread = SupportThread.objects.get(user=self.user)
        self.assertEqual(thread.admin_unread_count, 1)
        self.assertEqual(message.text, "Нужна помощь")
        self.assertFalse(message.is_from_admin)

    def test_admin_reply_resets_admin_unread_and_increments_user_unread(self) -> None:
        create_user_message(author=self.author, text="Вопрос")
        thread = SupportThread.objects.get(user=self.user)

        reply = create_admin_reply(thread=thread, text="Ответ")

        thread.refresh_from_db()
        self.assertEqual(thread.admin_unread_count, 0)
        self.assertEqual(thread.user_unread_count, 1)
        self.assertTrue(reply.is_from_admin)

    def test_mark_read_counters(self) -> None:
        create_user_message(author=self.author, text="Вопрос")
        thread = SupportThread.objects.get(user=self.user)
        create_admin_reply(thread=thread, text="Ответ")

        mark_thread_read_by_user(thread)
        thread.refresh_from_db()
        self.assertEqual(thread.user_unread_count, 0)
        self.assertEqual(get_unread_count_for_user(thread=thread), 0)

        create_user_message(author=self.author, text="Ещё вопрос")
        thread.refresh_from_db()
        mark_thread_read_by_admin(thread)
        thread.refresh_from_db()
        self.assertEqual(thread.admin_unread_count, 0)
        self.assertGreaterEqual(get_unread_count_for_admin(), 0)

    def test_guest_reuses_thread_by_session_key(self) -> None:
        guest_author = SupportAuthor(
            user_id=None,
            guest_email="guest@test.local",
            guest_name="Гость",
            guest_session_key="sess-abc-123",
        )
        create_user_message(author=guest_author, text="Первое")
        create_user_message(author=guest_author, text="Второе")

        threads = SupportThread.objects.filter(
            guest_session_key="sess-abc-123",
            user__isnull=True,
        )
        self.assertEqual(threads.count(), 1)
        self.assertEqual(threads.first().messages.count(), 2)


class SupportMessageEditWindowTestCase(TestCase):
    """Окно редактирования сообщения поддержки."""

    def test_recent_message_editable(self) -> None:
        user = make_user(email="support-edit@test.local")
        message = create_user_message(
            author=SupportAuthor(
                user_id=user.pk,
                guest_email="",
                guest_name="",
                guest_session_key="",
            ),
            text="Текст",
        )

        self.assertTrue(can_edit_support_message(message))

    def test_old_message_not_editable(self) -> None:
        user = make_user(email="support-edit-old@test.local")
        message = create_user_message(
            author=SupportAuthor(
                user_id=user.pk,
                guest_email="",
                guest_name="",
                guest_session_key="",
            ),
            text="Старое",
        )
        old_time = timezone.now() - timedelta(minutes=20)
        SupportMessage.objects.filter(pk=message.pk).update(created_at=old_time)
        message.refresh_from_db()

        self.assertFalse(can_edit_support_message(message))
