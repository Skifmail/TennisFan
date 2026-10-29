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
        data.setdefault("process", "signup")
        page = self.client.post(
            reverse("social_start", args=[provider]), data, secure=True
        )
        self.assertEqual(page.status_code, 200)
        data = {
            **data,
            "token": page.context["form"]["token"].value(),
            "personal_data": "on",
            "terms": "on",
        }
        response = self.client.post(
            reverse("social_start", args=[provider]), data, secure=True
        )
        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlparse(response.url).query)
        self.last_start_query = query
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

    def login_flow(self, provider: str = "yandex", **data: str) -> SocialAuthFlow:
        """Начать обычный вход без регистрационного согласия."""
        response = self.client.post(
            reverse("social_start", args=[provider]), data, secure=True
        )
        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlparse(response.url).query)
        self.assertNotIn("optional_scope", query)
        self.assertNotIn("force_confirm", query)
        self.assertEqual(
            query["scope"],
            ["login:info" if provider == "yandex" else "vkid.personal_info"],
        )
        return SocialAuthFlow.objects.get(state=query["state"][0])

    def test_repeated_login_on_fresh_browsers_never_repeats_consents(self) -> None:
        self.callback(self.start())
        user = User.objects.get(email="new@test.local")
        count = LegalAcceptanceLog.objects.filter(user=user).count()
        for _ in range(3):
            self.client = Client()
            response = self.callback(self.login_flow(next="/tournaments/"))
            self.assertEqual(response.url, "/tournaments/")
            self.assertEqual(str(self.client.session["_auth_user_id"]), str(user.pk))
            self.assertEqual(
                LegalAcceptanceLog.objects.filter(user=user).count(), count
            )

    def test_linked_account_login_does_not_depend_on_document_availability(
        self,
    ) -> None:
        user = User.objects.create_user(email="existing-login@test.local")
        SocialIdentity.objects.create(user=user, provider="yandex", subject="123")
        with patch(
            "apps.users.social_views.load_documents", side_effect=OSError
        ) as documents:
            response = self.callback(self.login_flow())
        documents.assert_not_called()
        self.assertEqual(response.url, reverse("home"))
        self.assertFalse(LegalAcceptanceLog.objects.filter(user=user).exists())

    def test_vk_ok_and_mail_reuse_existing_identity_without_consents(self) -> None:
        user = User.objects.create_user(email="vk-login@test.local")
        SocialIdentity.objects.create(user=user, provider="vk", subject="123")
        for method in ("", "ok_ru", "mail_ru"):
            with self.subTest(method=method):
                self.client = Client()
                response = self.callback(
                    self.login_flow("vk", external_provider=method)
                )
                self.assertEqual(response.url, reverse("home"))
                self.assertEqual(
                    str(self.client.session["_auth_user_id"]), str(user.pk)
                )
        self.assertFalse(LegalAcceptanceLog.objects.filter(user=user).exists())

    def test_unknown_login_requests_signup_consent_without_creating_user(self) -> None:
        flow = self.login_flow("vk", external_provider="mail_ru", next="/tournaments/")
        response = self.callback(flow)
        self.assertEqual(response.url, reverse("social_registration_consent"))
        self.assertEqual(User.objects.count(), self.initial_user_count)
        self.assertFalse(SocialIdentity.objects.exists())
        self.assertFalse(LegalAcceptanceLog.objects.exists())
        flow.refresh_from_db()
        self.assertEqual(flow.identity, {"subject": "123"})
        page = self.client.get(response.url, secure=True)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Регистрация через Mail")
        self.assertFalse(page.context["form"]["personal_data"].value())
        self.assertContains(page, 'name="process" value="signup"')
        self.assertContains(page, 'name="external_provider" value="mail_ru"')
        self.assertContains(page, 'name="next" value="/tournaments/"')
        data = {
            "process": "signup",
            "external_provider": "mail_ru",
            "next": "/tournaments/",
            "token": page.context["form"]["token"].value(),
            "personal_data": "on",
            "terms": "on",
        }
        response = self.client.post(
            reverse("social_start", args=["vk"]), data, secure=True
        )
        full_flow = SocialAuthFlow.objects.get(
            state=parse_qs(urlparse(response.url).query)["state"][0]
        )
        self.assertFalse(full_flow.login_only)
        self.assertEqual(self.callback(full_flow).url, "/tournaments/")
        user = User.objects.get(email="new@test.local")
        self.assertEqual(LegalAcceptanceLog.objects.filter(user=user).count(), 3)

    def test_login_only_flow_cannot_bypass_registration_consent_through_match_page(
        self,
    ) -> None:
        flow = self.login_flow()
        self.callback(flow)
        self.client.post(reverse("social_signup"), {"choice": "no"}, secure=True)
        self.assertFalse(SocialIdentity.objects.exists())
        self.assertEqual(User.objects.count(), self.initial_user_count)

    def test_disconnected_account_requires_consent_to_connect_again(self) -> None:
        user = User.objects.create_user(
            email="connect-again@test.local", password="StrongPass123!"
        )
        SocialIdentity.objects.create(user=user, provider="yandex", subject="123")
        self.client.force_login(user)
        self.client.post(reverse("social_disconnect", args=["yandex"]), secure=True)
        page = self.client.post(
            reverse("social_start", args=["yandex"]),
            {"process": "connect"},
            secure=True,
        )
        self.assertEqual(page.status_code, 200)
        self.assertFalse(page.context["form"]["personal_data"].value())

    def test_registration_consent_requires_the_original_browser_session(self) -> None:
        flow = self.login_flow()
        self.callback(flow)
        response = Client().get(reverse("social_registration_consent"), secure=True)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(User.objects.count(), self.initial_user_count)

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
        self.assertEqual(LegalAcceptanceLog.objects.filter(user=user).count(), 3)
        self.assertEqual(UserConsent.objects.filter(user=user).count(), 0)
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

    def test_consent_is_required_before_provider_redirect(self) -> None:
        url = reverse("social_start", args=["yandex"])
        page = self.client.post(url, {"process": "signup"}, secure=True)
        self.assertEqual(page.status_code, 200)
        self.assertFalse(page.context["form"].is_bound)
        self.assertFalse(page.context["form"]["personal_data"].value())
        self.assertFalse(page.context["form"]["terms"].value())
        self.assertFalse(SocialAuthFlow.objects.exists())
        token = page.context["form"]["token"].value()
        for changes in (
            {},
            {"personal_data": "on"},
            {"terms": "on"},
            {"personal_data": "yes", "terms": "on"},
        ):
            response = self.client.post(url, {"token": token, **changes}, secure=True)
            self.assertEqual(response.status_code, 400)
            self.assertFalse(SocialAuthFlow.objects.exists())
            self.assertFalse(LegalAcceptanceLog.objects.exists())

    def test_signed_confirmation_cannot_move_between_browsers_or_providers(
        self,
    ) -> None:
        url = reverse("social_start", args=["yandex"])
        page = self.client.post(url, {"process": "signup"}, secure=True)
        data = {
            "token": page.context["form"]["token"].value(),
            "personal_data": "on",
            "terms": "on",
        }
        for client, target in (
            (Client(), url),
            (self.client, reverse("social_start", args=["vk"])),
        ):
            response = client.post(target, data, secure=True)
            self.assertEqual(response.status_code, 400)
            self.assertFalse(SocialAuthFlow.objects.exists())

    def test_document_change_requires_new_confirmation_and_allows_retry(self) -> None:
        from apps.users.social_consent import load_documents

        url = reverse("social_start", args=["yandex"])
        page = self.client.post(url, {"process": "signup"}, secure=True)
        data = {
            "token": page.context["form"]["token"].value(),
            "personal_data": "on",
            "terms": "on",
        }
        documents = load_documents()
        documents["terms"]["version"] = "changed"
        with patch("apps.users.social_views.load_documents", return_value=documents):
            response = self.client.post(url, data, secure=True)
            self.assertEqual(response.status_code, 400)
            self.assertFalse(SocialAuthFlow.objects.exists())
            self.assertContains(response, "Форма устарела", status_code=400)
            self.assertFalse(response.context["form"]["personal_data"].value())
            data["token"] = response.context["form"]["token"].value()
            self.assertEqual(self.client.post(url, data, secure=True).status_code, 302)

    def test_journal_keeps_confirmed_snapshot_and_does_not_accept_paid_offer(
        self,
    ) -> None:
        from apps.users.social_consent import load_documents

        flow = self.start()
        original = flow.legal_acceptance
        with patch("apps.users.social_views.load_documents", side_effect=OSError):
            self.callback(flow)
        entries = LegalAcceptanceLog.objects.filter(source="social_auth")
        self.assertEqual(
            set(entries.values_list("document_slug", flat=True)),
            {"social-personal-data", "terms", "privacy"},
        )
        for entry in entries:
            self.assertEqual(entry.metadata["confirmed_at"], original["confirmed_at"])
            self.assertEqual(
                entry.metadata["document_text"],
                original["documents"][entry.document_slug]["document_text"],
            )
            self.assertEqual(
                entry.document_version,
                original["documents"][entry.document_slug]["version"],
            )
        self.assertEqual(
            entries.get(document_slug="privacy").metadata["event"], "notice_presented"
        )
        self.assertFalse(
            UserConsent.objects.filter(consent_type="platform_offer").exists()
        )
        self.assertEqual(
            load_documents()["terms"]["version"],
            entries.get(document_slug="terms").document_version,
        )

    def test_legacy_flow_without_consent_cannot_fetch_profile(self) -> None:
        flow = self.start()
        SocialAuthFlow.objects.filter(pk=flow.pk).update(legal_acceptance={})
        with patch("apps.users.social_views.fetch_identity") as fetch:
            self.client.get(
                reverse("social_callback", args=[flow.provider]),
                {"code": "code", "state": flow.state},
                secure=True,
            )
        fetch.assert_not_called()
        self.assertFalse(SocialIdentity.objects.exists())

    def test_used_confirmation_cannot_start_another_flow(self) -> None:
        url = reverse("social_start", args=["yandex"])
        page = self.client.post(url, {"process": "signup"}, secure=True)
        data = {
            "token": page.context["form"]["token"].value(),
            "personal_data": "on",
            "terms": "on",
        }
        self.assertEqual(self.client.post(url, data, secure=True).status_code, 302)
        self.assertEqual(self.client.post(url, data, secure=True).status_code, 400)
        self.assertEqual(SocialAuthFlow.objects.count(), 1)

    def test_documents_unavailable_prevents_authorization(self) -> None:
        with patch("apps.users.social_views.load_documents", side_effect=OSError):
            response = self.client.post(
                reverse("social_start", args=["yandex"]),
                {"process": "signup"},
                secure=True,
            )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(SocialAuthFlow.objects.exists())

    def test_empty_token_returns_actionable_error_and_fresh_form(self) -> None:
        url = reverse("social_start", args=["yandex"])
        response = self.client.post(
            url, {"token": "", "personal_data": "on", "terms": "on"}, secure=True
        )
        self.assertContains(response, "Форма устарела", status_code=400)
        self.assertTrue(response.context["form"]["token"].value())
        self.assertFalse(response.context["form"]["personal_data"].value())
        self.assertFalse(SocialAuthFlow.objects.exists())

    def test_social_consent_is_publicly_available(self) -> None:
        response = self.client.get(reverse("legal_social_personal_data"), secure=True)
        self.assertContains(response, "СОГЛАСИЕ НА ОБРАБОТКУ ДАННЫХ")
        self.assertEqual(response.headers["Cache-Control"], "no-cache")

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

    def test_matching_phone_and_email_confirmation(self) -> None:
        owner = User.objects.create_user(
            email="new@test.local",
            password="StrongPass123!",
            first_name="Иван",
            last_name="Иванов",
            phone="8 (999) 123-45-67",
        )
        flow = self.start()
        response = self.callback(flow, {**IDENTITY, "phone": "+79991234567"})
        self.assertRedirects(
            response, reverse("social_signup"), fetch_redirect_response=False
        )
        page = self.client.get(reverse("social_signup"), secure=True)
        self.assertContains(page, "Иванов")
        self.assertNotIn("_auth_user_id", self.client.session)
        self.client.post(reverse("social_signup"), {"choice": "no"}, secure=True)
        new_user = User.objects.get(pk=self.client.session["_auth_user_id"])
        self.assertNotEqual(new_user.pk, owner.pk)
        self.assertIsNone(new_user.email)
        self.assertEqual(new_user.phone, "")
        self.assertFalse(SocialAuthFlow.objects.filter(pk=flow.pk).exists())
        owner.refresh_from_db()
        self.assertEqual(owner.phone, "8 (999) 123-45-67")

    def test_phone_only_match_yes_requires_old_login(self) -> None:
        User.objects.create_user(
            email="owner@test.local", password="StrongPass123!", phone="+79991234567"
        )
        flow = self.start("vk")
        self.callback(flow, {**IDENTITY, "phone": "8 999 123 45 67"})
        self.client.post(reverse("social_signup"), {"choice": "yes"}, secure=True)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertFalse(SocialIdentity.objects.exists())
        self.assertFalse(SocialAuthFlow.objects.filter(pk=flow.pk).exists())

    def test_match_choice_requires_session_and_csrf(self) -> None:
        User.objects.create_user(email="new@test.local", password="StrongPass123!")
        self.callback(self.start())
        stranger = Client()
        stranger.post(reverse("social_signup"), {"choice": "no"}, secure=True)
        self.assertFalse(SocialIdentity.objects.exists())
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.cookies = self.client.cookies
        response = csrf_client.post(
            reverse("social_signup"), {"choice": "no"}, secure=True
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(SocialIdentity.objects.exists())

    def test_external_vk_methods_use_shared_callback_and_identity(self) -> None:
        for external in ("ok_ru", "mail_ru"):
            with self.subTest(external=external):
                self.client = Client()
                flow = self.start("vk", external_provider=external)
                query = self.last_start_query
                self.assertEqual(query["provider"], [external])
                self.assertEqual(query["prompt"], ["login"])
                self.assertEqual(
                    query["redirect_uri"], [PROVIDERS["vk"]["redirect_uri"]]
                )
                self.callback(flow)
                self.assertIn("_auth_user_id", self.client.session)
        self.assertEqual(SocialIdentity.objects.filter(provider="vk").count(), 1)

    def test_external_vk_methods_visible_and_invalid_choice_rejected(self) -> None:
        response = self.client.get(reverse("login"), secure=True, follow=True)
        self.assertContains(response, "Войти через Одноклассники")
        self.assertContains(response, "Войти через Mail")
        for provider, external in (("vk", "evil"), ("yandex", "ok_ru")):
            response = self.client.post(
                reverse("social_start", args=[provider]),
                {"external_provider": external},
                secure=True,
            )
            self.assertEqual(response.status_code, 404)

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

    def test_profile_edit_shows_inline_connections_without_telegram(self) -> None:
        user = User.objects.create_user(
            email="settings@test.local", password="StrongPass123!"
        )
        Player.objects.create(user=user)
        SocialIdentity.objects.create(user=user, provider="yandex", subject="settings")
        self.client.force_login(user)
        with patch("apps.users.views._get_telegram_bot_connection_context") as telegram:
            response = self.client.get(reverse("profile_edit"), secure=True)
        telegram.assert_not_called()
        self.assertContains(response, 'id="profile-connections"')
        self.assertContains(response, "Подключено")
        self.assertContains(response, 'aria-label="Отключить Яндекс"')
        self.assertContains(response, 'aria-label="Подключить VK ID"')
        self.assertContains(response, 'name="external_provider" value="ok_ru"')
        self.assertContains(response, 'name="external_provider" value="mail_ru"')
        self.assertNotContains(response, "Telegram-бот")
        self.assertNotContains(response, 'id="telegram-connect-form"')
        states = {
            provider["key"]: provider["linked"]
            for provider in response.context["connection_providers"]
        }
        self.assertEqual(states, {"yandex": True, "vk": False})

    def test_profile_connection_returns_directly_to_profile(self) -> None:
        user = User.objects.create_user(
            email="inline@test.local", password="StrongPass123!"
        )
        self.client.force_login(user)
        target = reverse("profile_edit") + "#profile-connections"
        response = self.callback(self.start(process="connect", next=target))
        self.assertEqual(response.url, target)
        response = self.client.post(
            reverse("social_disconnect", args=["yandex"]), {"next": target}, secure=True
        )
        self.assertEqual(response.url, target)
        self.assertFalse(SocialIdentity.objects.filter(user=user).exists())

    def test_inline_connection_cancellation_returns_to_profile(self) -> None:
        user = User.objects.create_user(
            email="inline-cancel@test.local", password="StrongPass123!"
        )
        self.client.force_login(user)
        target = reverse("profile_edit") + "#profile-connections"
        flow = self.start(process="connect", next=target)
        response = self.client.get(
            reverse("social_callback", args=["yandex"]),
            {"state": flow.state, "error": "access_denied"},
            secure=True,
        )
        self.assertEqual(response.url, target)
        self.assertFalse(SocialIdentity.objects.filter(user=user).exists())

    def test_disconnect_rejects_external_return_address(self) -> None:
        user = User.objects.create_user(
            email="inline-safe@test.local", password="StrongPass123!"
        )
        SocialIdentity.objects.create(
            user=user, provider="yandex", subject="inline-safe"
        )
        self.client.force_login(user)
        response = self.client.post(
            reverse("social_disconnect", args=["yandex"]),
            {"next": "https://evil.test/"},
            secure=True,
        )
        self.assertEqual(response.url, reverse("social_connections"))

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

    def test_subject_only_login_does_not_return_profile_fields(self) -> None:
        for provider, token, profile in (
            (
                "yandex",
                {"access_token": "token"},
                {
                    "id": "321",
                    "default_email": "private@test.local",
                    "first_name": "Имя",
                    "birthday": "1990-01-01",
                },
            ),
            (
                "vk",
                {"access_token": "token", "state": "state", "user_id": "321"},
                {
                    "user": {
                        "user_id": "321",
                        "email": "private@test.local",
                        "phone": "+79991234567",
                    }
                },
            ),
        ):
            with (
                self.subTest(provider=provider),
                patch(
                    "apps.users.social_oauth.requests.request",
                    side_effect=[self.response(token), self.response(profile)],
                ),
            ):
                identity = fetch_identity(
                    get_provider(provider),
                    code="code",
                    verifier="verifier",
                    state="state",
                    device_id="device",
                    subject_only=True,
                )
            self.assertEqual(identity, {"subject": "321"})

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
