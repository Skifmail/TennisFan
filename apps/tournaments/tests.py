"""
Тесты для FAN- и олимпийской систем (продвижение победителей, bye).
"""

from datetime import date

from django.test import TestCase

from apps.tournaments.fan import _bracket_r1_count, _expected_final_round
from apps.tournaments.fan import generate_bracket as fan_generate_bracket
from apps.tournaments.models import Match, Tournament, TournamentStatus
from apps.tournaments.olympic_consolation import (
    generate_bracket as olympic_generate_bracket,
)
from apps.users.models import Player, User


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


def _complete_match(m: Match) -> None:
    """Установить победителя (по рейтингу или не-Bye) и сохранить матч."""
    if getattr(m.player2, "is_bye", False):
        w = m.player1
    elif getattr(m.player1, "is_bye", False):
        w = m.player2
    else:
        w = m.player1 if m.player1.total_points >= m.player2.total_points else m.player2
    m.winner = w
    m.status = Match.MatchStatus.COMPLETED
    m.save()


class FanBracketToFinalTestCase(TestCase):
    """
    Проверка создания сеток и доведения турниров до финала
    при количестве участников от 3 до 16.
    """

    def setUp(self) -> None:
        self.bye_player = Player.objects.filter(is_bye=True).first()
        self.assertIsNotNone(
            self.bye_player,
            "Bye-игрок должен существовать (миграция 0016)",
        )
        self.players = []
        for i in range(16):
            u = User.objects.create_user(
                email=f"bracket{i}@test.local",
                password="x",
            )
            # Рейтинг убывает: 0 — сильнейший, 15 — слабейший
            p = Player.objects.create(user=u, total_points=6000 - i * 200)
            self.players.append(p)

    def test_bracket_and_final_for_3_to_16_players(self) -> None:
        """
        Для N = 3..16: создаём турнир, формируем сетку, завершаем все матчи
        по раундам (по round_order). Финал должен существовать и содержать
        двух реальных игроков (без Bye). Всего матчей в основной сетке: N-1.
        """
        for n in range(3, 17):
            with self.subTest(n_players=n):
                slug = f"fan-bracket-{n}"
                t = Tournament.objects.create(
                    name=f"FAN {n} участников",
                    slug=slug,
                    city="Москва",
                    start_date=date.today(),
                    format="single_elimination",
                    bracket_generated=False,
                    max_participants=20,
                )
                t.participants.set(self.players[:n])

                ok, msg = fan_generate_bracket(t)
                self.assertTrue(ok, f"N={n}: сетка должна сформироваться: {msg}")

                # Ожидаемое количество матчей R1 (бинарное дерево: bracket_size/2)
                r1_expected = _bracket_r1_count(n)
                r1_matches = t.matches.filter(
                    round_index=1, is_consolation=False
                ).order_by("round_order")
                self.assertEqual(
                    r1_matches.count(),
                    r1_expected,
                    f"N={n}: в 1-м круге должно быть {r1_expected} матчей",
                )

                # Ожидаемый раунд финала
                final_round = _expected_final_round(t)
                max_round = 5  # с запасом для 16 участников
                completed_rounds = set()

                # Завершаем матчи по раундам и по порядку (round_order).
                # После каждого завершения матча сетка может обновиться (заглушка Bye
                # заменяется на игрока), поэтому заново запрашиваем матчи и завершаем
                # по одному за итерацию, чтобы не перезаписать обновлённые данные.
                for ri in range(1, max_round + 1):
                    while True:
                        to_complete = list(
                            t.matches.filter(
                                round_index=ri,
                                is_consolation=False,
                            )
                            .exclude(
                                status__in=(
                                    Match.MatchStatus.COMPLETED,
                                    Match.MatchStatus.WALKOVER,
                                )
                            )
                            .order_by("round_order")
                            .select_related("player1", "player2", "winner")[:1]
                        )
                        if not to_complete:
                            break
                        _complete_match(to_complete[0])
                    completed_rounds.add(ri)
                    if ri == final_round:
                        break

                # Финал: ровно один матч в раунде финала
                finals = list(
                    t.matches.filter(round_index=final_round, is_consolation=False)
                )
                self.assertEqual(
                    len(finals),
                    1,
                    f"N={n}: в финале (round_index={final_round}) должен быть ровно 1 матч, получено {len(finals)}",
                )
                final = finals[0]
                self.assertFalse(
                    getattr(final.player1, "is_bye", False),
                    f"N={n}: в финале player1 не должен быть Bye",
                )
                self.assertFalse(
                    getattr(final.player2, "is_bye", False),
                    f"N={n}: в финале player2 не должен быть Bye",
                )

                # В основной сетке матчей не меньше N-1 (при нечётном N возможен лишний матч с Bye)
                main_matches_count = (
                    t.matches.filter(is_consolation=False)
                    .exclude(status=Match.MatchStatus.CANCELLED)
                    .count()
                )
                self.assertGreaterEqual(
                    main_matches_count,
                    n - 1,
                    f"N={n}: в основной сетке должно быть не меньше n-1 = {n - 1} матчей",
                )

                # В финале (и только в нём) не должно быть Bye; в промежуточных раундах
                # при нечётном числе участников возможны заглушки (игрок vs Bye) до заполнения
                for m in t.matches.filter(
                    is_consolation=False, round_index=final_round
                ).select_related("player1", "player2"):
                    self.assertFalse(
                        getattr(m.player1, "is_bye", False),
                        f"N={n}: в финале player1 не должен быть Bye",
                    )
                    self.assertFalse(
                        getattr(m.player2, "is_bye", False),
                        f"N={n}: в финале player2 не должен быть Bye",
                    )


