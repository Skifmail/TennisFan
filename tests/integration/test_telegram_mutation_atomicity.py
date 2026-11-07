"""Свежие состояния и откат изменяющих действий спарринга и продления дедлайна."""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

from django.contrib import admin
from django.core import mail
from django.core.cache import cache
from django.http import JsonResponse
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.models import PlatformActivityEvent, UserTelegramLink
from apps.notifications.models import Delivery
from apps.sparring.doubles_services import (
    accept_join_request,
    create_doubles_request,
    create_join_request,
)
from apps.sparring.models import (
    DoublesMatchRequestStatus,
    SparringRequest,
    SparringResponse,
    TeamSide,
)
from apps.sparring.services import confirm_sparring_response
from apps.telegram_bot import views
from apps.telegram_bot.admin import (
    DeadlineExtensionRequestProxyAdmin,
    approve_extension_action,
)
from apps.telegram_bot.models import (
    DeadlineExtensionRequestProxy,
    TelegramUpdateReceipt,
)
from apps.tournaments.models import DeadlineExtensionRequest, Match
from apps.users.models import Player
from tests.support.factories import make_player, make_tournament


@override_settings(
    TELEGRAM_USER_BOT_WEBHOOK_SECRET="mutation-secret",
    SECURE_SSL_REDIRECT=False,
    ALLOWED_HOSTS=["testserver"],
    NOTIFICATION_CENTER_ENABLED=False,
    USER_NOTIFICATIONS_EMAIL_ENABLED=True,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class TelegramMutationAtomicityTests(TestCase):
    """Проверки настоящих обработчиков без внешней сети."""

    def setUp(self) -> None:
        cache.clear()
        self.update_id = 0
        self._mock("apps.telegram_bot.views.bot.send_message", return_value=(1, True))
        self._mock("apps.telegram_bot.views.bot.send_to_user", return_value=True)
        self._mock("apps.telegram_bot.views._answer_callback")
        self._mock("apps.telegram_bot.views._edit_message_remove_reply_markup")
        self._mock("apps.core.telegram_notify.send_admin_message", return_value=True)
        self._mock("apps.sparring.views.user_has_sparring_access", return_value=True)
        self._mock("apps.sparring.utils.user_has_sparring_access", return_value=True)
        self._mock("apps.telegram_bot.admin.messages.success")
        self._mock("apps.telegram_bot.admin.messages.warning")
        self.extension_admin = DeadlineExtensionRequestProxyAdmin(
            DeadlineExtensionRequestProxy, admin.site
        )
        self.player1 = make_player(email_suffix="mutation-1")
        self.player2 = make_player(email_suffix="mutation-2")
        self.link = UserTelegramLink.objects.create(
            user=self.player1.user, user_bot_chat_id=1001
        )
        self.tournament = make_tournament(slug="mutation-tournament")
        self.match = Match.objects.create(
            tournament=self.tournament,
            player1=self.player1,
            player2=self.player2,
            deadline=timezone.now() + timedelta(days=2),
        )
        Delivery.objects.all().delete()
        mail.outbox.clear()

    def _mock(self, target: str, **kwargs: Any) -> MagicMock:
        """Ограничить подмену внешнего взаимодействия одним тестом."""
        patcher = patch(target, **kwargs)
        self.addCleanup(patcher.stop)
        mocked = patcher.start()
        assert isinstance(mocked, MagicMock)
        return mocked

    def _callback(self, action: str) -> JsonResponse:
        """Вызвать реальный webhook с уникальным обновлением и явной привязкой."""
        self.update_id += 1
        request = RequestFactory().post(
            "/telegram/user-bot-webhook/",
            data=json.dumps(
                {
                    "update_id": self.update_id,
                    "callback_query": {
                        "id": "mutation-callback",
                        "from": {"id": 1001},
                        "data": action,
                        "message": {
                            "message_id": 1,
                            "date": 1,
                            "chat": {"id": 1001, "type": "private"},
                        },
                    },
                }
            ),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="mutation-secret",
        )
        return views.user_bot_webhook(request)

    def _response(
        self, *, status: str = SparringResponse.ResponseStatus.PENDING
    ) -> SparringResponse:
        """Сохранить одиночную заявку с откликом второго игрока."""
        request = SparringRequest.objects.create(
            player=self.player1, city="Москва", description="Игра"
        )
        return SparringResponse.objects.create(
            sparring_request=request,
            respondent=self.player2,
            contact_method="telegram",
            status=status,
        )

    def test_extension_request_does_not_use_match_cancelled_after_initial_read(
        self,
    ) -> None:
        def cancel_before_link_read(chat_id: int | None) -> UserTelegramLink:
            """Имитировать отмену после чтения матча обработчиком."""
            Match.objects.filter(pk=self.match.pk).update(
                status=Match.MatchStatus.CANCELLED
            )
            return self.link

        with patch(
            "apps.telegram_bot.views._get_link_by_chat_id",
            side_effect=cancel_before_link_read,
        ):
            response = self._callback(f"extension_request_{self.match.pk}")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.match.deadline_extension_requests.exists())

    def test_extension_approval_ignores_stale_already_processed_request(self) -> None:
        extension = DeadlineExtensionRequestProxy.objects.create(
            match=self.match, requested_by=self.player1
        )
        old_deadline = self.match.deadline
        DeadlineExtensionRequest.objects.filter(pk=extension.pk).update(
            status=DeadlineExtensionRequest.Status.APPROVED
        )
        queryset = DeadlineExtensionRequestProxy.objects.filter(pk=extension.pk)
        with patch.object(queryset, "filter", return_value=[extension]):
            approve_extension_action(
                self.extension_admin, RequestFactory().post("/admin/"), queryset
            )
        self.assertEqual(Match.objects.get(pk=self.match.pk).deadline, old_deadline)

    def test_extension_failure_rolls_back_deadline_with_request(self) -> None:
        extension = DeadlineExtensionRequestProxy.objects.create(
            match=self.match, requested_by=self.player1
        )
        old_deadline = self.match.deadline
        with patch(
            "apps.tournaments.models.DeadlineExtensionRequest.save",
            side_effect=RuntimeError("history failed"),
        ):
            with self.assertRaises(RuntimeError):
                approve_extension_action(
                    self.extension_admin,
                    RequestFactory().post("/admin/"),
                    DeadlineExtensionRequestProxy.objects.filter(pk=extension.pk),
                )
        self.assertEqual(Match.objects.get(pk=self.match.pk).deadline, old_deadline)
        self.assertEqual(
            DeadlineExtensionRequest.objects.get(pk=extension.pk).status,
            DeadlineExtensionRequest.Status.PENDING,
        )

    def test_extension_request_repeat_creates_one_pending_request(self) -> None:
        for _ in range(2):
            self.assertEqual(
                self._callback(f"extension_request_{self.match.pk}").status_code, 200
            )
        self.assertEqual(self.match.deadline_extension_requests.count(), 1)

    def test_site_cannot_accept_rejected_sparring_response(self) -> None:
        response = self._response(status=SparringResponse.ResponseStatus.REJECTED)
        self.client.force_login(self.player1.user)
        self.assertEqual(
            self.client.post(
                reverse("sparring_confirm_response", args=[response.pk])
            ).status_code,
            302,
        )
        self.assertFalse(Match.objects.filter(sparring_response=response).exists())

    def test_site_confirmation_failure_does_not_leave_match_or_notifications(
        self,
    ) -> None:
        response = self._response()
        events_before = PlatformActivityEvent.objects.count()
        self.client.force_login(self.player1.user)
        with patch(
            "apps.sparring.models.SparringResponse.save",
            side_effect=RuntimeError("response failed"),
        ):
            self.assertEqual(
                self.client.post(
                    reverse("sparring_confirm_response", args=[response.pk])
                ).status_code,
                302,
            )
        self.assertFalse(Match.objects.filter(sparring_response=response).exists())
        self.assertEqual(PlatformActivityEvent.objects.count(), events_before)
        self.assertFalse(Delivery.objects.exists())
        self.assertFalse(mail.outbox)

    def test_bot_confirmation_failure_is_retryable_and_atomic(self) -> None:
        response = self._response()
        with patch(
            "apps.sparring.models.SparringResponse.save",
            side_effect=RuntimeError("response failed"),
        ):
            result = self._callback(f"confirm_match_{response.pk}")
        self.assertEqual(result.status_code, 503)
        self.assertFalse(Match.objects.filter(sparring_response=response).exists())
        self.assertFalse(TelegramUpdateReceipt.objects.exists())
        self.assertFalse(Delivery.objects.exists())

    def test_site_and_bot_share_one_confirmation_without_duplicate_match(self) -> None:
        response = self._response()
        self.client.force_login(self.player1.user)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("sparring_confirm_response", args=[response.pk]))
            self.assertEqual(
                self._callback(f"confirm_match_{response.pk}").status_code, 200
            )
        self.assertEqual(Match.objects.filter(sparring_response=response).count(), 1)

    def _prepare_respondent_profile(self) -> None:
        """Подготовить проверенный профиль для настоящего сайта откликов."""
        user = self.player2.user
        user.first_name = "Игрок"
        user.last_name = "Отклик"
        user.phone = "+79990001002"
        user.email_verified = True
        user.save(update_fields=["first_name", "last_name", "phone", "email_verified"])
        Player.objects.filter(pk=self.player2.pk).update(
            birth_date=date(1990, 1, 1), is_verified=True
        )
        self.client.force_login(user)

    def test_site_repeat_response_cannot_reset_accepted_response_after_reopen(
        self,
    ) -> None:
        response = self._response()
        match = confirm_sparring_response(response.pk, self.player1.user_id)
        SparringRequest.objects.filter(pk=response.sparring_request_id).update(
            status=SparringRequest.Status.ACTIVE
        )
        self._prepare_respondent_profile()
        result = self.client.post(
            reverse("sparring_respond", args=[response.sparring_request_id]),
            {"method": "telegram"},
            content_type="application/json",
        )
        self.assertEqual(result.status_code, 409)
        response.refresh_from_db()
        self.assertEqual(response.status, SparringResponse.ResponseStatus.ACCEPTED)
        self.assertEqual(response.matches.get().pk, match.pk)

    def test_site_response_rechecks_request_closed_after_initial_read(self) -> None:
        response = self._response()
        self._prepare_respondent_profile()

        def close_after_read(*args: Any, **kwargs: Any) -> None:
            """Имитировать закрытие заявки после первоначальной проверки сайта."""
            SparringRequest.objects.filter(pk=response.sparring_request_id).update(
                status=SparringRequest.Status.CLOSED
            )

        with patch(
            "apps.sparring.views._padel_entry_redirect", side_effect=close_after_read
        ):
            result = self.client.post(
                reverse("sparring_respond", args=[response.sparring_request_id]),
                {"method": "telegram"},
                content_type="application/json",
            )
        self.assertEqual(result.status_code, 409)
        self.assertEqual(
            SparringResponse.objects.get(pk=response.pk).status,
            SparringResponse.ResponseStatus.PENDING,
        )

    def test_cancel_or_delete_request_cannot_erase_created_match_context(self) -> None:
        response = self._response()
        match = confirm_sparring_response(response.pk, self.player1.user_id)
        self.client.force_login(self.player1.user)
        for endpoint in ("sparring_cancel", "sparring_delete"):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(
                    self.client.post(
                        reverse(endpoint, args=[response.sparring_request_id])
                    ).status_code,
                    302,
                )
                self.assertTrue(
                    SparringRequest.objects.filter(
                        pk=response.sparring_request_id
                    ).exists()
                )
                self.assertEqual(
                    Match.objects.get(pk=match.pk).sparring_response_id, response.pk
                )

    def test_bot_team_failure_is_retryable_and_rolls_back_all_five_matches(
        self,
    ) -> None:
        partner = make_player(email_suffix="mutation-partner")
        opponent_partner = make_player(email_suffix="mutation-opponent-partner")
        request = create_doubles_request(
            created_by=self.player1, partner=partner, kind="team"
        )
        join = create_join_request(
            match_request_id=request.pk,
            created_by=self.player2,
            target_side=TeamSide.OPPONENT,
            players=[self.player2, opponent_partner],
        )
        accept_join_request(
            match_request_id=request.pk,
            join_request_id=join.pk,
            accepted_by=self.player1,
        )
        Delivery.objects.all().delete()
        mail.outbox.clear()
        events_before = PlatformActivityEvent.objects.count()
        with patch(
            "apps.sparring.models.DoublesMatchRequest.save",
            side_effect=RuntimeError("series failed"),
        ):
            result = self._callback(f"team_sparring_generate_{request.pk}")
        self.assertEqual(result.status_code, 503)
        request.refresh_from_db()
        self.assertEqual(request.status, DoublesMatchRequestStatus.READY)
        self.assertFalse(request.series_matches.exists())
        self.assertFalse(TelegramUpdateReceipt.objects.exists())
        self.assertFalse(Delivery.objects.exists())
        self.assertEqual(PlatformActivityEvent.objects.count(), events_before)
        self.assertFalse(mail.outbox)

    def test_result_input_selection_is_not_saved_when_callback_rolls_back(self) -> None:
        key = views.CACHE_KEY_RESULT_ENTRY % 1001
        cache.set(key, -1)
        with patch(
            "apps.telegram_bot.views.bot.send_message",
            side_effect=RuntimeError("reply failed"),
        ):
            result = self._callback(f"result_type_{self.match.pk}_normal")
        self.assertEqual(result.status_code, 503)
        self.assertEqual(cache.get(key), -1)

    def test_result_cancel_keeps_input_when_reply_fails(self) -> None:
        key = views.CACHE_KEY_RESULT_ENTRY % 1001
        cache.set(key, self.match.pk)
        payload = {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "date": 1,
                "from": {"id": 1001},
                "chat": {"id": 1001, "type": "private"},
                "text": "/cancel",
            },
        }
        request = RequestFactory().post(
            "/telegram/user-bot-webhook/",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="mutation-secret",
        )
        with patch(
            "apps.telegram_bot.views.bot.send_message",
            side_effect=RuntimeError("reply failed"),
        ):
            self.assertEqual(views.user_bot_webhook(request).status_code, 503)
        self.assertEqual(cache.get(key), self.match.pk)

    def test_menu_profile_failure_is_not_confirmed_as_success(self) -> None:
        with patch(
            "apps.telegram_bot.views._handle_menu_callback_action",
            side_effect=RuntimeError("menu failed"),
        ):
            self.assertEqual(self._callback("menu_my_matches").status_code, 503)
        self.assertFalse(TelegramUpdateReceipt.objects.exists())

    def test_failed_message_return_is_retryable_and_does_not_change_input(self) -> None:
        """Неуспешный транспорт возвращает False, а не обязательно исключение."""
        key = views.CACHE_KEY_RESULT_ENTRY % 1001
        cache.set(key, -1)
        with patch(
            "apps.telegram_bot.views.bot.send_message", return_value=(None, False)
        ):
            self.assertEqual(
                self._callback(f"result_type_{self.match.pk}_normal").status_code, 503
            )
        self.assertFalse(TelegramUpdateReceipt.objects.exists())
        self.assertEqual(cache.get(key), -1)

    def test_failed_menu_send_is_retryable(self) -> None:
        """HTTP 200 от webhook не должен скрывать отказ отправки меню."""
        with patch("apps.telegram_bot.views.bot.send_to_user", return_value=False):
            self.assertEqual(self._callback("menu_my_matches").status_code, 503)
        self.assertFalse(TelegramUpdateReceipt.objects.exists())

    def test_subscription_read_failure_does_not_look_like_missing_subscription(
        self,
    ) -> None:
        """Сбой БД при чтении тарифа не подтверждается как отсутствие подписки."""
        with patch(
            "apps.subscriptions.services.ensure_current_subscription",
            side_effect=RuntimeError("subscription read failed"),
        ):
            self.assertEqual(self._callback("menu_my_subscription").status_code, 503)
            self.assertEqual(self._callback("menu_my_profile").status_code, 503)
        self.assertFalse(TelegramUpdateReceipt.objects.exists())
