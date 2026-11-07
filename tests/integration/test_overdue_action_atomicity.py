"""Свежесть и атомарность неявки, автоматического RT и отката результата."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.core.models import UserTelegramLink
from apps.core.sports import Sport
from apps.notifications.models import Delivery
from apps.tournaments.models import Match
from apps.tournaments.overdue import (
    apply_deadline_auto_rt,
    apply_no_show_walkover,
    notify_admins_match_deadline_overdue,
    revert_deadline_auto_rt,
    revert_no_show_walkover,
)
from apps.tournaments.proposal_service import submit_match_result
from apps.users.models import Notification
from apps.users.sport_rating import ensure_sport_profile
from tests.support.factories import make_player


@override_settings(
    NOTIFICATION_CENTER_ENABLED=False, USER_NOTIFICATIONS_EMAIL_ENABLED=False
)
class OverdueActionAtomicityTests(TestCase):
    """Устаревшая копия не разрешает повторно изменить уже обработанный матч."""

    def setUp(self) -> None:
        """Создать просроченный спарринг без внешних транспортов."""
        self.p1 = make_player(email_suffix="overdue-atomic-1", points=3000)
        self.p2 = make_player(email_suffix="overdue-atomic-2", points=2500)
        self.match = Match.objects.create(
            match_type=Match.MatchType.SPARRING,
            player1=self.p1,
            player2=self.p2,
            deadline=timezone.now() - timedelta(days=1),
        )
        Notification.objects.all().delete()

    def _played(self) -> None:
        """Применить настоящий сыгранный результат."""
        submit_match_result(
            self.match.pk, self.p1, result="win", player1_set1=6, player2_set1=4
        )

    def test_stale_no_show_does_not_replace_played_result(self) -> None:
        self._played()
        with self.assertRaises(ValueError):
            apply_no_show_walkover(self.match, loser=self.p2, notify=False)
        self.assertEqual(
            Match.objects.get(pk=self.match.pk).status, Match.MatchStatus.COMPLETED
        )

    def test_cancelled_match_cannot_receive_no_show(self) -> None:
        Match.objects.filter(pk=self.match.pk).update(
            status=Match.MatchStatus.CANCELLED
        )
        with self.assertRaises(ValueError):
            apply_no_show_walkover(self.match, loser=self.p2, notify=False)
        self.assertEqual(
            Match.objects.get(pk=self.match.pk).status, Match.MatchStatus.CANCELLED
        )

    def test_stale_auto_rt_does_not_replace_played_result(self) -> None:
        self._played()
        with self.assertRaises(ValueError):
            apply_deadline_auto_rt(self.match, notify=False)
        self.assertEqual(
            Match.objects.get(pk=self.match.pk).status, Match.MatchStatus.COMPLETED
        )

    def test_extended_deadline_cannot_be_closed_by_stale_cron(self) -> None:
        Match.objects.filter(pk=self.match.pk).update(
            deadline=timezone.now() + timedelta(days=2)
        )
        ok, _message = notify_admins_match_deadline_overdue(self.match)
        self.assertFalse(ok)
        self.assertEqual(
            Match.objects.get(pk=self.match.pk).status, Match.MatchStatus.SCHEDULED
        )
        self.assertFalse(Notification.objects.exists())

    def test_auto_rt_notification_failure_rolls_back_result(self) -> None:
        with patch(
            "apps.tournaments.overdue.create_notification",
            side_effect=RuntimeError("RT inbox failed"),
        ):
            with self.assertRaises(RuntimeError):
                apply_deadline_auto_rt(self.match)
        self.assertEqual(
            Match.objects.get(pk=self.match.pk).status, Match.MatchStatus.SCHEDULED
        )
        self.assertFalse(Notification.objects.exists())

    def test_no_show_notification_failure_rolls_back_rating_and_result(self) -> None:
        with patch(
            "apps.tournaments.proposal_service.notify_participants_match_result_confirmed",
            side_effect=RuntimeError("WO delivery failed"),
        ):
            with self.assertRaises(RuntimeError):
                apply_no_show_walkover(self.match, loser=self.p2)
        self.assertEqual(
            Match.objects.get(pk=self.match.pk).status, Match.MatchStatus.SCHEDULED
        )
        self.p2.refresh_from_db()
        self.assertEqual(self.p2.total_points, 2500)
        self.assertEqual(self.p2.matches_played, 0)

    def test_stale_revert_cannot_undo_new_played_result(self) -> None:
        apply_no_show_walkover(self.match, loser=self.p2, notify=False)
        stale = Match.objects.get(pk=self.match.pk)
        revert_no_show_walkover(self.match)
        self._played()
        with self.assertRaises(ValueError):
            revert_no_show_walkover(stale)
        self.assertEqual(
            Match.objects.get(pk=self.match.pk).status, Match.MatchStatus.COMPLETED
        )

    def test_revert_bracket_failure_rolls_back_rating_and_status(self) -> None:
        apply_no_show_walkover(self.match, loser=self.p2, notify=False)
        with patch(
            "apps.tournaments.overdue._sync_bracket_after_walkover_edit",
            side_effect=RuntimeError("bracket failure"),
        ):
            with self.assertRaises(RuntimeError):
                revert_no_show_walkover(self.match)
        self.assertEqual(
            Match.objects.get(pk=self.match.pk).status, Match.MatchStatus.WALKOVER
        )
        self.p2.refresh_from_db()
        self.assertEqual(self.p2.total_points, 2460)
        self.assertEqual(self.p2.matches_played, 1)

    def test_doubles_sparring_revert_restores_both_partners(self) -> None:
        partner1 = make_player(email_suffix="overdue-atomic-partner1", points=3000)
        partner2 = make_player(email_suffix="overdue-atomic-partner2", points=2500)
        self.match.partner1, self.match.partner2 = partner1, partner2
        self.match.save(update_fields=["partner1", "partner2"])
        apply_no_show_walkover(self.match, loser=self.p2, notify=False)
        revert_no_show_walkover(self.match)
        for player, points in (
            (self.p1, 3000),
            (partner1, 3000),
            (self.p2, 2500),
            (partner2, 2500),
        ):
            player.refresh_from_db()
            self.assertEqual(player.total_points, points)
            self.assertEqual(player.matches_played, 0)
        self.assertFalse(Delivery.objects.exists())

    def test_padel_revert_does_not_change_tennis_rating_or_stats(self) -> None:
        """Неявка и её откат возвращают только профиль вида спорта матча."""
        profiles = [
            ensure_sport_profile(player, Sport.PADEL) for player in (self.p1, self.p2)
        ]
        for profile in profiles:
            profile.total_points = profile.hidden_rating = 4000
            profile.matches_played = 4
            profile.matches_won = 1
            profile.save()
        self.match.sport = Sport.PADEL
        self.match.save(update_fields=["sport"])
        apply_no_show_walkover(self.match, loser=self.p2, notify=False)
        revert_no_show_walkover(self.match)
        for profile in profiles:
            profile.refresh_from_db()
            self.assertEqual(profile.total_points, 4000)
            self.assertEqual(profile.matches_played, 4)
            self.assertEqual(profile.matches_won, 1)
        for player, points in ((self.p1, 3000), (self.p2, 2500)):
            player.refresh_from_db()
            self.assertEqual(player.total_points, points)
            self.assertEqual(player.matches_played, 0)

    def test_doubles_no_show_edit_reverts_rating_and_wins_of_all_four_players(
        self,
    ) -> None:
        """Замена стороны неявки не оставляет прежний штраф у партнёра."""
        partner1 = make_player(email_suffix="overdue-edit-partner1", points=3000)
        partner2 = make_player(email_suffix="overdue-edit-partner2", points=2500)
        self.match.partner1, self.match.partner2 = partner1, partner2
        self.match.save(update_fields=["partner1", "partner2"])
        apply_no_show_walkover(self.match, loser=self.p2, notify=False)
        apply_no_show_walkover(self.match, loser=self.p1, replace=True, notify=False)
        for player, points, wins in (
            (self.p1, 2960, 0),
            (partner1, 2960, 0),
            (self.p2, 2500, 1),
            (partner2, 2500, 1),
        ):
            player.refresh_from_db()
            self.assertEqual(player.total_points, points)
            self.assertEqual(player.matches_played, 1)
            self.assertEqual(player.matches_won, wins)

    def test_auto_rt_sparring_uses_sum_of_both_partner_ratings_and_notifies_everyone(
        self,
    ) -> None:
        """У пары учитывается общий рейтинг, уведомления получают также партнёры."""
        partner1 = make_player(email_suffix="overdue-rt-partner1", points=100)
        partner2 = make_player(email_suffix="overdue-rt-partner2", points=4000)
        self.match.partner1, self.match.partner2 = partner1, partner2
        self.match.save(update_fields=["partner1", "partner2"])
        apply_deadline_auto_rt(self.match)
        self.assertEqual(self.match.winner_id, self.p2.pk)
        self.assertEqual(
            Notification.objects.filter(event_key="match.overdue_result").count(), 4
        )
        for player in (partner1, partner2):
            self.assertIn("RT 0:0", Notification.objects.get(user=player.user).message)

    def test_auto_rt_saves_telegram_result_with_dispute_context(self) -> None:
        """Автоматический результат также должен доходить до бота с оспариванием."""
        for index, player in enumerate((self.p1, self.p2)):
            UserTelegramLink.objects.create(
                user=player.user, user_bot_chat_id=5000 + index
            )
        with patch("apps.telegram_bot.services.is_configured", return_value=True):
            apply_deadline_auto_rt(self.match)
        deliveries = list(
            Delivery.objects.filter(
                channel="telegram", event_key="match.result_confirmed"
            )
        )
        self.assertEqual(len(deliveries), 2)
        for delivery in deliveries:
            payload = delivery.telegram_payload
            assert payload is not None
            self.assertIn("RT 0:0", payload["text"])
            buttons = payload["reply_markup"]["inline_keyboard"]
            self.assertTrue(
                any("Оспорить" in button["text"] for row in buttons for button in row)
            )

    def test_explicit_new_deadline_is_saved_with_auto_rt_revert(self) -> None:
        """Свежесть результата не должна потерять явно переданную администратором дату."""
        apply_deadline_auto_rt(self.match, notify=False)
        new_deadline = timezone.now() + timedelta(days=7)
        revert_deadline_auto_rt(self.match, new_deadline=new_deadline)
        self.assertEqual(Match.objects.get(pk=self.match.pk).deadline, new_deadline)
