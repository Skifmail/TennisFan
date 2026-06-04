"""Интеграционные тесты: формат TVD (группы и плей-офф)."""

from datetime import date

from django.test import TestCase

from apps.tournaments.models import (
    Match,
    Tournament,
    TournamentStatus,
)
from apps.users.models import Player, User
from tests.support.matches import complete_tvd_group_stage


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
                complete_tvd_group_stage(t)
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
                complete_tvd_group_stage(t)
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
