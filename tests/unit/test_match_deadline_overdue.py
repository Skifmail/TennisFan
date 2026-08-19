"""Просрочка дедлайна: авто-RT по рейтингу и Walkover за неявку от клуба."""

from __future__ import annotations

from datetime import date, timedelta

from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.tournaments.models import Match, TournamentStatus
from apps.tournaments.overdue import (
    apply_no_show_walkover,
    find_match_for_walkover_replace,
    notify_admins_match_deadline_overdue,
    replace_no_show_walkover,
    revert_deadline_auto_rt,
)
from apps.tournaments.rating import WALKOVER_NO_SHOW_PENALTY
from apps.users.models import Notification
from tests.support.factories import make_player, make_tournament, make_user


@override_settings(
    EMAIL_BACKEND="apps.core.mail.LoggingEmailBackend",
    EMAIL_BACKEND_INNER="django.core.mail.backends.locmem.EmailBackend",
)
class NotifyOverdueDeadlineTestCase(TestCase):
    """Просроченный матч закрывается RT 0:0, побеждает более высокий рейтинг."""

    def setUp(self) -> None:
        self.admin = make_user(
            email="overdue-admin@test.local",
            is_staff=True,
        )
        self.p1 = make_player(email_suffix="overdue-p1", points=3000.0)
        self.p2 = make_player(email_suffix="overdue-p2", points=2500.0)
        self.tournament = make_tournament(
            name="Турнир просрочка",
            slug="overdue-notify",
            format="round_robin",
            status=TournamentStatus.ACTIVE,
            bracket_generated=True,
            start_date=date.today(),
        )
        self.match = Match.objects.create(
            tournament=self.tournament,
            round_name="Тур 4",
            round_index=4,
            round_order=1,
            player1=self.p1,
            player2=self.p2,
            status=Match.MatchStatus.SCHEDULED,
            deadline=timezone.now() - timedelta(hours=2),
        )

    def test_overdue_assigns_auto_rt_to_higher_rated_without_penalty(self) -> None:
        ok, msg = notify_admins_match_deadline_overdue(self.match)

        self.assertTrue(ok, msg)
        self.match.refresh_from_db()
        self.p1.refresh_from_db()
        self.p2.refresh_from_db()
        self.assertEqual(self.match.status, Match.MatchStatus.WALKOVER)
        self.assertTrue(self.match.is_deadline_auto_rt())
        self.assertFalse(self.match.is_no_show_walkover())
        self.assertEqual(self.match.winner_id, self.p1.pk)
        self.assertEqual(self.match.player1_set1, 0)
        self.assertEqual(self.match.player2_set1, 0)
        self.assertEqual(self.match.player1_set2, 0)
        self.assertEqual(self.match.player2_set2, 0)
        self.assertEqual(self.p1.total_points, 3000.0)
        self.assertEqual(self.p2.total_points, 2500.0)
        self.assertEqual(self.match.rating_delta_player1, 0.0)
        self.assertEqual(self.match.rating_delta_player2, 0.0)
        self.assertIsNotNone(self.match.deadline_overdue_notified_at)
        note = Notification.objects.get(user=self.admin)
        self.assertIn("Просрочен дедлайн", note.message)
        self.assertIn(f"#match-{self.match.pk}", note.url)
        overdue_mails = [
            m for m in mail.outbox if "просрочен дедлайн" in m.subject.lower()
        ]
        self.assertEqual(len(overdue_mails), 1)
        self.assertIn(self.admin.email, overdue_mails[0].to)
        self.assertIn("RT", overdue_mails[0].alternatives[0][0])

    def test_second_notify_is_skipped(self) -> None:
        notify_admins_match_deadline_overdue(self.match)

        ok, msg = notify_admins_match_deadline_overdue(self.match)

        self.assertFalse(ok)
        self.assertIn("уже", msg)
        self.assertEqual(Notification.objects.filter(user=self.admin).count(), 1)

    def test_revert_auto_rt_returns_match_to_scheduled(self) -> None:
        notify_admins_match_deadline_overdue(self.match)
        self.match.refresh_from_db()
        self.tournament.refresh_from_db()
        self.assertEqual(self.tournament.status, TournamentStatus.COMPLETED)

        revert_deadline_auto_rt(self.match)

        self.match.refresh_from_db()
        self.tournament.refresh_from_db()
        self.p1.refresh_from_db()
        self.p2.refresh_from_db()
        self.assertEqual(self.match.status, Match.MatchStatus.SCHEDULED)
        self.assertEqual(self.tournament.status, TournamentStatus.ACTIVE)
        self.assertFalse(
            self.tournament.fan_results.filter(place__isnull=False).exists()
        )
        self.assertIsNone(self.match.winner_id)
        self.assertIsNone(self.match.player1_set1)
        self.assertIsNone(self.match.completed_datetime)
        self.assertIsNone(self.match.deadline_overdue_notified_at)
        self.assertFalse(self.match.is_deadline_auto_rt())
        self.assertEqual(self.p1.total_points, 3000.0)
        self.assertEqual(self.p2.total_points, 2500.0)


