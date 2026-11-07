"""Раздельные открытый и подписной чаты без небезопасных запасных ID."""

from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from apps.core.models import UserTelegramLink
from apps.subscriptions.models import SubscriptionTier, UserSubscription
from apps.telegram_bot import services, views
from apps.telegram_bot.private_chat import get_private_chat_access_status
from tests.support.factories import make_player


@override_settings(
    TELEGRAM_PUBLIC_COMMUNITY_URL="https://t.me/open_test_community",
    TELEGRAM_PUBLIC_COMMUNITY_CHAT_ID="-100100",
    TELEGRAM_PRIVATE_COMMUNITY_CHAT_ID="-100200",
    TELEGRAM_PRIVATE_CHAT_ACCESS_ENABLED=False,
)
class TelegramCommunityTests(TestCase):
    """Открытая ссылка не зависит от тарифа, закрытый чат включается явно."""

    def setUp(self) -> None:
        self.update_id = 0
        self.player = make_player(email_suffix="tg-community")
        UserTelegramLink.objects.create(user=self.player.user, user_bot_chat_id=1001)

    def test_private_chat_is_not_configured_without_explicit_enable(self) -> None:
        self.assertFalse(services.is_private_chat_configured())
        with patch("apps.telegram_bot.services._api_post") as api:
            self.assertIsNone(services.create_private_chat_invite_link())
            self.assertFalse(services.kick_from_private_chat(1001))
        api.assert_not_called()

    def test_private_chat_requires_distinct_valid_public_and_private_ids(self) -> None:
        with override_settings(TELEGRAM_PRIVATE_CHAT_ACCESS_ENABLED=True):
            self.assertTrue(services.is_private_chat_configured())
            for private_id in ("", "invalid", "1001", "-100100"):
                with self.subTest(private_id=private_id):
                    with override_settings(
                        TELEGRAM_PRIVATE_COMMUNITY_CHAT_ID=private_id
                    ):
                        self.assertFalse(services.is_private_chat_configured())
            with override_settings(TELEGRAM_PUBLIC_COMMUNITY_CHAT_ID=""):
                self.assertFalse(services.is_private_chat_configured())

    def test_open_chat_menu_never_claims_paid_access_or_creates_private_invite(
        self,
    ) -> None:
        with patch(
            "apps.telegram_bot.views.bot.send_to_user", return_value=True
        ) as send:
            with patch(
                "apps.telegram_bot.services.create_private_chat_invite_link"
            ) as invite:
                views._handle_menu_callback_action(
                    1001, None, "menu_private_chat", self.player.user, self.player
                )
        text = send.call_args.args[1]
        self.assertIn("Открытый", text)
        self.assertNotIn("Доступ подтверждён", text)
        self.assertNotIn("Закрытый канал", text)
        self.assertIn(
            "https://t.me/open_test_community",
            str(send.call_args.kwargs["reply_markup"]),
        )
        invite.assert_not_called()

    def test_missing_public_url_never_falls_back_to_paid_chat(self) -> None:
        with override_settings(TELEGRAM_PUBLIC_COMMUNITY_URL=""):
            with patch(
                "apps.telegram_bot.views.bot.send_to_user", return_value=True
            ) as send:
                with patch(
                    "apps.telegram_bot.services.create_private_chat_invite_link"
                ) as invite:
                    views._handle_menu_callback_action(
                        1001, None, "menu_private_chat", self.player.user, self.player
                    )
        self.assertNotIn("t.me/", str(send.call_args))
        invite.assert_not_called()

    @override_settings(TELEGRAM_PRIVATE_CHAT_ACCESS_ENABLED=True)
    def test_invite_and_removal_use_one_configured_private_id(self) -> None:
        with patch(
            "apps.telegram_bot.services._api_post",
            return_value=({"invite_link": "https://t.me/+test"}, True),
        ) as api:
            services.create_private_chat_invite_link()
            services.get_private_chat_member_status(1001)
            services.kick_from_private_chat(1001)
        self.assertTrue(api.call_args_list)
        for call in api.call_args_list:
            self.assertEqual(call.args[1]["chat_id"], -100200)
        self.assertTrue(api.call_args_list[0].args[1]["creates_join_request"])
        self.assertNotIn("member_limit", api.call_args_list[0].args[1])

    @override_settings(TELEGRAM_PRIVATE_CHAT_ACCESS_ENABLED=True)
    def test_unknown_status_and_chat_admins_are_never_forcibly_removed(self) -> None:
        for status in (None, "administrator", "creator"):
            with self.subTest(status=status):
                with patch(
                    "apps.telegram_bot.services.is_configured", return_value=True
                ):
                    with patch(
                        "apps.telegram_bot.management.commands.sync_private_chat_access.user_has_private_chat_access",
                        return_value=False,
                    ):
                        with patch(
                            "apps.telegram_bot.services.get_private_chat_member_status",
                            return_value=status,
                        ):
                            with patch(
                                "apps.telegram_bot.services.kick_from_private_chat"
                            ) as kick:
                                call_command("sync_private_chat_access")
                kick.assert_not_called()

    def test_sync_disabled_never_checks_or_removes_members(self) -> None:
        with patch("apps.telegram_bot.services.is_configured", return_value=True):
            with patch(
                "apps.telegram_bot.services.get_private_chat_member_status"
            ) as status:
                with patch("apps.telegram_bot.services.kick_from_private_chat") as kick:
                    call_command("sync_private_chat_access")
        status.assert_not_called()
        kick.assert_not_called()

    def _grant_access(self) -> UserSubscription:
        """Выдать действующий тариф закрытого сообщества."""
        tier = SubscriptionTier.objects.create(
            name="telegram-community", has_private_chat=True
        )
        return UserSubscription.objects.update_or_create(
            user=self.player.user,
            defaults={
                "tier": tier,
                "is_active": True,
                "end_date": timezone.now() + timedelta(days=30),
            },
        )[0]

    @override_settings(TELEGRAM_PRIVATE_CHAT_ACCESS_ENABLED=True)
    def test_subscriber_menu_checks_current_tier_before_creating_invite(self) -> None:
        """Открытая ссылка никогда не используется как вход в подписной чат."""
        with patch(
            "apps.telegram_bot.views.bot.send_to_user", return_value=True
        ) as send:
            with patch(
                "apps.telegram_bot.services.create_private_chat_invite_link",
                return_value="https://t.me/+paid",
            ) as invite:
                views._handle_menu_callback_action(
                    1001, None, "menu_subscriber_chat", self.player.user, self.player
                )
                invite.assert_not_called()
                self.assertIn("тариф", send.call_args.args[1].lower())
                self._grant_access()
                views._handle_menu_callback_action(
                    1001, None, "menu_subscriber_chat", self.player.user, self.player
                )
                invite.assert_called_once()
                self.assertIn("заявк", send.call_args.args[1].lower())
                self.assertIn("https://t.me/+paid", str(send.call_args))
                self.assertNotIn(
                    "https://t.me/open_test_community", str(send.call_args)
                )

    def test_access_reads_fresh_subscription_and_does_not_reactivate_it(self) -> None:
        """Устаревший объект подписки не обходит отзыв доступа и не меняет тариф."""
        subscription = self._grant_access()
        _ = self.player.user.subscription
        UserSubscription.objects.filter(pk=subscription.pk).update(is_active=False)
        allowed, _ = get_private_chat_access_status(self.player.user)
        self.assertFalse(allowed)
        subscription.refresh_from_db()
        self.assertFalse(subscription.is_active)
        self.assertEqual(subscription.tier.name, "telegram-community")

    def _join(self, *, chat_id: int = -100200, user_id: int = 1001) -> None:
        """Отправить реальный webhook заявки без внешней сети."""
        self.update_id += 1
        request = RequestFactory().post(
            "/telegram/user-bot-webhook/",
            data=json.dumps(
                {
                    "update_id": self.update_id,
                    "chat_join_request": {
                        "chat": {"id": chat_id, "type": "supergroup"},
                        "from": {"id": user_id},
                        "user_chat_id": 9999,
                        "date": 1,
                    },
                }
            ),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="community-secret",
        )
        self.assertEqual(views.user_bot_webhook(request).status_code, 200)

    @override_settings(
        TELEGRAM_PRIVATE_CHAT_ACCESS_ENABLED=True,
        TELEGRAM_USER_BOT_WEBHOOK_SECRET="community-secret",
    )
    def test_join_request_approves_only_currently_bound_subscriber(self) -> None:
        """Пересланное приглашение не даёт доступ неподписанному Telegram-аккаунту."""
        subscription = self._grant_access()
        with patch(
            "apps.telegram_bot.services.resolve_private_chat_join_request",
            return_value=True,
        ) as resolve:
            self._join()
            resolve.assert_called_once_with(1001, approve=True)
            resolve.reset_mock()
            self._join(user_id=2002)
            resolve.assert_called_once_with(2002, approve=False)
            resolve.reset_mock()
            UserSubscription.objects.filter(pk=subscription.pk).update(
                end_date=timezone.now()
            )
            self._join()
            resolve.assert_called_once_with(1001, approve=False)

    @override_settings(TELEGRAM_USER_BOT_WEBHOOK_SECRET="community-secret")
    def test_join_request_ignores_public_chat_and_disabled_private_chat(self) -> None:
        """Webhook не модерирует другие сообщества или отключённый подписной чат."""
        with patch(
            "apps.telegram_bot.services.resolve_private_chat_join_request"
        ) as resolve:
            self._join()
            with override_settings(TELEGRAM_PRIVATE_CHAT_ACCESS_ENABLED=True):
                self._join(chat_id=-100100)
                self._join(chat_id=-100300)
        resolve.assert_not_called()

    @override_settings(TELEGRAM_PRIVATE_CHAT_ACCESS_ENABLED=True)
    def test_sync_rechecks_binding_and_renewal_after_member_lookup(self) -> None:
        """Сетевой запрос не превращает свежую перепривязку или оплату в удаление."""
        command_access = "apps.telegram_bot.management.commands.sync_private_chat_access.user_has_private_chat_access"
        for changed_binding in (True, False):
            with self.subTest(changed_binding=changed_binding):
                UserTelegramLink.objects.filter(user=self.player.user).update(
                    user_bot_chat_id=1001
                )

                def lookup(
                    user_chat_id: int, changed_binding: bool = changed_binding
                ) -> str:
                    if changed_binding:
                        UserTelegramLink.objects.filter(user=self.player.user).update(
                            user_bot_chat_id=2002
                        )
                    return "member"

                with patch(
                    "apps.telegram_bot.services.is_configured", return_value=True
                ):
                    with patch(command_access, side_effect=[False, True]):
                        with patch(
                            "apps.telegram_bot.services.get_private_chat_member_status",
                            side_effect=lookup,
                        ):
                            with patch(
                                "apps.telegram_bot.services.kick_from_private_chat"
                            ) as kick:
                                call_command("sync_private_chat_access")
                kick.assert_not_called()
