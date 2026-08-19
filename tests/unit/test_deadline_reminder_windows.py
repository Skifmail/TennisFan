"""Окна напоминаний о дедлайне: календарные сутки и письма для спаррингов."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from django.core import mail
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.tournaments.management.commands.send_deadline_reminders import (
    matches_with_deadline_in_days,
)
from apps.tournaments.models import Match, TournamentStatus
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
