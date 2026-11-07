"""Пакетные объявления турниров: сохранение, актуальная аудитория и откаты."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

from django.core import mail
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.clubs.models import (
    ClubMember,
    ClubMemberStatus,
    ClubNotificationConfig,
    ClubNotificationSettings,
)
from apps.core.models import UserTelegramLink
from apps.notifications.models import Delivery, EventPolicy, NotificationPreference
from apps.notifications.worker import process_deliveries
from apps.telegram_bot.announcements import (
    prepare_announcement,
    process_announcements,
    request_announcement,
)
from apps.telegram_bot.delivery import enqueue_message, process_telegram_deliveries
from apps.telegram_bot.models import TournamentAnnouncement
from apps.users.models import Notification, Player, User
from tests.support.factories import make_club, make_player, make_tournament
from tests.support.notifications import seed_event_policies


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    NOTIFICATION_CENTER_ENABLED=False,
)
class TelegramAnnouncementTests(TestCase):
    """Никакая подготовка не обращается к Telegram или SMTP."""

    def setUp(self) -> None:
        seed_event_policies()
        self.players = [
            make_player(email_suffix=f"announcement-{index}") for index in range(4)
        ]
        Player.objects.filter(pk__in=[player.pk for player in self.players]).update(
            city="г. Москва"
        )
        for index, player in enumerate(self.players, 1):
            UserTelegramLink.objects.create(
                user=player.user, user_bot_chat_id=1000 + index
            )
        self.tournament = make_tournament(slug="persisted-announcement", city="Москва")
        self.job = TournamentAnnouncement.objects.get(tournament=self.tournament)
        mail.outbox.clear()

    def test_tournament_creation_saves_a_single_job_before_commit(self) -> None:
        """Сигнал и явные повторы сохраняют одну подготовку, а не запускают поток."""
        repeated = request_announcement(self.tournament.pk)
        self.assertEqual(repeated.pk, self.job.pk)
        self.assertEqual(TournamentAnnouncement.objects.count(), 1)
        self.assertEqual(Delivery.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_preparation_advances_in_batches_without_duplicates(self) -> None:
        """После перезапуска курсор продолжает пакет, готовое объявление не повторяется."""
        self.assertEqual(prepare_announcement(self.job.pk, batch_size=2), 2)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, "queued")
        self.assertEqual(self.job.cursor_user_id, self.players[1].user_id)
        self.assertEqual(self.job.deliveries.filter(channel="telegram").count(), 2)
        self.assertEqual(prepare_announcement(self.job.pk, batch_size=2), 2)
        self.assertEqual(prepare_announcement(self.job.pk, batch_size=2), 0)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, "prepared")
        self.assertEqual(
            Notification.objects.filter(event_key="tournament.created").count(), 4
        )
        self.assertEqual(self.job.deliveries.count(), 8)
        self.assertEqual(len(mail.outbox), 0)

    def test_limit_bounds_scanning_not_only_matching_recipients(self) -> None:
        """Нерелевантный город расходует один слот, а не требует полного сканирования."""
        Player.objects.filter(pk=self.players[0].pk).update(city="Санкт-Петербург")
        self.assertEqual(prepare_announcement(self.job.pk, batch_size=1), 1)
        self.job.refresh_from_db()
        self.assertEqual(self.job.cursor_user_id, self.players[0].user_id)
        self.assertEqual(self.job.deliveries.count(), 0)

    def test_new_users_are_not_added_to_a_started_snapshot(self) -> None:
        """Регистрации после начала подготовки не расширяют бесконечно аудиторию."""
        prepare_announcement(self.job.pk, batch_size=1)
        newcomer = make_player(email_suffix="announcement-newcomer")
        Player.objects.filter(pk=newcomer.pk).update(city="Москва")
        UserTelegramLink.objects.create(user=newcomer.user, user_bot_chat_id=2000)
        prepare_announcement(self.job.pk, batch_size=100)
        self.assertFalse(self.job.deliveries.filter(user=newcomer.user).exists())

    def test_failed_batch_rolls_back_notes_deliveries_and_cursor(self) -> None:
        """Ошибка посередине пакета не оставляет частично подготовленных получателей."""
        calls = 0

        def enqueue(
            user: User,
            text: str,
            *,
            reply_markup: object = None,
            event_key: str,
            announcement: TournamentAnnouncement,
            expires_at: datetime,
        ) -> Delivery | None:
            """Сохранить первый адресат и воспроизвести сбой следующего в том же пакете."""
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ValueError("Сбой пакета")
            return enqueue_message(
                user,
                text,
                event_key=event_key,
                announcement=announcement,
                expires_at=expires_at,
            )

        with patch(
            "apps.telegram_bot.announcements.enqueue_message", side_effect=enqueue
        ):
            with self.assertRaises(ValueError):
                prepare_announcement(self.job.pk, batch_size=2)
        self.job.refresh_from_db()
        self.assertEqual(self.job.cursor_user_id, 0)
        self.assertIsNone(self.job.started_at)
        self.assertEqual(self.job.deliveries.count(), 0)
        self.assertFalse(
            Notification.objects.filter(event_key="tournament.created").exists()
        )
        self.assertEqual(prepare_announcement(self.job.pk, batch_size=2), 2)

    def test_tournament_rollback_removes_its_job(self) -> None:
        """Задание объявления не переживает отмену создания турнира."""
        with self.assertRaises(ValueError):
            with transaction.atomic():
                tournament = make_tournament(slug="rolled-back-announcement")
                self.assertTrue(
                    TournamentAnnouncement.objects.filter(
                        tournament=tournament
                    ).exists()
                )
                raise ValueError("Отмена турнира")
        self.assertEqual(TournamentAnnouncement.objects.count(), 1)

    def test_process_has_one_total_preparation_budget(self) -> None:
        """Несколько турниров делят конечный бюджет кандидатов за запуск."""
        make_tournament(slug="another-announcement")
        self.assertEqual(process_announcements(limit=3), 3)
        self.assertEqual(Delivery.objects.filter(channel="telegram").count(), 3)

    def test_expired_preparation_never_creates_new_deliveries(self) -> None:
        """Остановленный на сутки cron не объявляет устаревший турнир."""
        TournamentAnnouncement.objects.filter(pk=self.job.pk).update(
            created_at=timezone.now() - timedelta(days=2)
        )
        self.assertEqual(prepare_announcement(self.job.pk), 0)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, "expired")
        self.assertEqual(Delivery.objects.count(), 0)

    def test_email_worker_keeps_branded_mail_when_center_disabled(self) -> None:
        """Брендированное письмо отправляется один раз даже без push/email-центра."""
        prepare_announcement(self.job.pk, batch_size=1)
        self.assertEqual(process_deliveries(limit=10), 1)
        self.assertEqual(process_deliveries(limit=10), 0)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        assert isinstance(message, EmailMultiAlternatives)
        html = message.alternatives[0][0]
        assert isinstance(html, str)
        self.assertIn("TENNISFAN", html)
        self.assertEqual(self.job.deliveries.get(channel="email").status, "accepted")

    @override_settings(NOTIFICATION_CENTER_ENABLED=True)
    def test_email_consent_is_rechecked_without_duplicate_inbox(self) -> None:
        """Отзыв почтового согласия после подготовки запрещает SMTP, но не Telegram."""
        EventPolicy.objects.filter(key="tournament.created").update(
            email_enabled=True, push_enabled=False
        )
        prepare_announcement(self.job.pk, batch_size=1)
        NotificationPreference.objects.update_or_create(
            user=self.players[0].user, defaults={"email_enabled": False}
        )
        process_deliveries(limit=10)
        self.assertEqual(self.job.deliveries.get(channel="email").status, "skipped")
        self.assertEqual(self.job.deliveries.get(channel="telegram").status, "queued")
        self.assertEqual(
            Notification.objects.filter(event_key="tournament.created").count(), 1
        )
        self.assertEqual(len(mail.outbox), 0)

    def test_changed_city_or_cancelled_tournament_is_skipped_at_delivery(self) -> None:
        """Актуальность города и турнира проверяется перед каждым транспортом."""
        prepare_announcement(self.job.pk, batch_size=2)
        Player.objects.filter(pk=self.players[0].pk).update(city="Тверь")
        self.tournament.status = "cancelled"
        self.tournament.save(update_fields=["status"])
        with patch("apps.telegram_bot.services.is_configured", return_value=True):
            with patch("apps.telegram_bot.services.send_message_or_raise") as send:
                process_telegram_deliveries()
        send.assert_not_called()
        process_deliveries()
        self.assertEqual(self.job.deliveries.filter(status="skipped").count(), 4)
        self.assertEqual(len(mail.outbox), 0)

    def test_club_membership_and_telegram_consent_are_rechecked(self) -> None:
        """Выход из клуба и выключенный Telegram не превращаются в email-дубли."""
        club = make_club(slug="announcement-club")
        ClubNotificationConfig.objects.create(
            club=club, notify_by_telegram=True, tournament_reminders_enabled=True
        )
        memberships = [
            ClubMember.objects.create(
                club=club, user=player.user, status=ClubMemberStatus.ACTIVE
            )
            for player in self.players[:2]
        ]
        tournament = make_tournament(slug="club-announcement", club=club)
        job = TournamentAnnouncement.objects.get(tournament=tournament)
        prepare_announcement(job.pk)
        memberships[0].status = ClubMemberStatus.REMOVED
        memberships[0].save(update_fields=["status"])
        ClubNotificationSettings.objects.update_or_create(
            club=club, user=self.players[1].user, defaults={"telegram_enabled": False}
        )
        with patch("apps.telegram_bot.services.is_configured", return_value=True):
            with patch("apps.telegram_bot.services.send_message_or_raise") as send:
                process_telegram_deliveries()
        send.assert_not_called()
        self.assertEqual(
            job.deliveries.filter(channel="telegram", status="skipped").count(), 2
        )


@override_settings(
    TELEGRAM_BOT_SITE_BASE_URL="https://tennisfan.ru",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    NOTIFICATION_CENTER_ENABLED=False,
)
class TelegramAnnouncementButtonsTests(TestCase):
    """Анонс нового турнира получает inline-кнопки на сайт."""

    def setUp(self) -> None:
        self.players = [
            make_player(email_suffix=f"announcement-button-{index}")
            for index in range(1)
        ]
        Player.objects.filter(pk__in=[player.pk for player in self.players]).update(
            city="г. Москва"
        )
        for index, player in enumerate(self.players, 1):
            UserTelegramLink.objects.create(
                user=player.user, user_bot_chat_id=1000 + index
            )
        self.tournament = make_tournament(
            slug="announcement-buttons", city="Москва", max_participants=16
        )
        self.job = TournamentAnnouncement.objects.get(tournament=self.tournament)

    def test_announcement_delivery_has_registration_and_detail_buttons(self) -> None:
        """Подготовленный анонс содержит кнопки записи и подробностей."""
        prepare_announcement(self.job.pk, batch_size=1)
        delivery = self.job.deliveries.get(channel="telegram")
        keyboard = delivery.telegram_payload["reply_markup"]["inline_keyboard"]
        buttons = [button for row in keyboard for button in row]
        labels = [button["text"] for button in buttons]
        self.assertIn("✍️ Зарегистрироваться", labels)
        self.assertIn("🔗 Подробнее о турнире", labels)
        by_label = {button["text"]: button["url"] for button in buttons}
        self.assertEqual(
            by_label["✍️ Зарегистрироваться"],
            "https://tennisfan.ru"
            + reverse("tournament_register", args=[self.tournament.slug]),
        )
        self.assertEqual(
            by_label["🔗 Подробнее о турнире"],
            "https://tennisfan.ru"
            + reverse("tournament_detail", args=[self.tournament.slug]),
        )

    def test_announcement_without_registration_omits_register_button(self) -> None:
        """Если сетка уже сформирована, анонс не обещает запись."""
        self.tournament.bracket_generated = True
        self.tournament.save(update_fields=["bracket_generated"])
        prepare_announcement(self.job.pk, batch_size=1)
        delivery = self.job.deliveries.get(channel="telegram")
        keyboard = delivery.telegram_payload["reply_markup"]["inline_keyboard"]
        buttons = [button for row in keyboard for button in row]
        labels = [button["text"] for button in buttons]
        self.assertNotIn("✍️ Зарегистрироваться", labels)
        self.assertIn("🔗 Подробнее о турнире", labels)
