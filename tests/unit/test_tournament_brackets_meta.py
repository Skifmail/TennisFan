"""Юнит-тесты: метаданные сетки, дедлайны, slug, продвижение победителей."""

from datetime import date, datetime, timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.tournaments.fan import _expected_final_round
from apps.tournaments.fan import generate_bracket as fan_generate_bracket
from apps.tournaments.models import (
    Match,
    Tournament,
    TournamentStatus,
)
from apps.tournaments.olympic_consolation import (
    generate_bracket as olympic_generate_bracket,
)
from apps.tournaments.round_robin import (
    generate_bracket as round_robin_generate_bracket,
)
from apps.tournaments.utils import (
    generate_unique_tournament_slug,
    mark_tournament_bracket_generated,
    recalculate_tournament_match_deadlines,
    tournament_deadline_schedule_start,
)
from apps.users.models import Player, User


class MarkTournamentBracketGeneratedTestCase(TestCase):
    """Тесты смены статуса турнира после формирования сетки."""

    def setUp(self) -> None:
        self.tournament = Tournament.objects.create(
            name="Status test",
            slug="status-test",
            city="Москва",
            start_date=date.today(),
            format="round_robin",
            status=TournamentStatus.UPCOMING,
        )

    def test_marks_bracket_and_sets_active_from_upcoming(self) -> None:
        mark_tournament_bracket_generated(self.tournament)
        self.tournament.refresh_from_db()
        self.assertTrue(self.tournament.bracket_generated)
        self.assertEqual(self.tournament.status, TournamentStatus.ACTIVE)

    def test_does_not_change_non_upcoming_status(self) -> None:
        self.tournament.status = TournamentStatus.COMPLETED
        self.tournament.save(update_fields=["status"])
        mark_tournament_bracket_generated(self.tournament)
        self.tournament.refresh_from_db()
        self.assertEqual(self.tournament.status, TournamentStatus.COMPLETED)


class TournamentDeadlineScheduleStartTestCase(TestCase):
    """Дедлайны матчей не уходят в прошлое, если сетка создана позже start_date."""

    def test_schedule_start_uses_today_when_start_date_in_past(self) -> None:
        tournament = Tournament.objects.create(
            name="Поздний старт",
            slug="late-start-deadline",
            city="Москва",
            start_date=date(2026, 4, 28),
            format="round_robin",
        )
        with patch(
            "apps.tournaments.utils.timezone.localdate",
            return_value=date(2026, 5, 30),
        ):
            start = tournament_deadline_schedule_start(tournament)
        self.assertEqual(start.date(), date(2026, 5, 30))

    def test_schedule_start_keeps_future_start_date(self) -> None:
        tournament = Tournament.objects.create(
            name="Будущий старт",
            slug="future-start-deadline",
            city="Москва",
            start_date=date(2026, 6, 15),
            format="round_robin",
        )
        with patch(
            "apps.tournaments.utils.timezone.localdate",
            return_value=date(2026, 5, 30),
        ):
            start = tournament_deadline_schedule_start(tournament)
        self.assertEqual(start.date(), date(2026, 6, 15))

    def test_round_robin_deadlines_from_actual_bracket_date(self) -> None:
        players = [
            Player.objects.create(
                user=User.objects.create_user(
                    email=f"rr-deadline-{i}@test.local",
                    password="x",
                )
            )
            for i in range(3)
        ]
        tournament = Tournament.objects.create(
            name="Круговой дедлайны",
            slug="rr-deadline-bracket",
            city="Москва",
            start_date=date(2026, 4, 28),
            format="round_robin",
            match_days_per_round=7,
        )
        tournament.participants.set(players)
        with patch(
            "apps.tournaments.utils.timezone.localdate",
            return_value=date(2026, 5, 30),
        ):
            schedule_start = tournament_deadline_schedule_start(tournament)
            ok, msg = round_robin_generate_bracket(tournament)
        self.assertTrue(ok, msg)
        match = tournament.matches.order_by("round_index", "pk").first()
        assert match is not None
        expected_deadline = (schedule_start + timedelta(days=7)).date()
        self.assertEqual(
            timezone.localtime(match.deadline).date(),
            expected_deadline,
        )

    def test_recalculate_updates_existing_match_deadlines(self) -> None:
        players = [
            Player.objects.create(
                user=User.objects.create_user(
                    email=f"rr-recalc-{i}@test.local",
                    password="x",
                )
            )
            for i in range(3)
        ]
        tournament = Tournament.objects.create(
            name="Пересчёт дедлайнов",
            slug="rr-recalc-deadline",
            city="Москва",
            start_date=date(2026, 4, 28),
            format="round_robin",
            match_days_per_round=7,
            bracket_generated=True,
        )
        tournament.participants.set(players)
        old_deadline = timezone.make_aware(datetime(2026, 5, 5, 12, 0, 0))
        match = Match.objects.create(
            tournament=tournament,
            round_index=1,
            round_name="Тур 1",
            player1=players[0],
            player2=players[1],
            status=Match.MatchStatus.SCHEDULED,
            deadline=old_deadline,
        )
        with patch(
            "apps.tournaments.utils.timezone.localdate",
            return_value=date(2026, 5, 30),
        ):
            updated = recalculate_tournament_match_deadlines(tournament)
            schedule_start = tournament_deadline_schedule_start(tournament)
        self.assertEqual(updated, 1)
        match.refresh_from_db()
        self.assertEqual(
            timezone.localtime(match.deadline).date(),
            (schedule_start + timedelta(days=7)).date(),
        )


