"""Свежесть и атомарность ручного результата вместе с обязательными уведомлениями."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

from django.core import mail
from django.shortcuts import get_object_or_404
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.models import UserTelegramLink
from apps.notifications.models import Delivery
from apps.notifications.services import create_notification
from apps.tournaments.models import Match, TournamentStatus
from apps.tournaments.overdue import apply_no_show_walkover
from apps.users.models import Notification
from tests.support.factories import make_player, make_tournament, make_user


@override_settings(
    SECURE_SSL_REDIRECT=False,
    ALLOWED_HOSTS=["testserver"],
    NOTIFICATION_CENTER_ENABLED=False,
    USER_NOTIFICATIONS_EMAIL_ENABLED=True,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    COMPLETION_NOTIFY_SYNC=True,
    TELEGRAM_BOT_SITE_BASE_URL="https://tennisfan.ru",
)
class AdminMatchResultAtomicityTests(TestCase):
    """Ручной результат не переживает отказ уведомлений и не сохраняет старую копию матча."""

    def setUp(self) -> None:
        """Создать активный турнир и личные привязки обоих участников."""
        self.admin = make_user(email="atomic-result-admin@test.local", is_staff=True)
        self.client.force_login(self.admin)
        self.first = make_player(email_suffix="admin-atomic-first", points=3000)
        self.second = make_player(email_suffix="admin-atomic-second", points=2500)
        self.tournament = make_tournament(
            slug="atomic-admin-result",
            format="round_robin",
            status=TournamentStatus.ACTIVE,
        )
        self.deadline = timezone.now() + timedelta(days=2)
        self.match = Match.objects.create(
            tournament=self.tournament,
            player1=self.first,
            player2=self.second,
            deadline=self.deadline,
        )
        # Финализация турнира — отдельное событие: здесь проверяется только результат.
        Match.objects.create(
            tournament=self.tournament,
            player1=self.first,
            player2=make_player(email_suffix="admin-atomic-next"),
            deadline=self.deadline,
        )
        for index, player in enumerate((self.first, self.second)):
            UserTelegramLink.objects.create(
                user=player.user, user_bot_chat_id=8001 + index
            )
        self.url = reverse(
            "tournament_manage_match_result", args=[self.tournament.slug, self.match.pk]
        )
        self.payload: dict[str, Any] = {
            "winner_id": self.first.pk,
            "score1": "6",
            "score2": "4",
        }
        self.network = patch(
            "apps.telegram_bot.services.is_configured", return_value=True
        )
        self.network.start()
        self.addCleanup(self.network.stop)
        Notification.objects.all().delete()
        Delivery.objects.all().delete()
        mail.outbox.clear()

    def test_notification_failure_rolls_back_manual_score_and_rt(self) -> None:
        """Отказ второму получателю откатывает и сыгранный счёт, и ручной RT."""
        for rt in (False, True):
            with self.subTest(rt=rt):
                calls = 0

                def notify(**kwargs: Any) -> object:
                    """Сохранить первую запись, затем отказать второй."""
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        raise RuntimeError("admin result inbox failed")
                    return create_notification(**kwargs)

                with (
                    patch(
                        "apps.tournaments.proposal_service.create_notification",
                        side_effect=notify,
                    ),
                    self.captureOnCommitCallbacks(execute=True),
                ):
                    with self.assertRaises(RuntimeError):
                        self.client.post(
                            self.url, self.payload | {"walkover": "1" if rt else ""}
                        )
                self.match.refresh_from_db()
                self.first.refresh_from_db()
                self.second.refresh_from_db()
                self.assertEqual(self.match.status, Match.MatchStatus.SCHEDULED)
                self.assertIsNone(self.match.winner_id)
                self.assertEqual(
                    (self.first.matches_played, self.second.matches_played), (0, 0)
                )
                self.assertEqual(
                    (self.first.total_points, self.second.total_points), (3000, 2500)
                )
                self.assertFalse(Notification.objects.exists())
                self.assertFalse(Delivery.objects.exists())
                self.assertFalse(mail.outbox)

    def test_manual_result_persists_queue_before_commit_and_email_after(self) -> None:
        """Результат, ЛК и Telegram записываются вместе; почта не уходит до коммита."""
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.url, self.payload)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(
                Notification.objects.filter(event_key="match.result_confirmed").count(),
                2,
            )
            self.assertEqual(
                Delivery.objects.filter(
                    event_key="match.result_confirmed", status="queued"
                ).count(),
                2,
            )
            self.assertFalse(mail.outbox)
        # Два письма о результате и два отдельных приглашения оценить соперника.
        self.assertEqual(len(mail.outbox), 4)

    def test_replayed_manual_score_does_not_notify_or_count_twice(self) -> None:
        """Повтор неизменённого результата не создаёт ещё одно событие для игроков."""
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self.url, self.payload)
            self.client.post(self.url, self.payload)
        self.first.refresh_from_db()
        self.assertEqual(self.first.matches_played, 1)
        self.assertEqual(
            Notification.objects.filter(event_key="match.result_confirmed").count(), 2
        )
        self.assertEqual(
            Delivery.objects.filter(event_key="match.result_confirmed").count(), 2
        )
        self.assertEqual(len(mail.outbox), 4)

    def test_manual_result_keeps_deadline_changed_after_initial_read(self) -> None:
        """Результат берёт неизменённые поля из свежей строки, не из первого чтения."""
        new_deadline = self.deadline + timedelta(days=7)

        def get_match(*args: Any, **kwargs: Any) -> object:
            """Вернуть устаревшую копию после сохранения чужого продления."""
            obj = get_object_or_404(*args, **kwargs)
            if args[0] is Match:
                Match.objects.filter(pk=self.match.pk).update(deadline=new_deadline)
            return obj

        with patch("apps.tournaments.views.get_object_or_404", side_effect=get_match):
            self.client.post(self.url, self.payload)
        self.match.refresh_from_db()
        self.assertEqual(self.match.deadline, new_deadline)
        self.assertEqual(self.match.status, Match.MatchStatus.COMPLETED)

    def test_manual_result_rechecks_tournament_cancelled_after_initial_read(
        self,
    ) -> None:
        """Результат не применится, если разрешённый турнир уже отменён."""

        def get_match(*args: Any, **kwargs: Any) -> object:
            """Отменить турнир после чтения матча."""
            obj = get_object_or_404(*args, **kwargs)
            if args[0] is Match:
                type(self.tournament).objects.filter(pk=self.tournament.pk).update(
                    status=TournamentStatus.CANCELLED
                )
            return obj

        with patch("apps.tournaments.views.get_object_or_404", side_effect=get_match):
            self.client.post(self.url, self.payload)
        self.assertEqual(
            Match.objects.get(pk=self.match.pk).status, Match.MatchStatus.SCHEDULED
        )
        self.assertFalse(Notification.objects.exists())
        self.assertFalse(Delivery.objects.exists())

    def test_manual_result_refuses_cancelled_match(self) -> None:
        """Отменённый матч нельзя завершить обычным вводом результата."""
        Match.objects.filter(pk=self.match.pk).update(
            status=Match.MatchStatus.CANCELLED
        )
        self.client.post(self.url, self.payload)
        self.assertEqual(
            Match.objects.get(pk=self.match.pk).status, Match.MatchStatus.CANCELLED
        )
        self.assertFalse(Notification.objects.exists())

    def test_manual_result_uses_shared_finished_score_rules(self) -> None:
        """Пустой, незавершённый, неполный и лишний счёт отвергается как на сайте и в боте."""
        for scores in (
            {"score1": "", "score2": ""},
            {"score1": "5", "score2": "4"},
            {"score1": "6", "score2": ""},
            {"set2_p1": "6", "set2_p2": "3", "set3_p1": "6", "set3_p2": "2"},
        ):
            with self.subTest(scores=scores):
                self.client.post(self.url, self.payload | scores)
                self.assertEqual(
                    Match.objects.get(pk=self.match.pk).status,
                    Match.MatchStatus.SCHEDULED,
                )
                self.assertFalse(Notification.objects.exists())

    def test_played_result_replaces_no_show_flags_and_effects(self) -> None:
        """Ручной сыгранный результат отменяет прежнюю неявку, сохраняя однократную статистику."""
        apply_no_show_walkover(self.match, loser=self.second, notify=False)
        self.client.post(self.url, self.payload)
        self.match.refresh_from_db()
        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.assertFalse(self.match.walkover_no_show_side1)
        self.assertFalse(self.match.walkover_no_show_side2)
        self.assertEqual(
            (self.first.matches_played, self.second.matches_played), (1, 1)
        )
        self.assertEqual(
            self.second.total_points, 2500 + self.match.rating_delta_player2
        )
