"""Регрессии привязки аккаунта и игровых сценариев Telegram-webhook."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, ClassVar
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.http import JsonResponse
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.models import UserTelegramLink
from apps.telegram_bot import views
from apps.telegram_bot.binding import consume_binding_token
from apps.tournaments.models import Match
from apps.users.models import Player
from tests.support.factories import make_player


@override_settings(TELEGRAM_USER_BOT_WEBHOOK_SECRET="regression-secret")
class TelegramWebhookRegressionTests(TestCase):
    """Проверки реального обработчика без сетевых вызовов к Telegram."""

    player1: ClassVar[Player]
    player2: ClassVar[Player]
    partner1: ClassVar[Player]
    partner2: ClassVar[Player]

    @classmethod
    def setUpTestData(cls) -> None:
        cls.player1 = make_player(email_suffix="tg-regression-p1")
        cls.player2 = make_player(email_suffix="tg-regression-p2")
        cls.partner1 = make_player(email_suffix="tg-regression-partner1")
        cls.partner2 = make_player(email_suffix="tg-regression-partner2")

    def setUp(self) -> None:
        cache.clear()
        self.update_id = 0
        self.factory = RequestFactory()
        self.send_message: MagicMock = self._mock(
            "apps.telegram_bot.views.bot.send_message", return_value=(1, True)
        )
        self.send_to_user: MagicMock = self._mock(
            "apps.telegram_bot.views.bot.send_to_user", return_value=True
        )
        self.answer: MagicMock = self._mock("apps.telegram_bot.views._answer_callback")

    def _mock(self, target: str, **kwargs: Any) -> MagicMock:
        patcher = patch(target, **kwargs)
        self.addCleanup(patcher.stop)
        mocked = patcher.start()
        assert isinstance(mocked, MagicMock)
        return mocked

    def _post(self, data: Any, *, secret: str = "regression-secret") -> JsonResponse:
        request = self.factory.post(
            "/telegram/user-bot-webhook/",
            data=json.dumps(data),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=secret,
        )
        return views.user_bot_webhook(request)

    def _message(self, text: str, chat_id: int = 1001) -> dict[str, Any]:
        self.update_id += 1
        return {
            "update_id": self.update_id,
            "message": {
                "message_id": self.update_id,
                "date": 1,
                "from": {"id": chat_id},
                "chat": {"id": chat_id, "type": "private"},
                "text": text,
            },
        }

    def _callback(self, data: str, chat_id: int = 1001) -> dict[str, Any]:
        self.update_id += 1
        return {
            "update_id": self.update_id,
            "callback_query": {
                "id": "callback-regression",
                "from": {"id": chat_id},
                "data": data,
                "message": {
                    "message_id": self.update_id,
                    "date": 1,
                    "chat": {"id": chat_id, "type": "private"},
                },
            },
        }

    def _sparring(self, *, doubles: bool = False) -> Match:
        return Match.objects.create(
            match_type=Match.MatchType.SPARRING,
            player1=self.player1,
            player2=self.player2,
            partner1=self.partner1 if doubles else None,
            partner2=self.partner2 if doubles else None,
            status=Match.MatchStatus.SCHEDULED,
        )

    def test_empty_webhook_secret_rejects_request(self) -> None:
        with override_settings(TELEGRAM_USER_BOT_WEBHOOK_SECRET=""):
            response = self._post(self._message("/start"), secret="")
        self.assertEqual(response.status_code, 403)
        self.send_message.assert_not_called()

    def test_wrong_webhook_secret_rejects_request(self) -> None:
        self.assertEqual(self._post({}, secret="wrong").status_code, 403)

    def test_stale_model_cannot_return_consumed_binding_token(self) -> None:
        link = UserTelegramLink.objects.create(
            user=self.player1.user,
            binding_token="consumed-token",
            token_created_at=timezone.now(),
        )
        stale = UserTelegramLink.objects.get(pk=link.pk)
        self.assertEqual(consume_binding_token("consumed-token", 1001), (True, ""))
        token = stale.get_or_create_binding_token()
        self.assertNotEqual(token, "consumed-token")
        link.refresh_from_db()
        self.assertEqual(link.binding_token, token)
        self.assertEqual(link.user_bot_chat_id, 1001)

    @override_settings(SECURE_SSL_REDIRECT=False, ALLOWED_HOSTS=["testserver"])
    def test_disconnect_revokes_unused_binding_token(self) -> None:
        """Отмена подключения отзывает ссылку, даже если /start ещё не был выполнен."""
        link = UserTelegramLink.objects.create(
            user=self.player1.user,
            binding_token="unused-token",
            token_created_at=timezone.now(),
        )
        self.client.force_login(self.player1.user)
        self.assertEqual(
            self.client.post(reverse("telegram_disconnect")).status_code, 302
        )
        self.assertEqual(consume_binding_token("unused-token", 1001)[0], False)
        link.refresh_from_db()
        self.assertIsNone(link.binding_token)

    def test_non_object_update_is_ignored(self) -> None:
        self.assertEqual(self._post([]).status_code, 200)
        self.send_message.assert_not_called()

    def test_duplicate_update_is_not_dispatched_again_after_cache_reset(self) -> None:
        update = self._message("/start")
        self._post(update)
        cache.clear()
        self._post(update)
        self.send_message.assert_called_once()

    def test_duplicate_callback_does_not_repeat_game_action(self) -> None:
        update = self._callback("menu_my_profile")
        with patch(
            "apps.telegram_bot.views._handle_proposal_callback", return_value=True
        ) as handler:
            self._post(update)
            self._post(update)
        handler.assert_called_once()

    def test_failed_dispatch_is_rolled_back_and_can_be_retried(self) -> None:
        link = UserTelegramLink.objects.create(
            user=self.player1.user, user_bot_chat_id=1001
        )
        update = self._callback("menu_my_profile")

        def fail(callback_query: dict[str, Any], base_url: str) -> bool:
            UserTelegramLink.objects.filter(pk=link.pk).update(user_bot_chat_id=2002)
            raise RuntimeError("dispatch failed")

        with patch(
            "apps.telegram_bot.views._handle_proposal_callback", side_effect=fail
        ):
            self.assertEqual(self._post(update).status_code, 503)
        link.refresh_from_db()
        self.assertEqual(link.user_bot_chat_id, 1001)
        with patch(
            "apps.telegram_bot.views._handle_proposal_callback", return_value=True
        ) as handler:
            self.assertEqual(self._post(update).status_code, 200)
        handler.assert_called_once()

    def test_malformed_nested_payloads_and_update_ids_are_ignored(self) -> None:
        malformed = [
            {"update_id": 1, "message": []},
            {"update_id": 2, "message": {"chat": []}},
            {"update_id": 3, "callback_query": {"message": []}},
            {"update_id": 4, "callback_query": {"message": {"chat": []}, "from": []}},
            {
                "update_id": 5,
                "message": {
                    "message_id": 1,
                    "from": {"id": 1001},
                    "chat": {"id": 1001, "type": "private"},
                    "text": ["/start"],
                },
            },
        ]
        for update_id in (None, True, "1", -1, 2**63):
            update = self._message("/start")
            update["update_id"] = update_id
            malformed.append(update)
        missing_id = self._message("/start")
        del missing_id["update_id"]
        malformed.append(missing_id)
        for update in malformed:
            with self.subTest(update=update):
                self.assertEqual(self._post(update).status_code, 200)
        self.send_message.assert_not_called()

    def test_private_message_requires_matching_real_integer_sender_and_chat(
        self,
    ) -> None:
        for sender in (1002, True, "1001", None):
            with self.subTest(sender=sender):
                update = self._message("/start")
                update["message"]["from"]["id"] = sender
                self._post(update)
        self.send_message.assert_not_called()

    def test_expired_missing_and_future_token_dates_are_rejected(self) -> None:
        for created_at in (
            timezone.now() - timedelta(days=2),
            timezone.now() - timedelta(hours=24),
            None,
            timezone.now() + timedelta(hours=1),
        ):
            with self.subTest(created_at=created_at):
                link, _ = UserTelegramLink.objects.update_or_create(
                    user=self.player1.user,
                    defaults={
                        "binding_token": "expired-token",
                        "token_created_at": created_at,
                        "user_bot_chat_id": None,
                    },
                )
                self.assertEqual(
                    self._post(self._message("/start expired-token")).status_code, 200
                )
                link.refresh_from_db()
                self.assertIsNone(link.user_bot_chat_id)

    def test_fresh_token_is_consumed_once(self) -> None:
        link = UserTelegramLink.objects.create(
            user=self.player1.user,
            binding_token="fresh-token",
            token_created_at=timezone.now(),
        )
        self._post(self._message("/start fresh-token"))
        self._post(self._message("/start fresh-token", chat_id=1002))
        link.refresh_from_db()
        self.assertEqual(link.user_bot_chat_id, 1001)
        self.assertIsNone(link.binding_token)
        self.assertIsNone(link.token_created_at)

    def test_already_bound_chat_does_not_replace_another_account(self) -> None:
        owner = UserTelegramLink.objects.create(
            user=self.player2.user, user_bot_chat_id=1001
        )
        target = UserTelegramLink.objects.create(
            user=self.player1.user,
            binding_token="conflicting-token",
            token_created_at=timezone.now(),
        )
        response = self._post(self._message("/start conflicting-token"))
        self.assertEqual(response.status_code, 200)
        owner.refresh_from_db()
        target.refresh_from_db()
        self.assertEqual(owner.user_bot_chat_id, 1001)
        self.assertIsNone(target.user_bot_chat_id)
        self.assertEqual(target.binding_token, "conflicting-token")

    def test_sparring_result_entry_and_score_prompt_have_safe_context(self) -> None:
        UserTelegramLink.objects.create(user=self.player1.user, user_bot_chat_id=1001)
        match = self._sparring()
        for action in (f"result_enter_{match.pk}", f"result_type_{match.pk}_normal"):
            with self.subTest(action=action):
                self.assertEqual(self._post(self._callback(action)).status_code, 200)
                self.assertIn("Спарринг", self.send_message.call_args.args[1])

    def test_both_doubles_partners_see_their_match_in_both_menus(self) -> None:
        match = self._sparring(doubles=True)
        for player in (self.partner1, self.partner2):
            for action in ("menu_my_matches", "my_matches_sparring"):
                with self.subTest(player=player.pk, action=action):
                    views._handle_menu_callback_action(
                        1001, None, action, player.user, player, "https://tennisfan.ru/"
                    )
                    text = self.send_to_user.call_args.args[1]
                    self.assertNotIn("Нет предстоящих матчей", text)
                    markup = self.send_to_user.call_args.kwargs["reply_markup"]
                    if action == "my_matches_sparring":
                        self.assertIn(f"result_enter_{match.pk}", str(markup))

    def test_partner1_result_keeps_the_correct_side(self) -> None:
        """Партнёр вводит счёт после фиксации выбора матча в предыдущем update."""
        UserTelegramLink.objects.create(user=self.partner1.user, user_bot_chat_id=1001)
        match = self._sparring(doubles=True)
        with self.captureOnCommitCallbacks(execute=True):
            response = self._post(self._callback(f"result_type_{match.pk}_normal"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(cache.get(views.CACHE_KEY_RESULT_ENTRY % 1001), match.pk)
        with self.captureOnCommitCallbacks(execute=True):
            response = self._post(self._message("6:4 6:3"))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(cache.get(views.CACHE_KEY_RESULT_ENTRY % 1001))
        match.refresh_from_db()
        self.assertEqual(match.winner_id, self.player1.pk)
        self.assertEqual((match.player1_set1, match.player2_set1), (6, 4))

    def test_invalid_tennis_score_does_not_complete_match(self) -> None:
        UserTelegramLink.objects.create(user=self.player1.user, user_bot_chat_id=1001)
        match = self._sparring()
        cache.set(views.CACHE_KEY_RESULT_ENTRY % 1001, match.pk)
        self._post(self._message("20:1 20:1"))
        match.refresh_from_db()
        self.assertEqual(match.status, Match.MatchStatus.SCHEDULED)
        self.assertFalse(match.result_proposals.exists())

    def test_callback_from_group_or_other_sender_cannot_act_as_private_user(
        self,
    ) -> None:
        UserTelegramLink.objects.create(user=self.player1.user, user_bot_chat_id=1001)
        match = self._sparring()
        update = self._callback(f"result_type_{match.pk}_walkover_win")
        update["callback_query"]["message"]["chat"]["type"] = "group"
        self._post(update)
        update["callback_query"]["message"]["chat"]["type"] = "private"
        update["callback_query"]["from"]["id"] = 1002
        self._post(update)
        match.refresh_from_db()
        self.assertEqual(match.status, Match.MatchStatus.SCHEDULED)
        self.assertFalse(match.result_proposals.exists())
