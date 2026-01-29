"""
Тесты для FAN-турниров.
"""

from django.test import TestCase

from apps.tournaments.fan import _expected_final_round, generate_bracket
from apps.tournaments.models import Match, Tournament
from apps.users.models import Player, User


class FanAdvanceWinnerTestCase(TestCase):
    """Тесты advance_winner: финал не должен содержать Bye."""

    def setUp(self) -> None:
        """Bye-игрок создаётся миграцией 0016. Создать 10 участников."""
        self.bye_player = Player.objects.filter(is_bye=True).first()
        self.assertIsNotNone(self.bye_player, "Bye-игрок должен существовать (миграция)")
        self.players = []
        for i in range(10):
            u = User.objects.create_user(
                email=f"p{i}@test.local",
                password="x",
            )
            p = Player.objects.create(user=u, total_points=1000 - i * 50)
            self.players.append(p)

    def test_final_has_no_bye_10_players(self) -> None:
        """
        При 10 участниках финал должен быть между двумя реальными игроками,
        а не между победителем и Bye (баг: каскад R1.5→R2.3→R3.2→R4 создавал
        финал с Bye до завершения второго полуфинала).
        """
        from datetime import date

        t = Tournament.objects.create(
            name="Test FAN 10",
            slug="test-fan-10",
            city="Москва",
            start_date=date.today(),
            format="single_elimination",
            bracket_generated=False,
            max_participants=10,
        )
        t.participants.set(self.players)
        ok, _ = generate_bracket(t)
        self.assertTrue(ok, "Сетка должна сформироваться")

        # Завершить все R1
        for m in t.matches.filter(round_index=1, is_consolation=False):
            if m.status not in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER):
                w = m.player1 if getattr(m.player2, "is_bye", False) else (
                    m.player1 if m.player1.total_points >= m.player2.total_points else m.player2
                )
                m.winner = w
                m.status = Match.MatchStatus.COMPLETED
                m.save()

        # Завершить R2, R3
        for ri in [2, 3]:
            for m in t.matches.filter(round_index=ri, is_consolation=False):
                if m.status not in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER):
                    w = m.player1 if getattr(m.player2, "is_bye", False) else (
                        m.player1 if m.player1.total_points >= m.player2.total_points else m.player2
                    )
                    m.winner = w
                    m.status = Match.MatchStatus.COMPLETED
                    m.save()

        # Финал должен существовать и не содержать Bye
        final = t.matches.filter(round_index=4, is_consolation=False).first()
        self.assertIsNotNone(final, "Финал должен быть создан")
        self.assertFalse(
            getattr(final.player1, "is_bye", False),
            "Финал: player1 не должен быть Bye",
        )
        self.assertFalse(
            getattr(final.player2, "is_bye", False),
            "Финал: player2 не должен быть Bye",
        )

    def test_expected_final_round(self) -> None:
        """Ожидаемый раунд финала: ceil(log2(N))."""
        from datetime import date

        t = Tournament.objects.create(
            name="T",
            slug="t",
            city="Москва",
            start_date=date.today(),
            format="single_elimination",
        )
        t.participants.set(self.players[:2])
        self.assertEqual(_expected_final_round(t), 1)
        t.participants.set(self.players[:8])
        self.assertEqual(_expected_final_round(t), 3)  # ceil(log2(8))=3
        t.participants.set(self.players[:10])
        self.assertEqual(_expected_final_round(t), 4)