class ApplyNoShowWalkoverTestCase(TestCase):
    """Walkover за неявку: −40 проигравшему, 0 победителю, статус без игры."""

    def setUp(self) -> None:
        self.p1 = make_player(email_suffix="wo-p1", points=3000.0)
        self.p2 = make_player(email_suffix="wo-p2", points=2500.0)
        for player in (self.p1, self.p2):
            player.matches_played = 12
            player.save(update_fields=["matches_played"])
        self.tournament = make_tournament(
            name="Турнир walkover",
            slug="wo-apply",
            format="round_robin",
            status=TournamentStatus.ACTIVE,
            bracket_generated=True,
            start_date=date.today(),
        )
        self.match = Match.objects.create(
            tournament=self.tournament,
            round_name="Тур 1",
            round_index=1,
            round_order=1,
            player1=self.p1,
            player2=self.p2,
            status=Match.MatchStatus.SCHEDULED,
            deadline=timezone.now() - timedelta(hours=1),
        )

    def test_walkover_penalizes_only_no_show_player(self) -> None:
        apply_no_show_walkover(self.match, loser=self.p2)

        self.match.refresh_from_db()
        self.p1.refresh_from_db()
        self.p2.refresh_from_db()
        self.assertEqual(self.match.status, Match.MatchStatus.WALKOVER)
        self.assertEqual(self.match.winner_id, self.p1.pk)
        self.assertIsNone(self.match.player1_set1)
        self.assertTrue(self.match.is_no_show_walkover())
        self.assertFalse(self.match.is_walkover_loss())
        self.assertEqual(self.p1.total_points, 3000.0)
        self.assertEqual(self.p2.total_points, 2500.0 - WALKOVER_NO_SHOW_PENALTY)
        self.assertEqual(self.match.rating_delta_player1, 0.0)
        self.assertEqual(self.match.rating_delta_player2, -WALKOVER_NO_SHOW_PENALTY)

    def test_walkover_custom_penalty_is_applied_to_no_show_player(self) -> None:
        apply_no_show_walkover(self.match, loser=self.p2, penalty=15.0)

        self.match.refresh_from_db()
        self.p1.refresh_from_db()
        self.p2.refresh_from_db()
        self.assertEqual(self.p1.total_points, 3000.0)
        self.assertEqual(self.p2.total_points, 2485.0)
        self.assertEqual(self.match.rating_delta_player2, -15.0)

    def test_walkover_zero_penalty_does_not_change_rating(self) -> None:
        apply_no_show_walkover(self.match, loser=self.p2, penalty=0.0)

        self.match.refresh_from_db()
        self.p1.refresh_from_db()
        self.p2.refresh_from_db()
        self.assertEqual(self.match.status, Match.MatchStatus.WALKOVER)
        self.assertEqual(self.p1.total_points, 3000.0)
        self.assertEqual(self.p2.total_points, 2500.0)
        self.assertEqual(self.match.rating_delta_player1, 0.0)
        self.assertEqual(self.match.rating_delta_player2, 0.0)

    def test_walkover_both_sides_has_no_winner(self) -> None:
        apply_no_show_walkover(
            self.match,
            side1_no_show=True,
            side2_no_show=True,
            penalty_side1=WALKOVER_NO_SHOW_PENALTY,
            penalty_side2=WALKOVER_NO_SHOW_PENALTY,
        )

        self.match.refresh_from_db()
        self.p1.refresh_from_db()
        self.p2.refresh_from_db()
        self.assertIsNone(self.match.winner_id)
        self.assertTrue(self.match.is_mutual_no_show_walkover())
        self.assertEqual(self.p1.total_points, 3000.0 - WALKOVER_NO_SHOW_PENALTY)
        self.assertEqual(self.p2.total_points, 2500.0 - WALKOVER_NO_SHOW_PENALTY)
        self.assertEqual(self.match.rating_delta_player1, -WALKOVER_NO_SHOW_PENALTY)
        self.assertEqual(self.match.rating_delta_player2, -WALKOVER_NO_SHOW_PENALTY)
        self.assertEqual(self.p1.matches_won, 0)
        self.assertEqual(self.p2.matches_won, 0)
        self.assertEqual(self.p1.matches_played, 13)
        self.assertEqual(self.p2.matches_played, 13)

        from apps.tournaments.round_robin import compute_standings_for_entities

        standings = compute_standings_for_entities(
            self.tournament, [self.p1, self.p2], [self.match]
        )
        by_player = {row["player"].pk: row for row in standings}
        self.assertEqual(by_player[self.p1.pk]["losses"], 1)
        self.assertEqual(by_player[self.p2.pk]["losses"], 1)
        self.assertEqual(by_player[self.p1.pk]["wins"], 0)
        self.assertEqual(by_player[self.p2.pk]["wins"], 0)

    def test_replace_walkover_changes_sides_and_penalties(self) -> None:
        apply_no_show_walkover(
            self.match,
            side1_no_show=True,
            side2_no_show=True,
            penalty_side1=WALKOVER_NO_SHOW_PENALTY,
            penalty_side2=WALKOVER_NO_SHOW_PENALTY,
            notify=False,
        )
        apply_no_show_walkover(
            self.match,
            side1_no_show=False,
            side2_no_show=True,
            penalty_side2=15.0,
            replace=True,
            notify=False,
        )

        self.match.refresh_from_db()
        self.p1.refresh_from_db()
        self.p2.refresh_from_db()
        self.assertEqual(self.match.winner_id, self.p1.pk)
        self.assertFalse(self.match.is_mutual_no_show_walkover())
        self.assertEqual(self.p1.total_points, 3000.0)
        self.assertEqual(self.p2.total_points, 2485.0)
        self.assertEqual(self.match.rating_delta_player1, 0.0)
        self.assertEqual(self.match.rating_delta_player2, -15.0)


