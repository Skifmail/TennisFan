"""Публичная лента скрывает выбранную тренировку, сохраняя закрытый журнал событий."""

from __future__ import annotations

from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.models import PlatformActivityEvent
from tests.support.factories import make_player, make_user


@override_settings(SECURE_SSL_REDIRECT=False)
class HomeTrainingActivityPrivacyTests(TestCase):
    """Скрывать детали записи на тренировку для всех посетителей главной."""

    def setUp(self) -> None:
        """Создать старое событие с подробным текстом и ссылкой без реальной заявки."""
        self.player = make_player(email_suffix="home-training-privacy")
        self.staff = make_user(email="home-training-staff@test.local", is_staff=True)
        self.description = "Записался на тренировку «Закрытое занятие с тренером»"
        self.training_url = "/training/private-enrollment-target/"
        self.event = PlatformActivityEvent.objects.create(
            event_type=PlatformActivityEvent.EventType.TRAINING_ENROLLED,
            actor=self.player.user,
            actor_name=self.player.user.get_display_name(),
            description=self.description,
            target_url=self.training_url,
        )

    def test_home_hides_training_details_for_guests_players_and_staff(self) -> None:
        """Главная не раскрывает название и адрес даже авторизованным администраторам."""
        for user in (None, self.player.user, self.staff):
            with self.subTest(user_id=user.pk if user is not None else None):
                self.client.logout()
                if user is not None:
                    self.client.force_login(user)
                response = self.client.get(reverse("home"))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Записался на тренировку")
                self.assertContains(response, self.event.actor_name)
                self.assertNotContains(response, "Закрытое занятие с тренером")
                self.assertNotContains(response, self.training_url)
        self.event.refresh_from_db()
        self.assertEqual(self.event.description, self.description)
        self.assertEqual(self.event.target_url, self.training_url)

    def test_enrollment_row_has_plain_description_without_link(self) -> None:
        """Текст записи не содержит ссылки, даже если она ведёт в общий каталог."""
        for url in (self.training_url, reverse("training_list"), "/admin/training/"):
            with self.subTest(url=url):
                self.event.target_url = url
                html = render_to_string(
                    "core/_home_activity_row.html", {"event": self.event, "index": 0}
                )
                description = html.split('<span class="home-activity__desc">', 1)[1]
                description = description.split("</span>", 1)[0]
                self.assertEqual(description.strip(), "Записался на тренировку")
                self.assertNotIn("<a", description)
                self.assertNotIn(self.description, html)
                self.assertEqual(self.event.get_public_target_url(), "")

    def test_public_description_is_generic_even_for_missing_legacy_text(self) -> None:
        """События записи с пустым или произвольным старым текстом имеют общую подпись."""
        for description in ("", self.description, "Другое старое описание"):
            with self.subTest(description=description):
                self.event.description = description
                self.assertEqual(
                    self.event.get_public_feed_description(), "Записался на тренировку"
                )

    def test_staff_dashboard_keeps_original_training_details(self) -> None:
        """Закрытая панель сохраняет исходные сведения; публичная маскировка их не стирает."""
        self.client.force_login(self.staff)
        response = self.client.get(reverse("platform_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.description)
        self.assertContains(response, f'href="{self.training_url}"')

    def test_published_training_remains_public_and_linked(self) -> None:
        """Скрывается выбор игрока, но не отдельная публикация тренировки тренером."""
        event = PlatformActivityEvent.objects.create(
            event_type=PlatformActivityEvent.EventType.TRAINING_PUBLISHED,
            actor_name="Тренер",
            description="Опубликовал тренировку «Открытое занятие»",
            target_url="/training/public-training/",
        )
        response = self.client.get(reverse("home"))
        self.assertContains(response, event.description)
        self.assertContains(response, f'href="{event.target_url}"')
        self.assertEqual(
            event.get_public_feed_description(), event.get_feed_description()
        )
        self.assertEqual(event.get_public_target_url(), event.target_url)
