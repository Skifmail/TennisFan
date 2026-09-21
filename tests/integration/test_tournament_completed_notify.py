"""Интеграционные тесты: письма и уведомления после завершения турнира."""

from __future__ import annotations

from datetime import date

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.tournaments.completion_notify import (
    _telegram_text,
    complete_tournament_and_notify,
    notify_tournament_completed,
)
from apps.tournaments.models import (
    TournamentPlayerResult,
    TournamentStatus,
    TournamentWithdrawal,
)
from apps.tournaments.overdue import _reopen_tournament_after_auto_rt_revert
from apps.tournaments.round_robin import generate_bracket
from apps.users.models import Notification
from tests.support.factories import make_player, make_tournament

_NOTIFY_EMAIL_SETTINGS = {
    "EMAIL_BACKEND": "apps.core.mail.LoggingEmailBackend",
    "EMAIL_BACKEND_INNER": "django.core.mail.backends.locmem.EmailBackend",
    "COMPLETION_NOTIFY_SYNC": True,
}


@override_settings(**_NOTIFY_EMAIL_SETTINGS)
class TournamentCompletedNotifyTestCase(TestCase):
    """Рассылка итогов: кубки, пропуск снятых, идемпотентность."""

    def setUp(self) -> None:
        self.winner = make_player(
            email_suffix="complete-1",
            first_name="Анна",
            last_name="Сорокина",
        )
        self.second = make_player(
            email_suffix="complete-2",
            first_name="Лидия",
            last_name="Рыжикова",
        )
        self.third = make_player(
            email_suffix="complete-3",
            first_name="Марина",
            last_name="Санталова",
        )
        self.fifth = make_player(
            email_suffix="complete-5",
            first_name="Юлия",
            last_name="Кормилина",
        )
        self.withdrawn = make_player(
            email_suffix="complete-w",
            first_name="Роман",
            last_name="Силкин",
        )
        self.tournament = make_tournament(
            name="Многодневный турнир Воскресенск",
            slug="rr-complete-notify",
            format="round_robin",
            status=TournamentStatus.COMPLETED,
        )
        TournamentPlayerResult.objects.create(
            tournament=self.tournament,
            player=self.winner,
            place=1,
            fan_points=100,
        )
        TournamentPlayerResult.objects.create(
            tournament=self.tournament,
            player=self.second,
            place=2,
            fan_points=70,
        )
        TournamentPlayerResult.objects.create(
            tournament=self.tournament,
            player=self.third,
            place=3,
            fan_points=45,
        )
        TournamentPlayerResult.objects.create(
            tournament=self.tournament,
            player=self.fifth,
            place=5,
            fan_points=25,
        )
        TournamentPlayerResult.objects.create(
            tournament=self.tournament,
            player=self.withdrawn,
            place=8,
            fan_points=0,
        )
        TournamentWithdrawal.objects.create(
            tournament=self.tournament,
            player=self.withdrawn,
            withdrawn_by=self.winner.user,
        )

    def test_place_one_email_has_trophy_and_congratulations(self) -> None:
        """Победитель получает кубок и поздравление."""
        sent = notify_tournament_completed(self.tournament.pk)
        self.assertEqual(sent, 4)
        winner_mail = next(m for m in mail.outbox if m.to == [self.winner.user.email])
        html = winner_mail.alternatives[0][0]
        self.assertIn("вы победили", winner_mail.subject)
        self.assertIn("trophy-place-1.png", html)
        self.assertIn("Поздравляем!", html)
        self.assertIn("это ваше золото", html)
        self.assertIn("100", html)

    def test_place_five_email_has_no_trophy(self) -> None:
        """5 место — без кубка, с благодарностью за игру."""
        notify_tournament_completed(self.tournament.pk)
        fifth_mail = next(m for m in mail.outbox if m.to == [self.fifth.user.email])
        html = fifth_mail.alternatives[0][0]
        self.assertIn("ваше 5 место", fifth_mail.subject)
        self.assertNotIn("trophy-place-", html)
        self.assertIn("Турнир завершён", html)
        self.assertIn("спасибо за игру", html)

    def test_skips_withdrawn_and_writes_lk(self) -> None:
        """Снятый участник не получает письмо; остальным есть уведомление в ЛК."""
        notify_tournament_completed(self.tournament.pk)
        withdrawn_emails = [
            m for m in mail.outbox if m.to == [self.withdrawn.user.email]
        ]
        self.assertEqual(withdrawn_emails, [])
        self.assertFalse(Notification.objects.filter(user=self.withdrawn.user).exists())
        self.assertTrue(
            Notification.objects.filter(
                user=self.winner.user, message__contains="завершён"
            ).exists()
        )

    def test_notify_is_idempotent(self) -> None:
        """Повторный вызов не дублирует письма."""
        first = notify_tournament_completed(self.tournament.pk)
        second = notify_tournament_completed(self.tournament.pk)
        self.assertEqual(first, 4)
        self.assertEqual(second, 0)
        self.assertEqual(len(mail.outbox), 4)
        self.tournament.refresh_from_db()
        self.assertIsNotNone(self.tournament.completion_notified_at)

    def test_completed_without_timestamp_still_notifies(self) -> None:
        """Статус COMPLETED без timestamp — рассылка всё равно уходит."""
        with self.captureOnCommitCallbacks(execute=True):
            complete_tournament_and_notify(self.tournament)
        self.assertEqual(len(mail.outbox), 4)
        self.tournament.refresh_from_db()
        self.assertIsNotNone(self.tournament.completion_notified_at)

    def test_no_results_does_not_stamp(self) -> None:
        """Без итогов флаг не ставится — рассылку можно повторить позже."""
        TournamentPlayerResult.objects.filter(tournament=self.tournament).delete()
        sent = notify_tournament_completed(self.tournament.pk)
        self.assertEqual(sent, 0)
        self.tournament.refresh_from_db()
        self.assertIsNone(self.tournament.completion_notified_at)

    def test_reopen_clears_completion_notified_at(self) -> None:
        """После авто-RT revert следующая финализация снова сможет разослать письма."""
        self.tournament.completion_notified_at = timezone.now()
        self.tournament.save(update_fields=["completion_notified_at"])
        _reopen_tournament_after_auto_rt_revert(self.tournament)
        self.tournament.refresh_from_db()
        self.assertEqual(self.tournament.status, TournamentStatus.ACTIVE)
        self.assertIsNone(self.tournament.completion_notified_at)

    def test_telegram_text_escapes_tournament_name(self) -> None:
        """Название турнира в Telegram не ломает HTML."""
        text = _telegram_text(
            tournament_name="Кубок <Gold> & Friends",
            place=1,
            place_stat="1",
            fan_points=100,
            trophy_place=1,
        )
        self.assertIn("Кубок &lt;Gold&gt; &amp; Friends", text)
        self.assertNotIn("<Gold>", text)