class OlympicBracketToFinalTestCase(TestCase):
    """
    Проверка олимпийской системы: создание основной сетки и доведение до финала
    при количестве участников от 3 до 16 (основная сетка как у FAN + утешительная).
    """

    def setUp(self) -> None:
        self.bye_player = Player.objects.filter(is_bye=True).first()
        self.assertIsNotNone(
            self.bye_player,
            "Bye-игрок должен существовать (миграция 0016)",
        )
        self.players = []
        for i in range(16):
            u = User.objects.create_user(
                email=f"olympic-bracket{i}@test.local",
                password="x",
            )
            p = Player.objects.create(user=u, total_points=6000 - i * 200)
            self.players.append(p)

    def test_olympic_main_bracket_and_final_3_to_16_players(self) -> None:
        """
        Для N = 3..16: олимпийский турнир, основная сетка формируется как FAN,
        завершаем все матчи основной сетки по раундам. Финал основной сетки
        должен существовать и содержать двух реальных игроков (без Bye).
        """
        for n in range(3, 17):
            with self.subTest(n_players=n):
                slug = f"olympic-bracket-{n}"
                t = Tournament.objects.create(
                    name=f"Olympic {n} участников",
                    slug=slug,
                    city="Москва",
                    start_date=date.today(),
                    format="olympic_consolation",
                    bracket_generated=False,
                    max_participants=20,
                )
                t.participants.set(self.players[:n])

                ok, msg = olympic_generate_bracket(t)
                self.assertTrue(
                    ok,
                    f"N={n}: олимпийская сетка должна сформироваться: {msg}",
                )

                r1_expected = _bracket_r1_count(n)
                r1_matches = t.matches.filter(
                    round_index=1, is_consolation=False
                ).order_by("round_order")
                self.assertEqual(
                    r1_matches.count(),
                    r1_expected,
                    f"N={n}: в 1-м круге основной сетки должно быть {r1_expected} матчей",
                )

                final_round = _expected_final_round(t)
                max_round = 5

                for ri in range(1, max_round + 1):
                    while True:
                        to_complete = list(
                            t.matches.filter(
                                round_index=ri,
                                is_consolation=False,
                            )
                            .exclude(
                                status__in=(
                                    Match.MatchStatus.COMPLETED,
                                    Match.MatchStatus.WALKOVER,
                                )
                            )
                            .order_by("round_order")
                            .select_related("player1", "player2", "winner")[:1]
                        )
                        if not to_complete:
                            break
                        _complete_match(to_complete[0])
                    if ri == final_round:
                        break

                finals = list(
                    t.matches.filter(round_index=final_round, is_consolation=False)
                )
                self.assertEqual(
                    len(finals),
                    1,
                    f"N={n}: в финале основной сетки (round_index={final_round}) должен быть ровно 1 матч, получено {len(finals)}",
                )
                final = finals[0]
                self.assertFalse(
                    getattr(final.player1, "is_bye", False),
                    f"N={n}: в финале основной сетки player1 не должен быть Bye",
                )
                self.assertFalse(
                    getattr(final.player2, "is_bye", False),
                    f"N={n}: в финале основной сетки player2 не должен быть Bye",
                )