class GenerateUniqueTournamentSlugTestCase(TestCase):
    """Тесты автогенерации уникального slug для турниров."""

    def test_first_tournament_gets_base_slug(self) -> None:
        """Первый турнир с названием получает slug без суффикса."""
        slug = generate_unique_tournament_slug(name="Кубок Москвы")
        self.assertEqual(slug, "кубок-москвы")

    def test_duplicate_name_gets_suffix(self) -> None:
        """Второй турнир с тем же названием получает slug с суффиксом -2."""
        Tournament.objects.create(
            name="Кубок Москвы",
            slug="кубок-москвы",
            city="Москва",
            start_date=date.today(),
            format="single_elimination",
        )
        slug = generate_unique_tournament_slug(name="Кубок Москвы")
        self.assertEqual(slug, "кубок-москвы-2")

    def test_third_tournament_gets_suffix_3(self) -> None:
        """Третий турнир с тем же названием получает slug с суффиксом -3."""
        Tournament.objects.create(
            name="Кубок Москвы",
            slug="кубок-москвы",
            city="Москва",
            start_date=date.today(),
            format="single_elimination",
        )
        Tournament.objects.create(
            name="Кубок Москвы",
            slug="кубок-москвы-2",
            city="Москва",
            start_date=date.today(),
            format="single_elimination",
        )
        slug = generate_unique_tournament_slug(name="Кубок Москвы")
        self.assertEqual(slug, "кубок-москвы-3")

    def test_long_name_truncated_with_suffix(self) -> None:
        """Длинное название обрезается, оставляя место для суффикса."""
        long_name = "Любительский Северо-Восточный кубок Москвы по теннису (Москва)"
        slug = generate_unique_tournament_slug(name=long_name)
        self.assertLessEqual(len(slug), 50)
        self.assertTrue(slug.replace("-", "").isalnum() or slug.endswith("-2"))

    def test_editing_existing_keeps_slug(self) -> None:
        """При редактировании турнира его slug сохраняется, если уникален."""
        t = Tournament.objects.create(
            name="Мой турнир",
            slug="moy-turnir",
            city="Москва",
            start_date=date.today(),
            format="single_elimination",
        )
        slug = generate_unique_tournament_slug(
            name="Мой турнир", slug="moy-turnir", instance=t
        )
        self.assertEqual(slug, "moy-turnir")

    def test_prepopulated_latin_slug_gets_suffix_on_duplicate(self) -> None:
        """При prepopulated Latin slug (как в админке) дубликат получает суффикс -2."""
        base_slug = "kubok-moskvy-2025"
        Tournament.objects.create(
            name="Кубок Москвы 2025",
            slug=base_slug,
            city="Москва",
            start_date=date.today(),
            format="single_elimination",
        )
        slug = generate_unique_tournament_slug(
            name="Кубок Москвы 2025",
            slug=base_slug,
        )
        self.assertEqual(slug, "kubok-moskvy-2025-2")


