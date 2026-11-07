"""Применённые результаты, оспаривание и одинаковые правила дедлайна."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.models import UserTelegramLink
from apps.telegram_bot import notifications
from apps.tournaments.management.commands.send_deadline_reminders import (
    _notify_match_participants,
)
from apps.tournaments.models import Match, MatchResultProposal
from tests.support.factories import make_player, make_tournament


@override_settings(
    TELEGRAM_BOT_SITE_BASE_URL="https://tennisfan.ru",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    USER_NOTIFICATIONS_EMAIL_ENABLED=True,
    NOTIFICATION_CENTER_ENABLED=False,
)
class TelegramResultMessageTests(TestCase):
    """Тексты проверяются на реальных матчах с замоканными отправителями."""

    def setUp(self) -> None:
        self.p1 = make_player(email_suffix="tg-result-1")
        self.p2 = make_player(email_suffix="tg-result-2")
        self.tournament = make_tournament(
            name="Кубок <Весна> & друзья", slug="tg-result-context"
        )
        self.match = Match.objects.create(
            player1=self.p1,
            player2=self.p2,
            tournament=self.tournament,
            deadline=timezone.now() + timedelta(days=1),
        )
        mail.outbox.clear()

    def test_opponent_receives_own_result_and_dispute_link_with_match_context(
        self,
    ) -> None:
        proposal = MatchResultProposal.objects.create(
            match=self.match,
            proposer=self.p1,
            result=Match.ResultChoice.WIN,
            player1_set1=6,
            player2_set1=4,
            player1_set2=6,
            player2_set2=3,
        )
        with patch(
            "apps.telegram_bot.notifications.bot.is_configured", return_value=True
        ):
            with patch("apps.telegram_bot.notifications.send_to_user_by_user") as send:
                notifications.notify_result_proposal(proposal)
        text = send.call_args.args[1]
        self.assertIn("Вы проиграли", text)
        self.assertIn("&lt;Весна&gt; &amp;", text)
        self.assertNotIn("Кубок <Весна>", text)
        rows = send.call_args.kwargs["reply_markup"]["inline_keyboard"]
        button = next(
            button for row in rows for button in row if "Оспорить" in button["text"]
        )
        query = parse_qs(urlsplit(button["url"]).query)
        self.assertEqual(urlsplit(button["url"]).path, reverse("support_feedback"))
        self.assertIn(str(self.match.pk), query["subject"][0])
        self.assertIn("6:4", query["message"][0])
        self.assertIn(
            reverse("match_detail", args=[self.match.pk]), query["message"][0]
        )

    def test_applied_result_does_not_claim_opponent_confirmation(self) -> None:
        self.match.status = Match.MatchStatus.COMPLETED
        self.match.winner = self.p1
        self.match.player1_set1, self.match.player2_set1 = 6, 4
        self.match.save()
        with patch(
            "apps.telegram_bot.notifications.bot.is_configured", return_value=True
        ):
            with patch("apps.telegram_bot.notifications.send_to_user_by_user") as send:
                notifications.notify_result_confirmed_to_participants(self.match)
        self.assertEqual(send.call_count, 2)
        for call in send.call_args_list:
            self.assertIn("Результат матча применён", call.args[1])
            self.assertIn("Оспорить", str(call.kwargs["reply_markup"]))

    def test_deadline_reminder_matches_tournament_rt_rule_and_has_no_email_duplicate(
        self,
    ) -> None:
        for player, chat_id in ((self.p1, 1001), (self.p2, 1002)):
            UserTelegramLink.objects.create(user=player.user, user_bot_chat_id=chat_id)
        with patch(
            "apps.telegram_bot.notifications.bot.is_configured", return_value=True
        ):
            with patch(
                "apps.telegram_bot.notifications.bot.send_to_user", return_value=True
            ) as send:
                _notify_match_participants(self.match, days_left=1)
        send.assert_not_called()
        self.assertEqual(len(mail.outbox), 2)
        with patch("apps.telegram_bot.notifications.send_to_user_by_user") as notify:
            with patch(
                "apps.telegram_bot.notifications.bot.is_configured", return_value=True
            ):
                notifications.notify_match_deadline_reminder(self.match, days_left=1)
        for call in notify.call_args_list:
            self.assertIn("RT 0:0", call.args[1])
            self.assertNotIn("−40 очков рейтинга каждому", call.args[1])
            self.assertTrue(call.kwargs["skip_email"])
