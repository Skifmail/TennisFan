"""Окна напоминаний о дедлайне: календарные сутки и письма для спаррингов."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from unittest.mock import patch
from urllib.parse import urlsplit

from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.notifications.models import Delivery, EventPolicy, NotificationPreference
from apps.telegram_bot.notifications import notify_match_deadline_reminder
from apps.tournaments.management.commands.send_deadline_reminders import (
    _notify_match_participants,
    build_deadline_reminder_lk_message,
    matches_with_deadline_in_days,
)
from apps.tournaments.models import Match, SparringDeadlineExtension, TournamentStatus
from apps.users.models import Notification
from tests.support.factories import make_player, make_tournament


def _end_of_day_in(days: int) -> datetime:
    """Дедлайн на конец суток через ``days`` календарных дней."""
    target = timezone.localdate() + timedelta(days=days)
    return timezone.make_aware(datetime.combine(target, time(23, 59)))


class DeadlineReminderWindowTestCase(TestCase):
    """Матчи выбираются по календарной дате дедлайна, а не по числу часов."""

    def setUp(self) -> None:
        self.p1 = make_player(email_suffix="win-p1")
        self.p2 = make_player(email_suffix="win-p2")
        self.tournament = make_tournament(
            name="Турнир окна напоминаний",
            slug="reminder-windows",
            format="round_robin",
            status=TournamentStatus.ACTIVE,
            bracket_generated=True,
            start_date=date.today(),
        )

    def _match(self, deadline: datetime | None, **kwargs) -> Match:
        """Создать запланированный матч с указанным дедлайном."""
        defaults = {
            "tournament": self.tournament,
            "round_name": "Тур 1",
            "round_index": 1,
            "round_order": 1,
            "player1": self.p1,
            "player2": self.p2,
            "status": Match.MatchStatus.SCHEDULED,
            "deadline": deadline,
        }
        defaults.update(kwargs)
        return Match.objects.create(**defaults)

    def test_end_of_day_deadline_falls_into_one_day_window(self) -> None:
        match = self._match(_end_of_day_in(1))

        self.assertIn(match, matches_with_deadline_in_days(1))
        self.assertNotIn(match, matches_with_deadline_in_days(2))

    def test_midnight_deadline_falls_into_two_day_window(self) -> None:
        target = timezone.localdate() + timedelta(days=2)
        match = self._match(timezone.make_aware(datetime.combine(target, time.min)))

        self.assertIn(match, matches_with_deadline_in_days(2))
        self.assertNotIn(match, matches_with_deadline_in_days(1))

    def test_far_and_past_deadlines_are_ignored(self) -> None:
        far = self._match(_end_of_day_in(5))
        today = self._match(_end_of_day_in(0))
        past = self._match(_end_of_day_in(-1))

        for days_left in (1, 2):
            selected = list(matches_with_deadline_in_days(days_left))
            self.assertNotIn(far, selected)
            self.assertNotIn(today, selected)
            self.assertNotIn(past, selected)

    def test_completed_match_is_not_reminded(self) -> None:
        match = self._match(
            _end_of_day_in(1),
            status=Match.MatchStatus.COMPLETED,
            winner=self.p1,
        )

        self.assertNotIn(match, matches_with_deadline_in_days(1))


class SparringDeadlineReminderTestCase(TestCase):
    """Напоминания о дедлайне приходят и по спаррингам (без турнира)."""

    def test_sparring_match_gets_reminder_email(self) -> None:
        p1 = make_player(email_suffix="spar-remind-p1")
        p2 = make_player(email_suffix="spar-remind-p2")
        Match.objects.create(
            match_type=Match.MatchType.SPARRING,
            player1=p1,
            player2=p2,
            status=Match.MatchStatus.SCHEDULED,
            deadline=_end_of_day_in(1),
        )
        mail.outbox.clear()

        call_command("send_deadline_reminders")

        reminder_mails = [
            m for m in mail.outbox if "до дедлайна матча" in m.subject.lower()
        ]
        self.assertEqual(len(reminder_mails), 2)
        recipients = {addr for m in reminder_mails for addr in m.to}
        self.assertEqual(recipients, {p1.user.email, p2.user.email})
        body = reminder_mails[0].alternatives[0][0]
        self.assertIn("40", body)


@override_settings(
    NOTIFICATION_CENTER_ENABLED=True,
    USER_NOTIFICATIONS_EMAIL_ENABLED=True,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class SparringExtensionReminderTextTestCase(TestCase):
    """Почта, ЛК и push объясняют продление только имеющему на него право игроку."""

    def setUp(self) -> None:
        self.p1 = make_player(email_suffix="hint-p1")
        self.p2 = make_player(email_suffix="hint-p2")
        self.match = Match.objects.create(
            match_type=Match.MatchType.SPARRING,
            player1=self.p1,
            player2=self.p2,
            deadline=_end_of_day_in(1),
        )
        EventPolicy.objects.filter(key="match.deadline_reminder").update(
            push_enabled=True,
            email_enabled=True,
            inbox_enabled=True,
        )
        for player in (self.p1, self.p2):
            NotificationPreference.objects.update_or_create(
                user=player.user,
                defaults={"push_enabled": True, "email_enabled": True},
            )

    @patch(
        "apps.tournaments.management.commands.send_deadline_reminders.tg.notify_match_deadline_reminder"
    )
    def test_reminders_include_personal_hint_and_direct_link(
        self, telegram: Any
    ) -> None:
        SparringDeadlineExtension.objects.create(
            match=self.match,
            player=self.p1,
            old_deadline=self.match.deadline - timedelta(days=7),
            new_deadline=self.match.deadline,
        )
        for days in (1, 2):
            with self.subTest(days=days):
                mail.outbox.clear()
                _notify_match_participants(self.match, days)
                for player, eligible in ((self.p1, False), (self.p2, True)):
                    note = Notification.objects.filter(
                        user=player.user, event_key="match.deadline_reminder"
                    ).latest("pk")
                    self.assertEqual(
                        note.url, reverse("match_detail", args=[self.match.pk])
                    )
                    self.assertEqual(
                        "Продлите на 7 дней один раз" in note.message, eligible
                    )
                    self.assertLessEqual(len(note.message), 255)
                    delivery = Delivery.objects.get(notification=note, channel="push")
                    self.assertEqual(
                        "Продлите на 7 дней один раз" in delivery.body, eligible
                    )
                    self.assertEqual(urlsplit(delivery.url).path, note.url)
                    email = next(
                        message
                        for message in mail.outbox
                        if player.user.email in message.to
                    )
                    self.assertEqual(
                        "Продлить дедлайн на неделю" in email.body, eligible
                    )
                    self.assertIn(note.url, email.alternatives[0][0])
                    if eligible:
                        self.assertIn("7 дней", email.body)
                    else:
                        self.assertIn("обратитесь к администратору", email.body)
        self.assertEqual(telegram.call_count, 2)

    def test_tournament_text_does_not_offer_self_extension(self) -> None:
        message = build_deadline_reminder_lk_message(
            tournament_name="Турнир",
            days_left=1,
            deadline_str="26.10.2026 23:59",
        )
        self.assertNotIn("Продлите", message)
        self.assertIn("RT 0:0", message)

    @patch("apps.telegram_bot.notifications.send_to_user_by_user")
    @patch("apps.telegram_bot.notifications.bot.is_configured", return_value=True)
    def test_telegram_reminder_links_to_self_extension(
        self, configured: Any, send: Any
    ) -> None:
        notify_match_deadline_reminder(self.match, days_left=1)
        self.assertEqual(send.call_count, 2)
        for call in send.call_args_list:
            self.assertIn("на 7 дней", call.args[1])
            rows = call.kwargs["reply_markup"]["inline_keyboard"]
            self.assertIn(
                reverse("match_detail", args=[self.match.pk]), rows[1][0]["url"]
            )
