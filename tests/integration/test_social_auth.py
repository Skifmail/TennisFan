"""Проверки социальной регистрации, привязки и защиты аккаунтов."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

import requests
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.models import LegalAcceptanceLog, UserConsent
from apps.users.models import (
    Player,
    SocialAuthFlow,
    SocialIdentity,
    User,
)
from apps.users.social_oauth import SocialAuthError, fetch_identity, get_provider

PROVIDERS = {
    key: {
        "enabled": True,
        "client_id": f"{key}-app",
        "client_secret": "test-secret",
        "redirect_uri": f"https://testserver/users/social/{key}/callback/",
    }
    for key in ("yandex", "vk")
}
IDENTITY = {
    "subject": "123",
    "email": "new@test.local",
    "first_name": "Иван",
    "last_name": "Тестов",
    "phone": "",
}


def payload(**changes: Any) -> dict[str, Any]:
    """Собрать обязательные данные завершения регистрации."""
    data = {
        "email": "new@test.local",
        "first_name": "Иван",
        "last_name": "Тестов",
        "phone": "+79991234567",
        "city": "Москва",
        "ntrp_level": "3,75",
        "agree_legal": "on",
    }
    data.update(changes)
    return data


@override_settings(
    SOCIAL_AUTH_PROVIDERS=PROVIDERS,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    TELEGRAM_BOT_TOKEN="",
    TELEGRAM_ADMIN_CHAT_IDS=[],
)
class SocialAuthTests(TestCase):
    """Регистрация сразу из соцсети и защита существующих аккаунтов."""

    def setUp(self) -> None:
        cache.clear()
        self.initial_user_count = User.objects.count()
        self.notify = patch("apps.users.social_views._send_registration_notifications")
        self.notify.start()
        self.addCleanup(self.notify.stop)

    def start(self, provider: str = "yandex", **data: str) -> SocialAuthFlow:
        """Начать HTTP-сеанс входа с PKCE."""
        response = self.client.post(
            reverse("social_start", args=[provider]), data, secure=True
        )
        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlparse(response.url).query)
        self.assertEqual(query["code_challenge_method"], ["S256"])
        return SocialAuthFlow.objects.get(state=query["state"][0])

    def callback(
        self, flow: SocialAuthFlow, identity: dict[str, str] | None = None
    ) -> Any:
        """Подтвердить профиль у провайдера без внешнего запроса."""
        with patch(
            "apps.users.social_views.fetch_identity",
            return_value=identity if identity is not None else IDENTITY,
        ):
            return self.client.get(
                reverse("social_callback", args=[flow.provider]),
                {"code": "code", "state": flow.state, "device_id": "device"},
                secure=True,
            )

    def test_registers_and_logs_in_without_form_or_strength(self) -> None:
        flow = self.start(next="/tournaments/")
        response = self.callback(flow)
        self.assertEqual(response.url, "/tournaments/")
        user = User.objects.get(email="new@test.local")
        self.assertEqual(str(self.client.session["_auth_user_id"]), str(user.pk))
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.email_verified)
        self.assertFalse(user.player.tennis_entry_level_set)
        self.assertFalse(user.player.is_verified)
        self.assertEqual(user.player.total_points, 0)
        self.assertEqual(LegalAcceptanceLog.objects.filter(user=user).count(), 2)
        self.assertEqual(UserConsent.objects.filter(user=user).count(), 2)
        self.assertFalse(SocialAuthFlow.objects.filter(pk=flow.pk).exists())

    def test_registers_multiple_users_without_email_and_other_data(self) -> None:
        for subject in ("one", "two"):
            self.client = Client()
            response = self.callback(self.start(), {"subject": subject})
            self.assertEqual(response.url, reverse("home"))
        self.assertEqual(User.objects.filter(email__isnull=True).count(), 2)
        self.assertEqual(
            SocialIdentity.objects.filter(subject__in=["one", "two"]).count(), 2
        )

    def test_available_profile_data_and_avatar_are_imported(self) -> None:
        identity = {
            **IDENTITY,
            "phone": "+79991234567",
            "birth_date": "1990-05-24",
            "gender": "female",
            "avatar_url": "https://avatars.yandex.net/get-yapic/123/islands-200",
        }
        with (
            patch(
                "apps.users.social_views.import_social_avatar", return_value=True
            ) as avatar,
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.callback(self.start(), identity)
        user = User.objects.get(email="new@test.local")
        self.assertEqual(user.player.birth_date.isoformat(), "1990-05-24")
        self.assertEqual(user.player.gender, "female")
        self.assertEqual(user.phone, "+79991234567")
        avatar.assert_called_once_with(user.player.pk, "yandex", identity["avatar_url"])

    def test_duplicate_email_does_not_merge_or_create_account(self) -> None:
        owner = User.objects.create_user(
            email="NEW@test.local", password="StrongPass123!"
        )
        response = self.callback(self.start())
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertFalse(SocialIdentity.objects.exists())
        self.assertEqual(User.objects.get(email__iexact="new@test.local").pk, owner.pk)

    def test_repeated_login_keeps_same_profile_and_strength(self) -> None:
        self.callback(self.start())
        user = User.objects.get(email="new@test.local")
        Player.objects.filter(user=user).update(
            tennis_entry_level_set=True, ntrp_level=Decimal("4.25")
        )
        self.client = Client()
        self.callback(self.start(), {**IDENTITY, "first_name": "Другое"})
        user.refresh_from_db()
        self.assertEqual(user.first_name, "Иван")
        self.assertEqual(user.player.ntrp_level, Decimal("4.25"))
        self.assertEqual(User.objects.count(), self.initial_user_count + 1)

    def test_replaying_old_cookie_cannot_create_second_user(self) -> None:
        flow = self.start()
        old_cookies = self.client.cookies.copy()
        self.callback(flow)
        self.client = Client()
        self.client.cookies = old_cookies
        with patch("apps.users.social_views.fetch_identity") as fetch:
            self.client.get(
                reverse("social_callback", args=["yandex"]),
                {"state": flow.state, "code": "code"},
                secure=True,
            )
            fetch.assert_not_called()
        self.assertEqual(User.objects.count(), self.initial_user_count + 1)

    def test_wrong_state_expiry_and_other_browser_are_rejected(self) -> None:
        flow = self.start()
        with patch("apps.users.social_views.fetch_identity") as fetch:
            Client().get(
                reverse("social_callback", args=["yandex"]),
                {"state": flow.state, "code": "code"},
                secure=True,
            )
            self.client.get(
                reverse("social_callback", args=["yandex"]),
                {"state": "wrong", "code": "code"},
                secure=True,
            )
            fetch.assert_not_called()
        flow = self.start()
        SocialAuthFlow.objects.filter(pk=flow.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        self.callback(flow)
        self.assertEqual(User.objects.count(), self.initial_user_count)

    def test_cancel_and_provider_failure_do_not_create_users(self) -> None:
        flow = self.start()
        self.client.get(
            reverse("social_callback", args=["yandex"]),
            {"state": flow.state, "error": "access_denied"},
            secure=True,
        )
        flow = self.start()
        with patch(
            "apps.users.social_views.fetch_identity", side_effect=SocialAuthError
        ):
            self.client.get(
                reverse("social_callback", args=["yandex"]),
                {"state": flow.state, "code": "code"},
                secure=True,
            )
        self.assertEqual(User.objects.count(), self.initial_user_count)

    def test_inactive_user_cannot_login(self) -> None:
        user = User.objects.create_user(email="blocked@test.local", is_active=False)
        SocialIdentity.objects.create(user=user, provider="yandex", subject="123")
        self.callback(self.start())
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_start_requires_post_csrf_and_safe_next(self) -> None:
        self.assertEqual(self.start(next="https://evil.test/").next_url, "")
        self.assertEqual(
            self.client.get(
                reverse("social_start", args=["yandex"]), secure=True
            ).status_code,
            405,
        )
        self.assertEqual(
            Client(enforce_csrf_checks=True)
            .post(reverse("social_start", args=["yandex"]), secure=True)
            .status_code,
            403,
        )

    @override_settings(SOCIAL_AUTH_PROVIDERS={})
    def test_disabled_provider_is_not_available(self) -> None:
        self.assertEqual(
            self.client.post(
                reverse("social_start", args=["yandex"]), secure=True
            ).status_code,
            404,
        )
        self.assertNotContains(
            self.client.get(reverse("auth"), secure=True), "Войти через Яндекс"
        )

    def test_existing_user_connects_and_disconnects(self) -> None:
        user = User.objects.create_user(
            email="owner@test.local", password="StrongPass123!"
        )
        self.client.force_login(user)
        self.callback(self.start(process="connect"))
        self.assertTrue(SocialIdentity.objects.filter(user=user).exists())
        self.client.post(reverse("social_disconnect", args=["yandex"]), secure=True)
        self.assertFalse(SocialIdentity.objects.filter(user=user).exists())

    def test_other_users_identity_cannot_be_connected(self) -> None:
        owner = User.objects.create_user(email="owner@test.local")
        other = User.objects.create_user(email="other@test.local")
        SocialIdentity.objects.create(user=owner, provider="yandex", subject="123")
        self.client.force_login(other)
        self.callback(self.start(process="connect"))
        self.assertEqual(SocialIdentity.objects.get().user_id, owner.pk)

    def test_last_social_login_cannot_be_disconnected(self) -> None:
        user = User.objects.create_user(email="owner@test.local")
        SocialIdentity.objects.create(user=user, provider="yandex", subject="123")
        self.client.force_login(user)
        self.client.post(reverse("social_disconnect", args=["yandex"]), secure=True)
        self.assertTrue(SocialIdentity.objects.filter(user=user).exists())

    def test_password_requires_verified_email_and_cannot_replace_existing_password(
        self,
    ) -> None:
        user = User.objects.create_user(email="owner@test.local", email_verified=False)
        self.client.force_login(user)
        data = {"new_password1": "StrongPass123!", "new_password2": "StrongPass123!"}
        self.client.post(reverse("social_set_password"), data, secure=True)
        user.refresh_from_db()
        self.assertFalse(user.has_usable_password())
        user.email_verified = True
        user.save()
        self.client.post(reverse("social_set_password"), data, secure=True)
        user.refresh_from_db()
        self.assertTrue(user.check_password("StrongPass123!"))
        response = self.client.post(reverse("social_set_password"), data, secure=True)
        self.assertEqual(response.url, reverse("password_change"))


@override_settings(SOCIAL_AUTH_PROVIDERS=PROVIDERS)
class SocialProviderApiTests(TestCase):
    """Проверка контрактов API и отсутствия доверия данным callback."""

    def response(self, data: object) -> Mock:
        """Подготовить ответ HTTP-клиента."""
        return Mock(json=Mock(return_value=data))

    def test_yandex_fetches_profile_with_server_token(self) -> None:
        with patch(
            "apps.users.social_oauth.requests.request",
            side_effect=[
                self.response({"access_token": "token"}),
                self.response({"id": "321", "default_email": "real@test.local"}),
            ],
        ) as call:
            identity = fetch_identity(
                get_provider("yandex"), code="code", verifier="verifier", state="state"
            )
        self.assertEqual(identity["subject"], "321")
        self.assertEqual(identity["email"], "real@test.local")
        self.assertEqual(
            call.call_args_list[0].kwargs["data"]["code_verifier"], "verifier"
        )
        self.assertEqual(
            call.call_args_list[1].kwargs["headers"]["Authorization"], "OAuth token"
        )

    def test_vk_checks_state_subject_and_device_id(self) -> None:
        with patch(
            "apps.users.social_oauth.requests.request",
            side_effect=[
                self.response(
                    {"access_token": "token", "state": "state", "user_id": 123}
                ),
                self.response({"user": {"user_id": "123", "first_name": "Иван"}}),
            ],
        ) as call:
            identity = fetch_identity(
                get_provider("vk"),
                code="code",
                verifier="verifier",
                state="state",
                device_id="device",
            )
        self.assertEqual(identity["subject"], "123")
        self.assertEqual(call.call_args_list[0].kwargs["data"]["device_id"], "device")

    def test_vk_rejects_token_state_mismatch(self) -> None:
        with patch(
            "apps.users.social_oauth.requests.request",
            return_value=self.response({"access_token": "token", "state": "wrong"}),
        ):
            with self.assertRaises(SocialAuthError):
                fetch_identity(
                    get_provider("vk"),
                    code="code",
                    verifier="verifier",
                    state="state",
                    device_id="device",
                )

    def test_vk_rejects_profile_subject_mismatch(self) -> None:
        with patch(
            "apps.users.social_oauth.requests.request",
            side_effect=[
                self.response(
                    {"access_token": "token", "state": "state", "user_id": 123}
                ),
                self.response({"user": {"user_id": "456"}}),
            ],
        ):
            with self.assertRaises(SocialAuthError):
                fetch_identity(
                    get_provider("vk"),
                    code="code",
                    verifier="verifier",
                    state="state",
                    device_id="device",
                )

    def test_invalid_json_timeout_and_missing_subject_are_rejected(self) -> None:
        for response in (
            self.response([]),
            self.response({"error": "denied"}),
            Mock(json=Mock(side_effect=ValueError)),
        ):
            with patch(
                "apps.users.social_oauth.requests.request", return_value=response
            ):
                with self.assertRaises(SocialAuthError):
                    fetch_identity(
                        get_provider("yandex"),
                        code="code",
                        verifier="verifier",
                        state="state",
                    )
        with patch(
            "apps.users.social_oauth.requests.request", side_effect=requests.Timeout
        ):
            with self.assertRaises(SocialAuthError):
                fetch_identity(
                    get_provider("yandex"),
                    code="code",
                    verifier="verifier",
                    state="state",
                )
        with patch(
            "apps.users.social_oauth.requests.request",
            side_effect=[
                self.response({"access_token": "token"}),
                self.response({"first_name": "Иван"}),
            ],
        ):
            with self.assertRaises(SocialAuthError):
                fetch_identity(
                    get_provider("yandex"),
                    code="code",
                    verifier="verifier",
                    state="state",
                )
