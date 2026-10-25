"""Проверки продления спаррингов игроками и управления матчем в админке."""

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.tournaments.admin import MatchAdmin
from apps.tournaments.models import Match, SparringDeadlineExtension
from apps.tournaments.sparring_deadlines import extend_sparring_deadline
from tests.support.factories import make_player


class SparringDeadlineExtensionTests(TestCase):
    """Каждый участник может добавить неделю только один раз."""

    def setUp(self) -> None:
        self.client = Client(HTTP_X_FORWARDED_PROTO="https")
        self.player1 = make_player(email_suffix="extend1")
        self.player2 = make_player(email_suffix="extend2")
        self.outsider = make_player(email_suffix="extend_outsider")
        self.deadline = timezone.now() + timedelta(days=2)
        self.match = Match.objects.create(
            match_type=Match.MatchType.SPARRING,
            player1=self.player1,
            player2=self.player2,
            deadline=self.deadline,
            deadline_overdue_notified_at=timezone.now(),
        )
        self.url = reverse("extend_sparring_match_deadline", args=[self.match.pk])

    def test_each_player_gets_one_week_and_repeat_is_rejected(self) -> None:
        extend_sparring_deadline(self.match.pk, self.player1)
        with self.assertRaises(ValidationError):
            extend_sparring_deadline(self.match.pk, self.player1)
        extend_sparring_deadline(self.match.pk, self.player2)
        self.match.refresh_from_db()
        self.assertEqual(self.match.deadline, self.deadline + timedelta(days=14))
        self.assertIsNone(self.match.deadline_overdue_notified_at)
        self.assertEqual(self.match.sparring_deadline_extensions.count(), 2)
        extension = self.match.sparring_deadline_extensions.get(player=self.player1)
        self.assertEqual(extension.old_deadline, self.deadline)
        self.assertEqual(extension.new_deadline, self.deadline + timedelta(days=7))

    def test_unique_record_blocks_duplicate_at_database_level(self) -> None:
        extend_sparring_deadline(self.match.pk, self.player1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            SparringDeadlineExtension.objects.create(
                match=self.match,
                player=self.player1,
                old_deadline=self.deadline,
                new_deadline=self.deadline,
            )

    def test_partner_in_doubles_can_extend(self) -> None:
        self.match.partner1 = self.outsider
        self.match.save(update_fields=["partner1"])
        extend_sparring_deadline(self.match.pk, self.outsider)
        self.match.refresh_from_db()
        self.assertEqual(self.match.deadline, self.deadline + timedelta(days=7))

    def test_outsider_and_missing_profile_cannot_extend(self) -> None:
        for player in (self.outsider, None):
            with self.subTest(player=player), self.assertRaises(ValidationError):
                extend_sparring_deadline(self.match.pk, player)
        self.assertFalse(self.match.sparring_deadline_extensions.exists())

    def test_closed_and_tournament_matches_and_missing_deadline_are_rejected(
        self,
    ) -> None:
        for changes in (
            {"status": Match.MatchStatus.COMPLETED},
            {"status": Match.MatchStatus.CANCELLED},
            {"status": Match.MatchStatus.WALKOVER},
            {"match_type": Match.MatchType.TOURNAMENT},
            {"deadline": None},
        ):
            with self.subTest(changes=changes):
                Match.objects.filter(pk=self.match.pk).update(**changes)
                with self.assertRaises(ValidationError):
                    extend_sparring_deadline(self.match.pk, self.player1)
                Match.objects.filter(pk=self.match.pk).update(
                    status=Match.MatchStatus.SCHEDULED,
                    match_type=Match.MatchType.SPARRING,
                    deadline=self.deadline,
                )

    def test_overdue_active_match_can_be_extended(self) -> None:
        deadline = timezone.now() - timedelta(days=1)
        Match.objects.filter(pk=self.match.pk).update(deadline=deadline)
        match = extend_sparring_deadline(self.match.pk, self.player1)
        self.assertEqual(match.deadline, deadline + timedelta(days=7))

    def test_endpoint_requires_login_post_and_participation(self) -> None:
        self.assertEqual(self.client.post(self.url).status_code, 302)
        self.client.force_login(self.player1.user)
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.client.force_login(self.outsider.user)
        self.client.post(self.url)
        self.match.refresh_from_db()
        self.assertEqual(self.match.deadline, self.deadline)
        self.client.force_login(self.player1.user)
        self.assertRedirects(
            self.client.post(self.url),
            reverse("match_detail", args=[self.match.pk]),
            fetch_redirect_response=False,
        )
        self.match.refresh_from_db()
        self.assertEqual(self.match.deadline, self.deadline + timedelta(days=7))

    def test_button_and_used_message_on_match_detail(self) -> None:
        self.client.force_login(self.player1.user)
        detail_url = reverse("match_detail", args=[self.match.pk])
        self.assertContains(self.client.get(detail_url), "Продлить дедлайн на неделю")
        self.client.post(self.url)
        response = self.client.get(detail_url)
        self.assertNotContains(response, "Продлить дедлайн на неделю")
        self.assertContains(response, "Вы уже использовали продление")
        self.client.force_login(self.outsider.user)
        self.assertNotContains(
            self.client.get(detail_url), "Продлить дедлайн на неделю"
        )

    @patch("apps.tournaments.sparring_deadlines.create_notification")
    def test_notifications_are_sent_after_commit(self, notify: Any) -> None:
        with self.captureOnCommitCallbacks(execute=True):
            extend_sparring_deadline(self.match.pk, self.player1)
            notify.assert_not_called()
        self.assertEqual(notify.call_count, 2)

    @patch(
        "apps.tournaments.sparring_deadlines.SparringDeadlineExtension.objects.create",
        side_effect=RuntimeError("Ошибка записи"),
    )
    def test_failed_history_save_rolls_back_deadline(self, create: Any) -> None:
        with self.assertRaises(RuntimeError):
            extend_sparring_deadline(self.match.pk, self.player1)
        self.match.refresh_from_db()
        self.assertEqual(self.match.deadline, self.deadline)


class SparringAdminTests(TestCase):
    """Администратор редактирует спарринги без ограничений игроков."""

    def setUp(self) -> None:
        self.client = Client(HTTP_X_FORWARDED_PROTO="https")
        self.player1 = make_player(email_suffix="admin_extend1")
        self.player2 = make_player(email_suffix="admin_extend2")
        self.admin_user = self.player1.user
        self.admin_user.is_staff = True
        self.admin_user.is_superuser = True
        self.admin_user.save()
        self.match = Match.objects.create(
            match_type=Match.MatchType.SPARRING,
            player1=self.player1,
            player2=self.player2,
            deadline=timezone.now().replace(microsecond=0) + timedelta(days=1),
        )
        self.model_admin = MatchAdmin(Match, AdminSite())
        self.request = RequestFactory().get("/")
        self.request.user = self.admin_user

    def test_sparring_fields_editable_but_tournament_rules_unchanged(self) -> None:
        fields = self.model_admin.get_form(self.request, self.match).base_fields
        for name in (
            "deadline",
            "player1",
            "player2",
            "partner1",
            "partner2",
            "status",
            "winner",
            "scheduled_datetime",
        ):
            self.assertIn(name, fields)
        tournament_match = Match.objects.create()
        self.assertIn(
            "deadline",
            self.model_admin.get_readonly_fields(self.request, tournament_match),
        )

    def _save_admin(self, **changes: Any) -> None:
        self.match.refresh_from_db()
        form_class = self.model_admin.get_form(self.request, self.match)
        from django.forms.models import model_to_dict

        data = model_to_dict(self.match)
        data.update(changes)
        for name, value in list(data.items()):
            if isinstance(value, datetime):
                local = timezone.localtime(value)
                data[f"{name}_0"] = local.strftime("%Y-%m-%d")
                data[f"{name}_1"] = local.strftime("%H:%M:%S")
        form = form_class(data, instance=self.match)
        self.assertTrue(form.is_valid(), form.errors.as_json())
        obj = form.save(commit=False)
        with transaction.atomic():
            self.model_admin.save_model(self.request, obj, form, True)
        self.match.refresh_from_db()

    def test_admin_can_extend_repeatedly_and_reset_overdue_notification(self) -> None:
        first = self.match.deadline + timedelta(days=20)
        self._save_admin(deadline=first)
        self._save_admin(deadline=first + timedelta(days=30))
        self.assertEqual(self.match.deadline, first + timedelta(days=30))
        self.assertFalse(self.match.sparring_deadline_extensions.exists())

    def test_admin_change_page_renders(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get(
            reverse("admin:tournaments_match_change", args=[self.match.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="deadline_0"')

    def test_completed_match_can_be_cancelled_then_replayed_without_double_stats(
        self,
    ) -> None:
        self._save_admin(
            status=Match.MatchStatus.COMPLETED,
            winner=self.player1.pk,
            player1_set1=6,
            player2_set1=2,
        )
        self.player1.refresh_from_db()
        self.assertEqual(self.player1.matches_played, 1)
        self._save_admin(status=Match.MatchStatus.CANCELLED)
        self.player1.refresh_from_db()
        self.assertEqual(self.player1.matches_played, 0)
        self.assertEqual(self.player1.matches_won, 0)
        self.assertIsNone(self.match.winner_id)
        self._save_admin(
            status=Match.MatchStatus.COMPLETED,
            winner=self.player2.pk,
            player1_set1=2,
            player2_set1=6,
        )
        self.player1.refresh_from_db()
        self.player2.refresh_from_db()
        self.assertEqual(self.player1.matches_played, 1)
        self.assertEqual(self.player2.matches_won, 1)

    def test_admin_extension_does_not_restore_used_player_extension(self) -> None:
        extend_sparring_deadline(self.match.pk, self.player1)
        self._save_admin(
            deadline=timezone.now().replace(microsecond=0) + timedelta(days=30)
        )
        with self.assertRaises(ValidationError):
            extend_sparring_deadline(self.match.pk, self.player1)

    def test_admin_cannot_save_inconsistent_winner(self) -> None:
        form_class = self.model_admin.get_form(self.request, self.match)
        form = form_class(
            {
                "player1": self.player1.pk,
                "player2": self.player2.pk,
                "sport": "tennis",
                "status": Match.MatchStatus.COMPLETED,
                "winner": self.player1.pk,
                "player1_set1": 2,
                "player2_set1": 6,
            },
            instance=self.match,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("Счёт не соответствует", str(form.non_field_errors()))