class TVDTestCase(TestCase):
    """Тесты турнира выходного дня: группы, змейка, standings, плей-офф."""

    def setUp(self) -> None:
        self.players = []
        for i in range(12):
            u = User.objects.create_user(
                email=f"tvd{i}@test.local",
                password="x",
            )
            p = Player.objects.create(user=u, total_points=1200 - i * 80)
            self.players.append(p)

    def test_calculate_group_structure(self) -> None:
        from apps.tournaments.tvd import calculate_group_structure

        self.assertEqual(calculate_group_structure(6), [3, 3])
        self.assertEqual(calculate_group_structure(8), [3, 3, 2])
        self.assertEqual(calculate_group_structure(9), [3, 3, 3])
        self.assertEqual(calculate_group_structure(12), [3, 3, 3, 3])
        self.assertLessEqual(len(calculate_group_structure(10)), 6)
        self.assertGreaterEqual(len(calculate_group_structure(10)), 2)

    def test_serpentine_distribution(self) -> None:
        from apps.tournaments.tvd import serpentine_distribution

        dist = serpentine_distribution(self.players[:6], 2)
        self.assertEqual(len(dist), 2)
        self.assertEqual(len(dist[0]) + len(dist[1]), 6)
        # Сильнейший (первый по total_points) в первой группе
        self.assertIn(self.players[0], dist[0])

    def test_tvd_generate_groups_and_standings(self) -> None:
        from apps.tournaments.tvd import generate_groups, recalculate_group_standings

        t = Tournament.objects.create(
            name="TVD Test",
            slug="tvd-test",
            city="Москва",
            start_date=date.today(),
            format="weekend_day",
            duration="weekend",
            bracket_generated=False,
            max_participants=12,
        )
        t.participants.set(self.players[:8])
        ok, msg = generate_groups(t)
        self.assertTrue(ok, msg)
        self.assertTrue(t.bracket_generated)
        groups = list(t.tvd_groups.order_by("order"))
        self.assertGreaterEqual(len(groups), 2)
        self.assertLessEqual(len(groups), 6)
        for g in groups:
            members = list(g.members.order_by("seed"))
            self.assertGreaterEqual(len(members), 2)
            self.assertLessEqual(len(members), 4)
        group_matches = t.matches.filter(tvd_stage="group")
        self.assertGreater(group_matches.count(), 0)
        # Завершить один матч и пересчитать группу
        m = group_matches.first()
        if m and m.player1_id and m.player2_id:
            m.winner = m.player1
            m.status = Match.MatchStatus.COMPLETED
            m.player1_set1 = 6
            m.player2_set1 = 4
            m.save()
            recalculate_group_standings(m.tvd_group)
            g = m.tvd_group
            m1 = g.members.get(player=m.player1)
            self.assertEqual(m1.wins, 1)
            self.assertEqual(m1.losses, 0)

    def test_tvd_playoffs_after_groups(self) -> None:
        from apps.tournaments.tvd import (
            generate_groups,
            generate_playoffs,
            is_group_stage_complete,
        )

        t = Tournament.objects.create(
            name="TVD Playoff Test",
            slug="tvd-playoff-test",
            city="Москва",
            start_date=date.today(),
            format="weekend_day",
            duration="weekend",
            bracket_generated=False,
            max_participants=8,
        )
        t.participants.set(self.players[:8])
        ok, _ = generate_groups(t)
        self.assertTrue(ok)
        # Завершить все групповые матчи и выставить места
        for m in t.matches.filter(tvd_stage="group"):
            m.winner = m.player1
            m.status = Match.MatchStatus.COMPLETED
            m.player1_set1 = 6
            m.player2_set1 = 4
            m.save()
        from apps.tournaments.tvd import recalculate_group_standings

        for g in t.tvd_groups.all():
            recalculate_group_standings(g)
        self.assertTrue(is_group_stage_complete(t))
        ok2, msg = generate_playoffs(t)
        self.assertTrue(ok2, msg)
        main_stages = (
            "main_qf",
            "main_sf",
            "main_final",
            "third_place",
            "main_round_robin",
            "main_rr_1_3",
            "main_rr_4_6",
        )
        main = t.matches.filter(is_consolation=False, tvd_stage__in=main_stages)
        self.assertGreater(main.count(), 0)


def _complete_tvd_group_stage(tournament: Tournament) -> None:
    """Завершить все групповые матчи (победитель — player1), пересчитать места в группах."""
    from apps.tournaments.tvd import recalculate_group_standings

    for m in tournament.matches.filter(tvd_stage="group"):
        m.winner = m.player1
        m.status = Match.MatchStatus.COMPLETED
        m.player1_set1 = 6
        m.player2_set1 = 4
        m.save()
    for g in tournament.tvd_groups.all():
        recalculate_group_standings(g)