class ReplaceNoShowWalkoverTestCase(TestCase):
    """Откат авто-FAN walkover и повторная простановка Walkover за неявку."""

    def setUp(self) -> None:
        self.p1 = make_player(
            email_suffix="replace-p1",
            first_name="Юлия",
            last_name="Кормилина",
            points=3000.0,
        )
        self.p2 = make_player(
            email_suffix="replace-p2",
            first_name="Анна",
            last_name="Сорокина",
            points=2500.0,
        )
        for player in (self.p1, self.p2):
            player.matches_played = 12
            player.matches_won = 4
            player.save(update_fields=["matches_played", "matches_won"])
        self.tournament = make_tournament(
            name="Многодневный турнир Воскресенск",
            slug="voskresensk-replace-wo",
            format="round_robin",
            status=TournamentStatus.ACTIVE,
            bracket_generated=True,
            start_date=date.today(),
        )
        self.old_delta1 = -102.5
        self.old_delta2 = -147.5
        self.match = Match.objects.create(
            tournament=self.tournament,
            round_name="Тур 4",
            round_index=4,
            round_order=1,
            player1=self.p1,
            player2=self.p2,
            status=Match.MatchStatus.SCHEDULED,
            deadline=timezone.now() - timedelta(hours=2),
        )
        # Старый авто-крон: WALKOVER без счёта и FAN от 0:0, без текущего сигнала.
        Match.objects.filter(pk=self.match.pk).update(
            winner=self.p2,
            status=Match.MatchStatus.WALKOVER,
            completed_datetime=timezone.now(),
            rating_status=Match.RatingCalcStatus.CALCULATED,
            rating_delta_player1=self.old_delta1,
            rating_delta_player2=self.old_delta2,
        )
        self.match.refresh_from_db()
        self.p1.total_points = 3000.0 + self.old_delta1
        self.p1.hidden_rating = 3000.0 + self.old_delta1
        self.p1.matches_played = 13
        self.p1.save(update_fields=["total_points", "hidden_rating", "matches_played"])
        self.p2.total_points = 2500.0 + self.old_delta2
        self.p2.hidden_rating = 2500.0 + self.old_delta2
        self.p2.matches_played = 13
        self.p2.matches_won = 5
        self.p2.save(
            update_fields=[
                "total_points",
                "hidden_rating",
                "matches_played",
                "matches_won",
            ]
        )

    def test_replace_reverts_old_fan_and_applies_no_show_penalty(self) -> None:
        lines = replace_no_show_walkover(self.match, loser=self.p1, notify=False)

        self.match.refresh_from_db()
        self.p1.refresh_from_db()
        self.p2.refresh_from_db()
        self.assertTrue(any("откат" in line.lower() for line in lines))
        self.assertEqual(self.match.status, Match.MatchStatus.WALKOVER)
        self.assertEqual(self.match.winner_id, self.p2.pk)
        self.assertTrue(self.match.is_no_show_walkover())
        self.assertEqual(self.p1.total_points, 3000.0 - WALKOVER_NO_SHOW_PENALTY)
        self.assertEqual(self.p2.total_points, 2500.0)
        self.assertEqual(self.match.rating_delta_player1, -WALKOVER_NO_SHOW_PENALTY)
        self.assertEqual(self.match.rating_delta_player2, 0.0)
        self.assertEqual(self.p1.matches_played, 13)
        self.assertEqual(self.p2.matches_played, 13)
        self.assertEqual(self.p1.matches_won, 4)
        self.assertEqual(self.p2.matches_won, 5)

    def test_dry_run_does_not_change_ratings(self) -> None:
        before_p1 = self.p1.total_points
        before_p2 = self.p2.total_points

        replace_no_show_walkover(self.match, loser=self.p1, dry_run=True, notify=False)

        self.match.refresh_from_db()
        self.p1.refresh_from_db()
        self.p2.refresh_from_db()
        self.assertEqual(self.p1.total_points, before_p1)
        self.assertEqual(self.p2.total_points, before_p2)
        self.assertEqual(self.match.rating_delta_player1, self.old_delta1)
        self.assertEqual(self.match.rating_delta_player2, self.old_delta2)

    def test_find_match_by_player_names(self) -> None:
        found = find_match_for_walkover_replace(
            player_query="Кормилина",
            opponent_query="Сорокина",
        )
        self.assertEqual(found.pk, self.match.pk)

    def test_management_command_keep_winner(self) -> None:
        call_command(
            "replace_overdue_walkover",
            player="Кормилина",
            opponent="Сорокина",
            keep_winner=True,
        )

        self.match.refresh_from_db()
        self.p1.refresh_from_db()
        self.p2.refresh_from_db()
        self.assertEqual(self.match.winner_id, self.p2.pk)
        self.assertEqual(self.p1.total_points, 3000.0 - WALKOVER_NO_SHOW_PENALTY)
        self.assertEqual(self.p2.total_points, 2500.0)