class FanAdvanceWinnerTestCase(TestCase):
    """Тесты advance_winner: финал не должен содержать Bye."""

    def setUp(self) -> None:
        """Bye-игрок создаётся миграцией 0016. Создать 10 участников."""
        self.bye_player = Player.objects.filter(is_bye=True).first()
        self.assertIsNotNone(
            self.bye_player, "Bye-игрок должен существовать (миграция)"
        )
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
            if m.status not in (
                Match.MatchStatus.COMPLETED,
                Match.MatchStatus.WALKOVER,
            ):
                w = (
                    m.player1
                    if getattr(m.player2, "is_bye", False)
                    else (
                        m.player1
                        if m.player1.total_points >= m.player2.total_points
                        else m.player2
                    )
                )
                m.winner = w
                m.status = Match.MatchStatus.COMPLETED
                m.save()

        # Завершить R2, R3
        for ri in [2, 3]:
            for m in t.matches.filter(round_index=ri, is_consolation=False):
                if m.status not in (
                    Match.MatchStatus.COMPLETED,
                    Match.MatchStatus.WALKOVER,
                ):
                    w = (
                        m.player1
                        if getattr(m.player2, "is_bye", False)
                        else (
                            m.player1
                            if m.player1.total_points >= m.player2.total_points
                            else m.player2
                        )
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
        for m in t.matches.filter(round_index=1, is_consolation=False).order_by(
            "round_order"
        ):
            if m.status in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER):
                continue
            w = (
                m.player1
                if getattr(m.player2, "is_bye", False)
                else (
                    m.player1
                    if m.player1.total_points >= m.player2.total_points
                    else m.player2
                )
            )
            m.winner = w
            m.status = Match.MatchStatus.COMPLETED
            m.save()

        # Завершаем первый матч R2 (тот, где уже два реальных игрока).
        # Это должно обновить заглушку второго слота R2 (победитель R1 match 3 vs Bye).
        for m in t.matches.filter(round_index=2, is_consolation=False).order_by(
            "round_order"
        ):
            if m.status in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER):
                continue
            if getattr(m.player1, "is_bye", False) or getattr(
                m.player2, "is_bye", False
            ):
                continue  # заглушка — заполнится после завершения другого R2
            w = (
                m.player1
                if m.player1.total_points >= m.player2.total_points
                else m.player2
            )
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
        self.assertIsNotNone(
            self.bye_player, "Bye-игрок должен существовать (миграция)"
        )
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
        for m in t.matches.filter(round_index=1, is_consolation=False).order_by(
            "round_order"
        ):
            if m.status in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER):
                continue
            w = (
                m.player1
                if getattr(m.player2, "is_bye", False)
                else (
                    m.player1
                    if m.player1.total_points >= m.player2.total_points
                    else m.player2
                )
            )
            m.winner = w
            m.status = Match.MatchStatus.COMPLETED
            m.save()

        # Завершаем первый матч R2 основной сетки (два реальных игрока)
        for m in t.matches.filter(round_index=2, is_consolation=False).order_by(
            "round_order"
        ):
            if m.status in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER):
                continue
            if getattr(m.player1, "is_bye", False) or getattr(
                m.player2, "is_bye", False
            ):
                continue
            w = (
                m.player1
                if m.player1.total_points >= m.player2.total_points
                else m.player2
            )
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