class TVDParticipantCountTestCase(TestCase):
    """Тесты ТВД для числа участников от 4 до 32: формирование групп и плей-офф (олимпийский формат)."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.players = []
        for i in range(33):
            u = User.objects.create_user(
                email=f"tvd_n{i}@test.local",
                password="x",
            )
            p = Player.objects.create(user=u, total_points=1500 - i * 40)
            cls.players.append(p)

    def test_group_structure_for_each_participant_count(self) -> None:
        """Для n от 4 до 32: число групп и распределение участников после generate_groups корректны."""
        from apps.tournaments.tvd import calculate_group_structure, generate_groups

        for n in range(4, 33):
            with self.subTest(n=n):
                t = Tournament.objects.create(
                    name=f"TVD n={n}",
                    slug=f"tvd-n-{n}",
                    city="Москва",
                    start_date=date.today(),
                    format="weekend_day",
                    duration="weekend",
                    bracket_generated=False,
                    max_participants=n + 5,
                )
                t.participants.set(self.players[:n])
                ok, msg = generate_groups(t)
                self.assertTrue(ok, f"n={n}: {msg}")
                expected_sizes = calculate_group_structure(n)
                group_count = t.tvd_groups.count()
                self.assertEqual(
                    len(expected_sizes), group_count, f"n={n}: число групп"
                )
                actual_sizes = [
                    g.members.count() for g in t.tvd_groups.order_by("order")
                ]
                total_in_groups = sum(actual_sizes)
                for sz in actual_sizes:
                    self.assertGreaterEqual(
                        sz, 2, f"n={n}: в группе минимум 2 участника"
                    )
                self.assertLessEqual(
                    total_in_groups,
                    n,
                    f"n={n}: в группах не больше n участников (получено {total_in_groups})",
                )
                self.assertGreaterEqual(
                    total_in_groups,
                    group_count * 2,
                    f"n={n}: все группы заполнены (минимум 2 в каждой)",
                )

    def test_playoffs_olympic_for_each_participant_count(self) -> None:
        """Для n от 4 до 32: после завершения групп плей-офф (олимпийский) создаётся без ошибок."""
        from apps.tournaments.tvd import (
            generate_groups,
            generate_playoffs,
            is_group_stage_complete,
        )

        for n in range(4, 33):
            with self.subTest(n=n):
                t = Tournament.objects.create(
                    name=f"TVD playoff n={n}",
                    slug=f"tvd-playoff-n-{n}",
                    city="Москва",
                    start_date=date.today(),
                    format="weekend_day",
                    duration="weekend",
                    bracket_generated=False,
                    max_participants=n + 5,
                )
                t.participants.set(self.players[:n])
                ok1, msg1 = generate_groups(t)
                self.assertTrue(ok1, f"n={n}: {msg1}")
                _complete_tvd_group_stage(t)
                self.assertTrue(
                    is_group_stage_complete(t),
                    f"n={n}: групповой этап должен быть завершён",
                )
                ok2, msg2 = generate_playoffs(t, skip_consolation=False)
                self.assertTrue(ok2, f"n={n}: {msg2}")
                self.assertEqual(
                    t.status, TournamentStatus.PLAYOFFS, f"n={n}: статус PLAYOFFS"
                )
                main_stages = (
                    "main_qf",
                    "main_sf",
                    "main_final",
                    "third_place",
                    "main_round_robin",
                    "main_rr_1_3",
                    "main_rr_4_6",
                )
                main_count = t.matches.filter(
                    is_consolation=False, tvd_stage__in=main_stages
                ).count()
                self.assertGreater(
                    main_count, 0, f"n={n}: должны быть матчи основной сетки"
                )

    def test_playoffs_circular_main_for_participant_counts_with_circular_option(
        self,
    ) -> None:
        """Проверка создания основной сетки в круговом формате (2, 3, 4 группы — все в один круг)."""
        from apps.tournaments.tvd import (
            generate_groups,
            generate_main_bracket,
            is_group_stage_complete,
        )

        for n in (4, 6, 8, 9, 12):
            with self.subTest(n=n):
                t = Tournament.objects.create(
                    name=f"TVD circular n={n}",
                    slug=f"tvd-circular-n-{n}",
                    city="Москва",
                    start_date=date.today(),
                    format="weekend_day",
                    duration="weekend",
                    bracket_generated=False,
                    max_participants=n + 5,
                )
                t.participants.set(self.players[:n])
                ok1, _ = generate_groups(t)
                self.assertTrue(ok1, f"n={n}")
                _complete_tvd_group_stage(t)
                self.assertTrue(is_group_stage_complete(t), f"n={n}")
                ok2, msg = generate_main_bracket(t, bracket_format="circular")
                self.assertTrue(ok2, f"n={n}: {msg}")
                rr_matches = t.matches.filter(
                    is_consolation=False,
                    tvd_stage="main_round_robin",
                )
                self.assertGreater(
                    rr_matches.count(),
                    0,
                    f"n={n}: должны быть матчи круговой основной сетки",
                )