class DeadlineReminderCopyTestCase(TestCase):
    """Напоминания игрокам содержат предупреждение о Walkover (−40)."""

    def test_lk_message_mentions_penalty_and_fits_limit(self) -> None:
        from apps.tournaments.management.commands.send_deadline_reminders import (
            LK_MESSAGE_MAX_LEN,
            build_deadline_reminder_lk_message,
        )

        msg = build_deadline_reminder_lk_message(
            tournament_name="Многодневный турнир Воскресенск",
            days_left=2,
            deadline_str="17.08.2026 23:59",
        )
        self.assertIn("RT", msg)
        self.assertIn("рейтинг", msg)
        self.assertLessEqual(len(msg), LK_MESSAGE_MAX_LEN)

    def test_command_sends_email_with_penalty_warning(self) -> None:
        p1 = make_player(email_suffix="remind-p1", points=2000.0)
        p2 = make_player(email_suffix="remind-p2", points=1800.0)
        tournament = make_tournament(
            name="Турнир напоминание",
            slug="deadline-remind",
            format="round_robin",
            status=TournamentStatus.ACTIVE,
            bracket_generated=True,
            start_date=date.today(),
        )
        Match.objects.create(
            tournament=tournament,
            round_name="Тур 1",
            round_index=1,
            round_order=1,
            player1=p1,
            player2=p2,
            status=Match.MatchStatus.SCHEDULED,
            deadline=timezone.now() + timedelta(hours=24),
        )
        mail.outbox.clear()
        call_command("send_deadline_reminders")
        reminder_mails = [
            m for m in mail.outbox if "до дедлайна матча" in m.subject.lower()
        ]
        self.assertGreaterEqual(len(reminder_mails), 2)
        body = reminder_mails[0].alternatives[0][0]
        self.assertIn("RT", body)
        self.assertIn("40", body)
        note = Notification.objects.filter(user=p1.user).first()
        self.assertIsNotNone(note)
        assert note is not None
        self.assertIn("RT", note.message)
