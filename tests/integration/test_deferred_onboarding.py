"""Отложенное заполнение профиля, напоминания и однократный выбор силы."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from apps.users.models import Player, User
from apps.users.onboarding import StrengthAlreadySet, set_initial_strength
from apps.users.social_context import profile_completion
from apps.users.verification import try_auto_verify


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class DeferredOnboardingTests(TestCase):
    """Игрок может смотреть сайт сразу, но не играть без заданной силы."""

    def setUp(self) -> None:
        cache.clear()
        self.user = User.objects.create_user(
            email="onboarding@test.local",
            first_name="Иван",
            last_name="Тестов",
            phone="+79991234567",
            email_verified=True,
        )
        self.player = Player.objects.create(
            user=self.user,
            city="Москва",
            birth_date=date(1990, 1, 1),
            gender="male",
            tennis_entry_level_set=False,
            is_verified=True,
        )
        self.client.force_login(self.user)

    def profile_data(self, **changes: object) -> dict[str, object]:
        """Собрать редактируемые данные профиля."""
        data: dict[str, object] = {
            "first_name": "Иван",
            "last_name": "Тестов",
            "phone": "+79991234567",
            "city": "Москва",
            "birth_date": "1990-01-01",
            "gender": "male",
        }
        data.update(changes)
        return data

    def test_initial_strength_is_saved_once_in_profile(self) -> None:
        response = self.client.post(
            reverse("profile_edit"), self.profile_data(ntrp_level="3,75"), secure=True
        )
        self.assertEqual(response.status_code, 302)
        self.player.refresh_from_db()
        self.assertTrue(self.player.tennis_entry_level_set)
        self.assertEqual(self.player.ntrp_level, Decimal("3.75"))
        original = self.player.total_points
        self.client.post(
            reverse("profile_edit"), self.profile_data(ntrp_level="6.00"), secure=True
        )
        self.player.refresh_from_db()
        self.assertEqual(self.player.ntrp_level, Decimal("3.75"))
        self.assertEqual(self.player.total_points, original)
        self.assertNotContains(
            self.client.get(reverse("profile_edit"), secure=True), 'name="ntrp_level"'
        )

    def test_api_cannot_resubmit_initial_strength(self) -> None:
        response = self.client.post(
            reverse("save_ntrp"),
            json.dumps({"ntrp_level": "3.75"}),
            content_type="application/json",
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        response = self.client.post(
            reverse("save_ntrp"),
            json.dumps({"ntrp_level": "6.00"}),
            content_type="application/json",
            secure=True,
        )
        self.assertEqual(response.status_code, 403)
        self.player.refresh_from_db()
        self.assertEqual(self.player.ntrp_level, Decimal("3.75"))

    def test_invalid_levels_do_not_change_rating(self) -> None:
        for value in ("NaN", "Infinity", "1.49", "7.01", "", "abc"):
            response = self.client.post(
                reverse("save_ntrp"),
                json.dumps({"ntrp_level": value}),
                content_type="application/json",
                secure=True,
            )
            self.assertEqual(response.status_code, 400)
        self.player.refresh_from_db()
        self.assertFalse(self.player.tennis_entry_level_set)

    def test_service_rejects_legacy_and_played_accounts(self) -> None:
        self.player.tennis_entry_level_set = True
        self.player.save()
        with self.assertRaises(StrengthAlreadySet):
            set_initial_strength(self.player.pk, Decimal("4.0"))
        self.player.tennis_entry_level_set = False
        self.player.matches_played = 1
        self.player.save()
        with self.assertRaises(StrengthAlreadySet):
            set_initial_strength(self.player.pk, Decimal("4.0"))

    def test_profile_can_be_saved_without_level(self) -> None:
        response = self.client.post(
            reverse("profile_edit"), self.profile_data(), secure=True
        )
        self.assertEqual(response.status_code, 302)
        self.player.refresh_from_db()
        self.assertFalse(self.player.tennis_entry_level_set)

    def test_missing_email_can_be_added_and_confirmation_sent(self) -> None:
        User.objects.filter(pk=self.user.pk).update(email=None, email_verified=False)
        with (
            patch("apps.core.email_service.send_email_verification") as send,
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self.client.post(
                reverse("profile_edit"),
                self.profile_data(email="added@test.local"),
                secure=True,
            )
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "added@test.local")
        self.assertFalse(self.user.email_verified)
        send.assert_called_once()

    def test_existing_email_collision_does_not_merge_users(self) -> None:
        User.objects.create_user(email="existing@test.local")
        response = self.client.post(
            reverse("profile_edit"),
            self.profile_data(email="EXISTING@test.local"),
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("email", response.context["form"].errors)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "onboarding@test.local")

    def test_auto_verification_is_preserved_without_strength(self) -> None:
        self.player.is_verified = False
        self.player.save()
        self.assertTrue(try_auto_verify(self.player))
        self.player.refresh_from_db()
        self.assertTrue(self.player.is_verified)
        self.assertFalse(self.player.tennis_entry_level_set)

    def test_verified_player_still_cannot_register_tournament_without_strength(
        self,
    ) -> None:
        from apps.tournaments.views import _check_tournament_registration_eligibility

        request = RequestFactory().get("/")
        request.user = self.user
        ok, message = _check_tournament_registration_eligibility(
            request, SimpleNamespace(sport="tennis"), self.player
        )
        self.assertFalse(ok)
        self.assertIn("уровень силы", message)

    def test_verified_player_cannot_invite_to_sparring_without_strength(self) -> None:
        with patch("apps.sparring.views.user_has_sparring_access", return_value=True):
            response = self.client.get(reverse("sparring_invite"), secure=True)
        self.assertRedirects(
            response, reverse("profile_edit"), fetch_redirect_response=False
        )

    def test_unfilled_user_can_browse_public_pages(self) -> None:
        User.objects.filter(pk=self.user.pk).update(email_verified=False)
        for url in (
            reverse("home"),
            reverse("sparring_list"),
            reverse("tournament_list"),
        ):
            response = self.client.get(url, secure=True)
            self.assertEqual(response.status_code, 200)

    def test_reminder_includes_strength_even_for_verified_player(self) -> None:
        request = RequestFactory().get("/")
        request.user = self.user
        request.resolver_match = SimpleNamespace(url_name="home")
        result = profile_completion(request)["profile_completion_reminder"]
        self.assertIn("Уровень силы", result["fields"])
        self.assertEqual(result["interval_ms"], 6 * 60 * 60 * 1000)
        request.resolver_match.url_name = "profile_edit"
        self.assertEqual(profile_completion(request), {})

    def test_full_profile_has_no_reminder(self) -> None:
        Player.objects.filter(pk=self.player.pk).update(
            tennis_entry_level_set=True, avatar="avatars/existing.jpg"
        )
        request = RequestFactory().get("/")
        request.user = User.objects.get(pk=self.user.pk)
        request.resolver_match = SimpleNamespace(url_name="home")
        self.assertEqual(profile_completion(request), {})

    def test_missing_email_resend_redirects_to_profile(self) -> None:
        User.objects.filter(pk=self.user.pk).update(email=None, email_verified=False)
        with patch("apps.core.email_service.send_email_verification") as send:
            response = self.client.post(reverse("verify_email_resend"), secure=True)
        self.assertEqual(response.url, reverse("profile_edit"))
        send.assert_not_called()

    def test_pending_partner_cannot_be_added_to_doubles(self) -> None:
        from apps.sparring.doubles_services import create_doubles_request
        from apps.sparring.models import DoublesMatchRequest

        captain_user = User.objects.create_user(email="captain@test.local")
        captain = Player.objects.create(user=captain_user)
        before = DoublesMatchRequest.objects.count()
        with self.assertRaisesMessage(ValueError, "уровень силы"):
            create_doubles_request(
                created_by=captain, partner=self.player, sport="tennis"
            )
        self.assertEqual(DoublesMatchRequest.objects.count(), before)
