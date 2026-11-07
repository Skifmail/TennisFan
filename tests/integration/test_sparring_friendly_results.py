"""Дружеский режим приглашений и откликов сохраняется при внесении результата."""

from __future__ import annotations

from django.test import TestCase, override_settings

from apps.sparring.models import SparringInvitation, SparringRequest, SparringResponse
from apps.sparring.services import (
    accept_sparring_invitation,
    confirm_sparring_response,
)
from apps.tournaments.models import Match
from apps.tournaments.proposal_service import (
    result_summary_for_player,
    submit_match_result,
)
from apps.users.models import Player
from tests.support.factories import make_player, make_subscription
from tests.support.matches import make_completed_sparring_match
from tests.support.notifications import seed_event_policies


@override_settings(
    NOTIFICATION_CENTER_ENABLED=False,
    USER_NOTIFICATIONS_EMAIL_ENABLED=False,
    TELEGRAM_ENABLED=False,
)
class SparringFriendlyResultTests(TestCase):
    """Результаты дружеских матчей не меняют рейтинг и счётчики участников."""

    def setUp(self) -> None:
        """Создать участников с доступом к спаррингам и разным исходным рейтингом."""
        seed_event_policies()
        self.inviter = make_player(email_suffix="friendly-inviter", points=3000)
        self.invitee = make_player(email_suffix="friendly-invitee", points=2500)
        for player in (self.inviter, self.invitee):
            make_subscription(
                player.user,
                tier_name="friendly-result",
                fancoin_balance=10,
                tier_kwargs={"has_sparring": True},
            )

    def _invitation_match(self, *, friendly: bool = True) -> Match:
        """Принять настоящее прямое приглашение через общий сервис сайта и бота."""
        invitation = SparringInvitation.objects.create(
            inviter=self.inviter,
            invitee=self.invitee,
            is_friendly=friendly,
        )
        return accept_sparring_invitation(invitation.pk, self.invitee.user_id)

    def _assert_unchanged_player(self, player: Player, points: float) -> None:
        """Проверить сохранённые рейтинг, скрытый рейтинг, матчи и победы."""
        player.refresh_from_db()
        self.assertEqual(player.total_points, points)
        self.assertEqual(player.hidden_rating, points)
        self.assertEqual(player.matches_played, 0)
        self.assertEqual(player.matches_won, 0)

    def _assert_friendly_result(self, match: Match) -> None:
        """Проверить свежий режим матча, отсутствие расчёта и честный текст исхода."""
        self._assert_unchanged_player(self.inviter, 3000)
        self._assert_unchanged_player(self.invitee, 2500)
        match.refresh_from_db()
        self.assertTrue(match.is_friendly_sparring())
        self.assertEqual(match.rating_status, Match.RatingCalcStatus.NOT_APPLICABLE)
        self.assertEqual(
            (match.rating_delta_player1, match.rating_delta_player2), (0, 0)
        )
        for player in (self.inviter, self.invitee):
            self.assertIn(
                "Рейтинг не меняется", result_summary_for_player(match, player)
            )

    def _submit_result(self, match: Match) -> None:
        """Внести завершённый счёт из общего сервиса игровых действий."""
        proposal = submit_match_result(
            match.pk,
            self.inviter,
            result=Match.ResultChoice.WIN,
            player1_set1=6,
            player2_set1=4,
            player1_set2=6,
            player2_set2=3,
        )
        self.assertIsNotNone(proposal.applied_at)
        self.assertEqual(proposal.status, Match.ProposalStatus.ACCEPTED)

    def test_friendly_invitation_result_keeps_rating_and_stats_unchanged(self) -> None:
        """Приглашение без sparring_response не становится рейтинговым после результата."""
        match = self._invitation_match()

        self._submit_result(match)

        self._assert_friendly_result(match)

    def test_friendly_invitation_manual_result_and_edit_keep_friendly_mode(
        self,
    ) -> None:
        """Прямое сохранение и смена победителя тоже не запускают FAN и статистику."""
        match = self._invitation_match()
        match.status = Match.MatchStatus.COMPLETED
        match.winner = self.inviter
        match.player1_set1, match.player2_set1 = 6, 4
        match.save(update_fields=["status", "winner", "player1_set1", "player2_set1"])
        self._assert_friendly_result(match)

        match.winner = self.invitee
        match.player1_set1, match.player2_set1 = 4, 6
        match.save(update_fields=["winner", "player1_set1", "player2_set1"])

        self._assert_friendly_result(match)

    def test_invitation_status_edit_does_not_remove_friendly_context(self) -> None:
        """Ручная смена статуса приглашения не превращает созданный матч в рейтинговый."""
        match = self._invitation_match()
        SparringInvitation.objects.filter(match=match).update(
            status=SparringInvitation.Status.CANCELLED
        )

        self._submit_result(match)

        self._assert_friendly_result(match)

    def test_friendly_response_result_has_not_applicable_rating_status(self) -> None:
        """Результат отклика не оставляет дружеский матч в очереди пересчёта рейтинга."""
        request = SparringRequest.objects.create(player=self.inviter, is_friendly=True)
        response = SparringResponse.objects.create(
            sparring_request=request,
            respondent=self.invitee,
            contact_method=SparringResponse.ContactMethod.TELEGRAM,
        )
        match = confirm_sparring_response(response.pk, self.inviter.user_id)

        self._submit_result(match)

        self._assert_friendly_result(match)

    def test_nonfriendly_invitation_still_calculates_rating(self) -> None:
        """Обычное приглашение не теряет расчёт рейтинга из-за общей защиты дружеских игр."""
        match = self._invitation_match(friendly=False)

        self._submit_result(match)

        match.refresh_from_db()
        self.inviter.refresh_from_db()
        self.invitee.refresh_from_db()
        self.assertFalse(match.is_friendly_sparring())
        self.assertEqual(match.rating_status, Match.RatingCalcStatus.CALCULATED)
        self.assertEqual(
            (self.inviter.matches_played, self.invitee.matches_played), (1, 1)
        )
        self.assertEqual((self.inviter.matches_won, self.invitee.matches_won), (1, 0))

    def test_unsaved_sparring_without_source_is_not_friendly(self) -> None:
        """До первого сохранения обычный матч не читает ещё отсутствующие обратные связи."""
        match = Match(
            match_type=Match.MatchType.SPARRING,
            player1=self.inviter,
            player2=self.invitee,
        )
        self.assertIsNone(match.pk)

        with self.assertNumQueries(0):
            self.assertFalse(match.is_friendly_sparring())

    def test_completed_sparring_created_without_winner_remains_pending(self) -> None:
        """Существующий тестовый сценарий создания завершённого матча работает без pk."""
        match = make_completed_sparring_match(self.inviter, self.invitee)

        self.assertIsNotNone(match.pk)
        self.assertEqual(match.status, Match.MatchStatus.COMPLETED)
        self.assertIsNotNone(match.completed_datetime)
        self.assertEqual(match.rating_status, Match.RatingCalcStatus.PENDING)
        self.assertFalse(match.is_friendly_sparring())
        self._assert_unchanged_player(self.inviter, 3000)
        self._assert_unchanged_player(self.invitee, 2500)

    def test_completed_sparring_created_with_winner_calculates_once(self) -> None:
        """Первое сохранение сразу завершённого матча рассчитывает рейтинг без повторов."""
        match = Match.objects.create(
            match_type=Match.MatchType.SPARRING,
            player1=self.inviter,
            player2=self.invitee,
            winner=self.inviter,
            status=Match.MatchStatus.COMPLETED,
            player1_set1=6,
            player2_set1=4,
        )
        self.inviter.refresh_from_db()
        self.invitee.refresh_from_db()
        initial_ratings = (
            self.inviter.hidden_rating,
            self.invitee.hidden_rating,
        )
        self.assertEqual(match.rating_status, Match.RatingCalcStatus.CALCULATED)
        self.assertIsNotNone(match.completed_datetime)

        match.save()

        self.inviter.refresh_from_db()
        self.invitee.refresh_from_db()
        self.assertEqual(
            (self.inviter.matches_played, self.invitee.matches_played), (1, 1)
        )
        self.assertEqual((self.inviter.matches_won, self.invitee.matches_won), (1, 0))
        self.assertEqual(
            (self.inviter.hidden_rating, self.invitee.hidden_rating), initial_ratings
        )

    def test_completed_sparring_with_friendly_response_reads_source_before_pk(
        self,
    ) -> None:
        """Защита обратных связей не теряет дружеский режим прямого FK при создании."""
        request = SparringRequest.objects.create(player=self.inviter, is_friendly=True)
        response = SparringResponse.objects.create(
            sparring_request=request,
            respondent=self.invitee,
            contact_method=SparringResponse.ContactMethod.TELEGRAM,
        )
        match = Match(
            match_type=Match.MatchType.SPARRING,
            sparring_response=response,
            player1=self.inviter,
            player2=self.invitee,
            winner=self.inviter,
            status=Match.MatchStatus.COMPLETED,
            player1_set1=6,
            player2_set1=4,
        )
        self.assertIsNone(match.pk)
        self.assertTrue(match.is_friendly_sparring())

        match.save()

        self._assert_friendly_result(match)
