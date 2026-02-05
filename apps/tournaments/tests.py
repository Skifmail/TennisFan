"""
Тесты для FAN- и олимпийской систем (продвижение победителей, bye).
"""

from django.test import TestCase

from apps.tournaments.fan import _expected_final_round, generate_bracket as fan_generate_bracket
from apps.tournaments.models import Match, Tournament
from apps.tournaments.olympic_consolation import generate_bracket as olympic_generate_bracket
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
        ok, _ = fan_generate_bracket(t)
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

    def test_no_double_bye_5_players(self) -> None:
        """
        При 5 участниках один получает bye в R1. Во 2-м круге после завершения
        всех матчей R1 и первого матча R2 заглушка «игрок vs Bye» должна
        заполниться вторым игроком, чтобы в R2 не оставалось Bye.
        """
        from datetime import date

        t = Tournament.objects.create(
            name="FAN 5",
            slug="fan-5",
            city="Москва",
            start_date=date.today(),
            format="single_elimination",
            bracket_generated=False,
            max_participants=8,
        )
        t.participants.set(self.players[:5])
        ok, _ = fan_generate_bracket(t)
        self.assertTrue(ok, "Сетка должна сформироваться")

        # Завершаем все R1
        for m in t.matches.filter(round_index=1, is_consolation=False).order_by("round_order"):
            if m.status in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER):
                continue
            w = (
                m.player1
                if getattr(m.player2, "is_bye", False)
                else (m.player1 if m.player1.total_points >= m.player2.total_points else m.player2)
            )
            m.winner = w
            m.status = Match.MatchStatus.COMPLETED
            m.save()

        # Завершаем первый матч R2 (тот, где уже два реальных игрока).
        # Это должно обновить заглушку второго слота R2 (победитель R1 match 3 vs Bye).
        for m in t.matches.filter(round_index=2, is_consolation=False).order_by("round_order"):
            if m.status in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER):
                continue
            if getattr(m.player1, "is_bye", False) or getattr(m.player2, "is_bye", False):
                continue  # заглушка — заполнится после завершения другого R2
            w = m.player1 if m.player1.total_points >= m.player2.total_points else m.player2
            m.winner = w
            m.status = Match.MatchStatus.COMPLETED
            m.save()
            break  # один матч достаточно, чтобы триггернуть обновление заглушки

        # Во 2-м круге не должно остаться матча, где один из игроков — Bye.
        r2_matches = t.matches.filter(round_index=2, is_consolation=False)
        for m in r2_matches:
            self.assertFalse(
                getattr(m.player1, "is_bye", False),
                "R2: player1 не должен быть Bye (нет двойного bye)",
            )
            self.assertFalse(
                getattr(m.player2, "is_bye", False),
                "R2: player2 не должен быть Bye (нет двойного bye)",
            )


class OlympicAdvanceWinnerTestCase(TestCase):
    """Олимпийская система: основная сетка продвигается через fan — двойной bye не должен появляться."""

    def setUp(self) -> None:
        self.bye_player = Player.objects.filter(is_bye=True).first()
        self.assertIsNotNone(self.bye_player, "Bye-игрок должен существовать (миграция)")
        self.players = []
        for i in range(10):
            u = User.objects.create_user(email=f"olympic{i}@test.local", password="x")
            p = Player.objects.create(user=u, total_points=1000 - i * 50)
            self.players.append(p)

    def test_olympic_no_double_bye_5_players(self) -> None:
        """
        Олимпийская система при 5 участниках: основная сетка как FAN.
        Во 2-м круге основной сетки не должно остаться матча с Bye после
        завершения «полного» матча R2 (логика заглушек из fan используется).
        """
        from datetime import date

        t = Tournament.objects.create(
            name="Olympic 5",
            slug="olympic-5",
            city="Москва",
            start_date=date.today(),
            format="olympic_consolation",
            bracket_generated=False,
            max_participants=8,
        )
        t.participants.set(self.players[:5])
        ok, _ = olympic_generate_bracket(t)
        self.assertTrue(ok, "Основная сетка олимпийского турнира должна сформироваться")

        # Завершаем все R1 основной сетки
        for m in t.matches.filter(round_index=1, is_consolation=False).order_by("round_order"):
            if m.status in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER):
                continue
            w = (
                m.player1
                if getattr(m.player2, "is_bye", False)
                else (m.player1 if m.player1.total_points >= m.player2.total_points else m.player2)
            )
            m.winner = w
            m.status = Match.MatchStatus.COMPLETED
            m.save()

        # Завершаем первый матч R2 основной сетки (два реальных игрока)
        for m in t.matches.filter(round_index=2, is_consolation=False).order_by("round_order"):
            if m.status in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER):
                continue
            if getattr(m.player1, "is_bye", False) or getattr(m.player2, "is_bye", False):
                continue
            w = m.player1 if m.player1.total_points >= m.player2.total_points else m.player2
            m.winner = w
            m.status = Match.MatchStatus.COMPLETED
            m.save()
            break

        # В основной сетке R2 не должно быть Bye
        for m in t.matches.filter(round_index=2, is_consolation=False):
            self.assertFalse(
                getattr(m.player1, "is_bye", False),
                "Olympic R2: player1 не должен быть Bye",
            )
            self.assertFalse(
                getattr(m.player2, "is_bye", False),
                "Olympic R2: player2 не должен быть Bye",
            )
