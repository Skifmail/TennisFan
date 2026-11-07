"""Атомарность продления и сохранение свежего результата при ручной правке дедлайна."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from unittest.mock import patch

from django.core import mail
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.models import UserTelegramLink
from apps.notifications.models import Delivery
from apps.notifications.services import create_notification
from apps.tournaments.models import Match, SparringDeadlineExtension, TournamentStatus
from apps.tournaments.proposal_service import submit_match_result
from apps.tournaments.sparring_deadlines import (
    extend_sparring_deadline,
    prepare_admin_sparring_save,
)
from apps.users.models import Notification
from tests.support.factories import make_player, make_tournament, make_user


@override_settings(
    NOTIFICATION_CENTER_ENABLED=False,
    USER_NOTIFICATIONS_EMAIL_ENABLED=False,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    COMPLETION_NOTIFY_SYNC=True,
)
class MatchDeadlineAtomicityTests(TestCase):
    """Продление не подтверждается отдельно от уведомлений и не затирает сыгранный матч."""

    def setUp(self) -> None:
        """Создать спарринг с отдельными привязками обоих участников."""
        self.first = make_player(email_suffix="deadline-atomic-first", points=3000)
        self.second = make_player(email_suffix="deadline-atomic-second", points=2500)
        self.deadline = timezone.now() + timedelta(days=2)
        self.match = Match.objects.create(
            match_type=Match.MatchType.SPARRING,
            player1=self.first,
            player2=self.second,
            deadline=self.deadline,
        )
        for index, player in enumerate((self.first, self.second)):
            UserTelegramLink.objects.create(
                user=player.user, user_bot_chat_id=6001 + index
            )
        Notification.objects.all().delete()
        Delivery.objects.all().delete()
        mail.outbox.clear()

    def _tournament_match(self) -> str:
        """Подготовить реальный административный endpoint изменения даты."""
        self.tournament = make_tournament(
            slug="deadline-atomic-tournament",
            status=TournamentStatus.ACTIVE,
            format="round_robin",
        )
        Match.objects.filter(pk=self.match.pk).update(
            tournament=self.tournament, match_type=Match.MatchType.TOURNAMENT
        )
        self.admin = make_user(email="deadline-atomic-admin@test.local", is_staff=True)
        self.client.force_login(self.admin)
        return reverse(
            "tournament_manage_match_deadline",
            kwargs={"slug": self.tournament.slug, "pk": self.match.pk},
        )

    def test_self_extension_notification_failure_rolls_back_deadline_and_history(
        self,
    ) -> None:
        """Поздний отказ уведомления возвращает дату и право участника на продление."""
        calls = 0

        def notify(**kwargs: Any) -> object:
            """Отказать второму получателю после реальной записи первого."""
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("deadline inbox failed")
            return create_notification(**kwargs)

        with patch(
            "apps.tournaments.sparring_deadlines.create_notification",
            side_effect=notify,
        ):
            with self.assertRaises(RuntimeError):
                extend_sparring_deadline(self.match.pk, self.first)
        self.assertEqual(Match.objects.get(pk=self.match.pk).deadline, self.deadline)
        self.assertFalse(SparringDeadlineExtension.objects.exists())
        self.assertFalse(Notification.objects.exists())
        self.assertFalse(Delivery.objects.exists())

    def test_self_extension_saves_inbox_and_telegram_before_commit(self) -> None:
        """Очередь продления доступна до коммита, внешняя отправка ещё не выполняется."""
        with patch("apps.telegram_bot.services.is_configured", return_value=True):
            extend_sparring_deadline(self.match.pk, self.first)
        self.assertEqual(
            Notification.objects.filter(event_key="match.deadline_extended").count(), 2
        )
        deliveries = Delivery.objects.filter(
            channel="telegram", event_key="match.deadline_extended"
        )
        self.assertEqual(deliveries.count(), 2)
        self.assertTrue(all(item.status == "queued" for item in deliveries))

    def test_admin_deadline_only_edit_preserves_result_saved_since_form_read(
        self,
    ) -> None:
        """Старая форма с правкой даты не сбрасывает новый результат и его дельты."""
        stale = Match.objects.get(pk=self.match.pk)
        submit_match_result(
            self.match.pk, self.first, result="win", player1_set1=6, player2_set1=4
        )
        current = Match.objects.get(pk=self.match.pk)
        stale.deadline = self.deadline + timedelta(days=7)
        with transaction.atomic():
            prepare_admin_sparring_save(stale, {"deadline"})
            stale.save()
        current.refresh_from_db()
        self.assertEqual(current.status, Match.MatchStatus.COMPLETED)
        self.assertEqual(current.winner_id, self.first.pk)
        self.assertEqual((current.player1_set1, current.player2_set1), (6, 4))
        self.assertEqual(current.rating_status, Match.RatingCalcStatus.CALCULATED)
        self.first.refresh_from_db()
        self.assertEqual(self.first.matches_played, 1)
        self.assertEqual(self.first.matches_won, 1)

    def test_manual_deadline_rechecks_result_saved_after_initial_match_read(
        self,
    ) -> None:
        """Управление турниром отказывает, если матч сыгран до захвата блокировки."""
        url = self._tournament_match()

        def get_match(*args: Any, **kwargs: Any) -> object:
            """Вернуть старую копию, пока в БД уже сохранён настоящий результат."""
            obj = get_object_or_404(*args, **kwargs)
            if args[0] is Match:
                submit_match_result(
                    self.match.pk,
                    self.first,
                    result="win",
                    player1_set1=6,
                    player2_set1=4,
                )
            return obj

        with patch(
            "apps.tournaments.views.get_object_or_404",
            side_effect=get_match,
        ):
            response = self.client.post(
                url,
                {"deadline": (date.today() + timedelta(days=10)).isoformat()},
                secure=True,
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Match.objects.get(pk=self.match.pk).deadline, self.deadline)
        self.assertEqual(
            Match.objects.get(pk=self.match.pk).status, Match.MatchStatus.COMPLETED
        )

    def test_manual_deadline_rejects_cancelled_match(self) -> None:
        """Отменённый матч нельзя вновь сделать активным одной правкой даты."""
        url = self._tournament_match()
        Match.objects.filter(pk=self.match.pk).update(
            status=Match.MatchStatus.CANCELLED
        )
        self.client.post(
            url,
            {"deadline": (date.today() + timedelta(days=10)).isoformat()},
            secure=True,
        )
        self.assertEqual(Match.objects.get(pk=self.match.pk).deadline, self.deadline)

    def test_manual_deadline_notification_failure_rolls_back_change_and_email(
        self,
    ) -> None:
        """Отказ обязательной записи не оставляет новую дату и письмо об отменённой правке."""
        url = self._tournament_match()
        with patch(
            "apps.tournaments.utils.create_notification",
            side_effect=RuntimeError("manual deadline inbox failed"),
        ):
            with (
                self.captureOnCommitCallbacks(execute=True),
                self.assertRaises(RuntimeError),
            ):
                self.client.post(
                    url,
                    {"deadline": (date.today() + timedelta(days=10)).isoformat()},
                    secure=True,
                )
        self.assertEqual(Match.objects.get(pk=self.match.pk).deadline, self.deadline)
        self.assertFalse(Notification.objects.exists())
        self.assertFalse(Delivery.objects.exists())
        self.assertFalse(mail.outbox)
