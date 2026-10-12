"""Тесты группировки и отрисовки ленты уведомлений."""

from django.test import Client, TestCase
from django.urls import reverse

from apps.users.models import Notification
from apps.users.notification_inbox import (
    classify_notification,
    safe_notification_url,
)
from tests.support.factories import make_user


class ClassifyNotificationTestCase(TestCase):
    """Группа определяется по тексту, более узкие темы идут раньше общих."""

    def test_sparring_wins_over_match_word(self) -> None:
        message = "Ваш отклик на заявку на спарринг принят. Матч создан."
        self.assertEqual(classify_notification(message), "sparring")

    def test_tournament_club_and_fallback(self) -> None:
        self.assertEqual(classify_notification("Новый турнир: Кубок"), "tournament")
        self.assertEqual(classify_notification("Заявка в клуб «Север»"), "club")
        self.assertEqual(classify_notification("Оцените соперника"), "general")
        self.assertEqual(
            classify_notification("Дедлайн матча истёк. Вам присуждена тех. победа."),
            "match",
        )


class SafeNotificationUrlTestCase(TestCase):
    """В кнопку попадают только обычные ссылки."""

    def test_allows_path_and_http(self) -> None:
        self.assertEqual(safe_notification_url("/tournaments/1/"), "/tournaments/1/")
        self.assertEqual(
            safe_notification_url("https://tennisfan.ru/tournaments/1/"),
            "https://tennisfan.ru/tournaments/1/",
        )

    def test_drops_script_and_protocol_relative(self) -> None:
        self.assertEqual(safe_notification_url("javascript:alert(1)"), "")
        self.assertEqual(safe_notification_url("//evil.example/phish"), "")


class NotificationsPageTestCase(TestCase):
    """Страница помечает прочитанным и показывает новую запись в этой отрисовке."""

    def setUp(self) -> None:
        self.client = Client()
        self.user = make_user(email="inbox-page@test.local")
        self.client.force_login(self.user)

    def test_unread_stays_visible_as_new_and_is_marked_read(self) -> None:
        note = Notification.objects.create(
            user=self.user,
            message="Новый турнир: «Кубок парка». Приглашаем принять участие.",
            url="/tournaments/12/",
            is_read=False,
        )

        response = self.client.get(reverse("notifications"), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "inbox-card--new")
        self.assertContains(response, "Турнир")
        self.assertContains(response, "Перейти")
        self.assertContains(response, note.message)
        note.refresh_from_db()
        self.assertTrue(note.is_read)

    def test_empty_state(self) -> None:
        response = self.client.get(reverse("notifications"), follow=True)

        self.assertContains(response, "Пока тихо")
        self.assertNotContains(response, "inbox-card")
