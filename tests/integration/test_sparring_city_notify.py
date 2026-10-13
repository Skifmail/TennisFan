"""Письмо и уведомление в ЛК игрокам города о новой заявке на спарринг."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.sparring.city_notify import notify_published_sparring
from apps.sparring.doubles_services import create_doubles_request
from apps.sparring.models import SparringRequest
from apps.users.models import Notification
from tests.support.factories import make_player, make_subscription


def _place(player, city: str) -> None:
    """Записать населённый пункт игрока."""
    player.city = city
    player.save(update_fields=["city"])


@override_settings(
    USER_NOTIFICATIONS_EMAIL_ENABLED=True,
    EMAIL_BACKEND="apps.core.mail.LoggingEmailBackend",
    EMAIL_BACKEND_INNER="django.core.mail.backends.locmem.EmailBackend",
)
class CitySparringNotifyTests(TestCase):
    """Рассылка только игрокам того же города, со ссылкой и письмом."""

    def setUp(self) -> None:
        self.author = make_player(
            email_suffix="city-author",
            first_name="Иван",
            last_name="Автор",
        )
        _place(self.author, "Сочи")
        self.neighbor = make_player(
            email_suffix="city-neighbor",
            first_name="Пётр",
            last_name="Сосед",
        )
        _place(self.neighbor, "Сочи")
        self.other = make_player(
            email_suffix="city-kazan",
            first_name="Анна",
            last_name="Казань",
        )
        _place(self.other, "Казань")
        self.sparring = SparringRequest.objects.create(
            player=self.author,
            city="Сочи",
            description="Хочу играть вечером",
        )

    def test_same_city_gets_cabinet_notice_and_email(self) -> None:
        """Сосед по городу получает текст, ссылку, письмо и кнопку в ЛК."""
        lk_count, email_count = notify_published_sparring(
            kind="singles", pk=self.sparring.pk
        )

        self.assertEqual(lk_count, 1)
        self.assertEqual(email_count, 1)
        notice = Notification.objects.get(user=self.neighbor.user)
        self.assertIn("Иван Автор", notice.message)
        self.assertIn("ищет партнёров для спарринга", notice.message)
        self.assertIn(f"#sparring-{self.sparring.pk}", notice.url)
        self.assertFalse(Notification.objects.filter(user=self.author.user).exists())
        self.assertFalse(Notification.objects.filter(user=self.other.user).exists())
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, [self.neighbor.user.email])
        self.assertIn("Иван Автор", message.subject)
        html = message.alternatives[0][0]
        self.assertIn("Открыть спарринг", html)
        self.assertIn("type=singles", html)
        self.assertIn(f"#sparring-{self.sparring.pk}", html)

        self.client.force_login(self.neighbor.user)
        page = self.client.get(reverse("notifications"), follow=True)
        self.assertContains(page, "Открыть спарринг")
        self.assertContains(page, f"#sparring-{self.sparring.pk}")

    def test_prefixed_city_matches_catalog_name(self) -> None:
        """«г. Москва» в профиле совпадает с заявкой «Москва»."""
        _place(self.neighbor, "г. Москва")
        self.sparring.city = "Москва"
        self.sparring.save(update_fields=["city"])

        lk_count, _email_count = notify_published_sparring(
            kind="singles", pk=self.sparring.pk
        )

        self.assertEqual(lk_count, 1)
        self.assertTrue(Notification.objects.filter(user=self.neighbor.user).exists())

    def test_skips_author_bye_inactive_and_closed(self) -> None:
        """Bye, выключенный аккаунт и закрытая заявка не получают рассылку."""
        bye = make_player(email_suffix="city-bye")
        _place(bye, "Сочи")
        bye.is_bye = True
        bye.save(update_fields=["is_bye"])
        quiet = make_player(email_suffix="city-off")
        _place(quiet, "Сочи")
        quiet.user.is_active = False
        quiet.user.save(update_fields=["is_active"])

        self.sparring.status = SparringRequest.Status.CLOSED
        self.sparring.save(update_fields=["status"])
        lk_count, email_count = notify_published_sparring(
            kind="singles", pk=self.sparring.pk
        )

        self.assertEqual((lk_count, email_count), (0, 0))
        self.assertFalse(Notification.objects.exists())

    def test_doubles_request_links_to_detail(self) -> None:
        """Парная заявка ведёт на её страницу, а не на общий список."""
        doubles = create_doubles_request(
            created_by=self.author,
            city="Сочи",
            description="Ищем пару",
        )

        notify_published_sparring(kind="doubles", pk=doubles.pk)

        notice = Notification.objects.get(user=self.neighbor.user)
        self.assertEqual(notice.url, reverse("doubles_detail", args=[doubles.pk]))
        self.assertIn("парный спарринг", notice.message)
        self.assertIn(notice.url, mail.outbox[0].body)


@override_settings(USER_NOTIFICATIONS_EMAIL_ENABLED=True)
class SparringCreateSchedulesCityNotifyTests(TestCase):
    """Публикация одиночной заявки ставит рассылку после сохранения."""

    def setUp(self) -> None:
        self.author_user_player = make_player(
            email_suffix="sched-author",
            first_name="Иван",
            last_name="Заявка",
        )
        user = self.author_user_player.user
        user.phone = "+79990001144"
        user.save(update_fields=["phone"])
        self.author_user_player.city = "Сочи"
        self.author_user_player.is_verified = True
        self.author_user_player.birth_date = date(1990, 1, 1)
        self.author_user_player.save(
            update_fields=["city", "is_verified", "birth_date"]
        )
        make_subscription(
            user,
            tier_name="city-notify-silver",
            tier_kwargs={"has_sparring": True},
        )
        self.neighbor = make_player(email_suffix="sched-neighbor")
        _place(self.neighbor, "Сочи")
        self.client.force_login(user)

    def test_create_notifies_same_city_after_commit(self) -> None:
        """Форма создаёт заявку и после коммита пишет соседу в ЛК."""

        class _InlineThread:
            def __init__(self, target=None, kwargs=None, **_ignored: object) -> None:
                self._target = target
                self._kwargs = kwargs or {}

            def start(self) -> None:
                if self._target is not None:
                    self._target(**self._kwargs)

        with patch("apps.sparring.city_notify.threading.Thread", _InlineThread):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    reverse("sparring_create") + "?type=singles&sport=tennis",
                    {
                        "sparring_type": "singles",
                        "sport": "tennis",
                        "city": "Сочи",
                        "desired_category": "amateur",
                        "preferred_gender": "open",
                        "description": "Хочу играть вечером на выходных",
                        "preferred_days": "Сб, Вс",
                        "preferred_time": "19:00",
                        "preferred_location": "Центральный корт",
                        "desired_partner_age_min": "25",
                        "desired_partner_age_max": "45",
                    },
                    secure=True,
                )

        self.assertEqual(response.status_code, 302)
        created = SparringRequest.objects.get(player=self.author_user_player)
        notice = Notification.objects.get(user=self.neighbor.user)
        self.assertIn("Иван Заявка", notice.message)
        self.assertIn(f"#sparring-{created.pk}", notice.url)