@override_settings(**_NOTIFY_EMAIL_SETTINGS)
class RoundRobinFinalizeSendsCompletionEmailTestCase(TestCase):
    """Финализация круговика ставит статус и шлёт письма после коммита."""

    def test_finalize_two_players_sends_emails(self) -> None:
        """После последнего матча оба участника получают письмо."""
        from apps.tournaments.models import Match

        p1 = make_player(email_suffix="rr-fin-1", first_name="Анна", last_name="Один")
        p2 = make_player(email_suffix="rr-fin-2", first_name="Лидия", last_name="Два")
        tournament = make_tournament(
            name="Круговик письмо",
            slug="rr-finalize-mail",
            format="round_robin",
            status=TournamentStatus.ACTIVE,
            max_participants=2,
            start_date=date.today(),
        )
        tournament.participants.add(p1, p2)
        ok, msg = generate_bracket(tournament)
        self.assertTrue(ok, msg)
        mail.outbox.clear()

        match = tournament.matches.get()
        match.player1_set1 = 6
        match.player2_set1 = 1
        match.player1_set2 = 6
        match.player2_set2 = 2
        match.winner = p1
        match.status = Match.MatchStatus.COMPLETED
        match.completed_datetime = timezone.now()
        with self.captureOnCommitCallbacks(execute=True):
            match.save()

        tournament.refresh_from_db()
        self.assertEqual(tournament.status, TournamentStatus.COMPLETED)
        self.assertIsNotNone(tournament.completion_notified_at)
        recipients = {tuple(m.to) for m in mail.outbox}
        self.assertIn((p1.user.email,), recipients)
        self.assertIn((p2.user.email,), recipients)
        html_bodies = [m.alternatives[0][0] for m in mail.outbox if m.alternatives]
        self.assertTrue(any("trophy-place-1.png" in body for body in html_bodies))


@override_settings(**_NOTIFY_EMAIL_SETTINGS)
class CompleteTournamentAndNotifyIdempotentStatusTestCase(TestCase):
    """Повторная финализация уже уведомлённого турнира не шлёт письма снова."""

    def test_already_notified_does_not_resend(self) -> None:
        """Если timestamp уже стоит, хелпер не планирует повторную рассылку."""
        tournament = make_tournament(
            slug="already-complete-notify",
            format="round_robin",
            status=TournamentStatus.COMPLETED,
        )
        tournament.completion_notified_at = timezone.now()
        tournament.save(update_fields=["completion_notified_at"])
        with self.captureOnCommitCallbacks(execute=True):
            complete_tournament_and_notify(tournament)
        tournament.refresh_from_db()
        self.assertEqual(tournament.status, TournamentStatus.COMPLETED)
        self.assertEqual(len(mail.outbox), 0)
