"""
Тесты для FAN- и олимпийской систем (продвижение победителей, bye).
"""

from datetime import date, timedelta
from typing import cast
from urllib.parse import urlencode

from django.conf import settings
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.clubs.models import (
    Club,
    ClubMember,
    ClubMemberPlan,
    ClubMemberRole,
    ClubMemberStatus,
    ClubPlayerPlan,
)
from apps.clubs.plan_services import assign_member_plan
from apps.subscriptions.models import SubscriptionTier, UserSubscription
from apps.tournaments.fan import _bracket_r1_count, _expected_final_round
from apps.tournaments.fan import generate_bracket as fan_generate_bracket
from apps.tournaments.models import (
    Match,
    Tournament,
    TournamentPostpaymentInvoice,
    TournamentRegistrationCoverage,
    TournamentStatus,
)
from apps.tournaments.olympic_consolation import (
    generate_bracket as olympic_generate_bracket,
)
from apps.tournaments.postpayment import (
    get_pending_postpayment_users,
    mark_registration_covered,
)
from apps.tournaments.utils import generate_unique_tournament_slug
from apps.users.models import Player, SkillLevel, User


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


class MyMatchesVisibilityTestCase(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(
            email="viewer@test.local",
            password="testpass123",
        )
        self.player = Player.objects.create(user=self.user)
        self.opponent = Player.objects.create(
            user=User.objects.create_user(
                email="opponent@test.local",
                password="testpass123",
            )
        )
        self.client.force_login(self.user)

    def test_my_matches_hides_club_tournaments(self) -> None:
        global_tournament = Tournament.objects.create(
            name="Глобальный турнир",
            slug="global-visible",
            city="Москва",
            start_date=date.today(),
            format="single_elimination",
        )
        club = Club.objects.create(
            name="Клуб",
            slug="club-hidden",
            city="Москва",
            address="ул. Пушкина, 1",
            email="club@test.local",
            admin_name="Администратор клуба",
        )
        club_tournament = Tournament.objects.create(
            name="Клубный турнир",
            slug="club-hidden-tournament",
            city="Москва",
            club=club,
            start_date=date.today(),
            format="single_elimination",
        )

        Match.objects.create(
            tournament=global_tournament,
            player1=self.player,
            player2=self.opponent,
            status=Match.MatchStatus.SCHEDULED,
        )
        Match.objects.create(
            tournament=club_tournament,
            player1=self.player,
            player2=self.opponent,
            status=Match.MatchStatus.SCHEDULED,
        )

        response = self.client.get(reverse("my_matches"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Глобальный турнир")
        self.assertNotContains(response, "Клубный турнир")


class TournamentListCardStateTestCase(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.user = User.objects.create_user(
            email="list-user@test.local",
            password="testpass123",
        )
        self.player = Player.objects.create(user=self.user, skill_level="amateur")
        self.opponent = Player.objects.create(
            user=User.objects.create_user(
                email="list-opponent@test.local",
                password="testpass123",
            )
        )
        self.client.force_login(self.user)

    def test_tournament_list_shows_actual_registration_state(self) -> None:
        registered = Tournament.objects.create(
            name="Уже записан",
            slug="already-registered",
            city="Москва",
            start_date=date.today(),
            format="single_elimination",
            status=TournamentStatus.UPCOMING,
        )
        registered.allowed_categories.create(category="amateur")
        registered.participants.add(self.player)

        bracket_closed = Tournament.objects.create(
            name="Сетка сформирована",
            slug="bracket-closed",
            city="Москва",
            start_date=date.today(),
            format="single_elimination",
            status=TournamentStatus.UPCOMING,
            bracket_generated=True,
        )
        bracket_closed.allowed_categories.create(category="amateur")

        completed = Tournament.objects.create(
            name="Завершённый турнир",
            slug="completed-tournament",
            city="Москва",
            start_date=date.today(),
            format="single_elimination",
            status=TournamentStatus.COMPLETED,
        )
        completed.allowed_categories.create(category="amateur")

        open_tournament = Tournament.objects.create(
            name="Открытый турнир",
            slug="open-tournament",
            city="Москва",
            start_date=date.today(),
            format="single_elimination",
            status=TournamentStatus.UPCOMING,
            is_one_day=True,
        )
        open_tournament.allowed_categories.create(category="amateur")

        response = self.client.get(reverse("tournament_list"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Вы записаны")
        self.assertContains(response, "Регистрация закрыта")
        self.assertContains(response, "Турнир завершён")
        self.assertContains(
            response,
            reverse("tournament_register", kwargs={"slug": open_tournament.slug}),
        )

    def test_tournament_list_club_filter_limits_results(self) -> None:
        club = Club.objects.create(
            name="Фильтр-клуб",
            slug="list-filter-club",
            city="Москва",
            address="ул. 1",
            email="c@test.local",
            admin_name="Админ",
        )
        club_tm = Tournament.objects.create(
            name="Клубный для фильтра",
            slug="list-club-filtered",
            city="Москва",
            start_date=date.today(),
            format="single_elimination",
            status=TournamentStatus.UPCOMING,
            club=club,
        )
        club_tm.allowed_categories.create(category="amateur")
        platform_tm = Tournament.objects.create(
            name="Платформенный для фильтра",
            slug="list-platform-filtered",
            city="Москва",
            start_date=date.today(),
            format="single_elimination",
            status=TournamentStatus.UPCOMING,
        )
        platform_tm.allowed_categories.create(category="amateur")

        r_platform = self.client.get(
            reverse("tournament_list"),
            {"club": "__platform__"},
            secure=True,
        )
        self.assertEqual(r_platform.status_code, 200)
        self.assertContains(r_platform, platform_tm.name)
        self.assertNotContains(r_platform, club_tm.name)

        r_club_only = self.client.get(
            reverse("tournament_list"),
            {"club": "__club_only__"},
            secure=True,
        )
        self.assertEqual(r_club_only.status_code, 200)
        self.assertContains(r_club_only, club_tm.name)
        self.assertNotContains(r_club_only, platform_tm.name)

        r_club = self.client.get(
            reverse("tournament_list"),
            {"club": club.slug},
            secure=True,
        )
        self.assertEqual(r_club.status_code, 200)
        self.assertContains(r_club, club_tm.name)
        self.assertNotContains(r_club, platform_tm.name)


class TournamentTablesListFiltersTestCase(TestCase):
    """Фильтры на странице «Турнирные таблицы»."""

    def setUp(self) -> None:
        self.client = Client()
        self.club = Club.objects.create(
            name="Клуб таблиц",
            slug="tables-filter-club",
            city="Москва",
            address="ул. 1",
            email="t@test.local",
            admin_name="Админ",
        )
        self.club_tournament = Tournament.objects.create(
            name="Клубный турнир таблиц",
            slug="tables-club-tm",
            city="Москва",
            start_date=date.today(),
            format="round_robin",
            status=TournamentStatus.UPCOMING,
            club=self.club,
        )
        self.platform_tournament = Tournament.objects.create(
            name="Платформенный турнир таблиц",
            slug="tables-platform-tm",
            city="Сочи",
            start_date=date.today(),
            format="round_robin",
            status=TournamentStatus.UPCOMING,
        )

    def test_tables_list_platform_filter_excludes_club_tournaments(self) -> None:
        url = reverse("tournament_tables_list")
        response = self.client.get(url, {"club": "__platform__"}, secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.platform_tournament.name)
        self.assertNotContains(response, self.club_tournament.name)

    def test_tables_list_club_only_filter_excludes_platform_tournaments(self) -> None:
        url = reverse("tournament_tables_list")
        response = self.client.get(url, {"club": "__club_only__"}, secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.club_tournament.name)
        self.assertNotContains(response, self.platform_tournament.name)

    def test_tables_list_city_filter(self) -> None:
        url = reverse("tournament_tables_list")
        response = self.client.get(url, {"city": "Сочи"}, secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.platform_tournament.name)
        self.assertNotContains(response, self.club_tournament.name)


class MatchDetailPlayerActionsTestCase(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.user = User.objects.create_user(
            email="match-player@test.local",
            password="testpass123",
        )
        self.player = Player.objects.create(user=self.user)
        self.opponent = Player.objects.create(
            user=User.objects.create_user(
                email="match-opponent@test.local",
                password="testpass123",
            )
        )
        self.tournament = Tournament.objects.create(
            name="Турнир для матча",
            slug="match-detail-actions",
            city="Москва",
            start_date=date.today(),
            format="round_robin",
        )
        self.match = Match.objects.create(
            tournament=self.tournament,
            player1=self.player,
            player2=self.opponent,
            status=Match.MatchStatus.SCHEDULED,
        )
        self.client.force_login(self.user)

    def test_match_detail_shows_result_form_for_participant(self) -> None:
        response = self.client.get(
            reverse("match_detail", kwargs={"pk": self.match.pk}),
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Действия игрока")
        self.assertContains(response, "Отправить на подтверждение")


@override_settings(
    STORAGES={
        **settings.STORAGES,
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    }
)
class ClubTournamentRegistrationWithoutGlobalSubscriptionTestCase(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.user = User.objects.create_user(
            email="club-member@test.local",
            password="testpass123",
            first_name="Леонид",
            last_name="Ермолаев",
            phone="+79990000000",
        )
        self.player = Player.objects.create(
            user=self.user,
            skill_level="amateur",
            birth_date=date(1990, 1, 1),
        )
        self.club = Club.objects.create(
            name="Спартак",
            slug="spartak-club",
            city="Москва",
            address="ул. Спортивная, 1",
            email="club@test.local",
            admin_name="Админ клуба",
        )
        self.member = ClubMember.objects.create(
            club=self.club,
            user=self.user,
            role=ClubMemberRole.PLAYER,
            status=ClubMemberStatus.ACTIVE,
        )
        self.plan = ClubPlayerPlan.objects.create(
            club=self.club,
            name="Стандарт",
            monthly_fee=1000,
            max_tournaments_per_month=3,
            is_active=True,
        )
        assign_member_plan(self.member, self.plan)
        self.client.force_login(self.user)

    def _create_club_tournament(
        self,
        *,
        slug: str,
        entry_fee: int = 0,
        is_one_day: bool = False,
        variant: str = "singles",
    ) -> Tournament:
        tournament = Tournament.objects.create(
            name=f"Турнир {slug}",
            slug=slug,
            city="Москва",
            club=self.club,
            start_date=date.today(),
            format="round_robin",
            status=TournamentStatus.UPCOMING,
            gender="open",
            is_one_day=is_one_day,
            entry_fee=entry_fee,
            variant=variant,
        )
        tournament.allowed_categories.create(category="amateur")
        return cast(Tournament, tournament)

    def _clear_member_plan(self) -> None:
        ClubMemberPlan.objects.filter(club_member=self.member).delete()

    def _create_global_subscription(self, *, slots: int = 5) -> None:
        tier = SubscriptionTier.objects.create(
            name="club-test-tier",
            display_name="Club Test Tier",
            price=990,
            max_tournaments=slots,
            duration_days=30,
            is_visible=True,
        )
        UserSubscription.objects.create(
            user=self.user,
            tier=tier,
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(days=30),
            is_active=True,
            tournament_registration_balance=slots,
        )

    def _assert_tournament_payment_redirect(
        self,
        response,
        tournament: Tournament,
        *,
        next_url: str = "",
    ) -> None:
        params: dict[str, str | int] = {
            "type": "tournament",
            "id": tournament.id,
        }
        if next_url:
            params["next"] = next_url
        expected_url = f"{reverse('payment_preview')}?{urlencode(params)}"
        self.assertRedirects(
            response,
            expected_url,
            fetch_redirect_response=False,
        )

    def test_member_can_register_for_multiday_club_tournament_without_global_subscription(
        self,
    ) -> None:
        tournament = self._create_club_tournament(
            slug="club-multiday-no-global-sub",
            entry_fee=0,
            is_one_day=False,
        )

        response = self.client.post(
            reverse("tournament_register", kwargs={"slug": tournament.slug}),
            secure=True,
        )

        self.assertRedirects(
            response,
            reverse("tournament_detail", kwargs={"slug": tournament.slug}),
            fetch_redirect_response=False,
        )
        self.assertTrue(tournament.participants.filter(pk=self.player.pk).exists())

    def test_member_can_register_doubles_team_for_club_tournament_without_global_subscription(
        self,
    ) -> None:
        partner_user = User.objects.create_user(
            email="club-partner@test.local",
            password="testpass123",
            first_name="Иван",
            last_name="Петров",
            phone="+79990000001",
        )
        partner = Player.objects.create(
            user=partner_user,
            skill_level="amateur",
            birth_date=date(1991, 1, 1),
        )
        partner_member = ClubMember.objects.create(
            club=self.club,
            user=partner_user,
            role=ClubMemberRole.PLAYER,
            status=ClubMemberStatus.ACTIVE,
        )
        assign_member_plan(partner_member, self.plan)

        tournament = self._create_club_tournament(
            slug="club-doubles-no-global-sub",
            entry_fee=0,
            is_one_day=False,
            variant="doubles",
        )

        response = self.client.post(
            reverse("tournament_register_doubles", kwargs={"slug": tournament.slug}),
            {"action": "add_partner", "partner_id": str(partner.pk)},
            secure=True,
        )

        self.assertRedirects(
            response,
            reverse("tournament_detail", kwargs={"slug": tournament.slug}),
            fetch_redirect_response=False,
        )
        self.assertTrue(
            tournament.teams.filter(player1=self.player, player2=partner).exists()
        )

    def test_club_doubles_registration_uses_club_shell_without_global_footer(
        self,
    ) -> None:
        tournament = self._create_club_tournament(
            slug="club-doubles-shell",
            entry_fee=0,
            is_one_day=False,
            variant="doubles",
        )

        response = self.client.get(
            reverse("tournament_register_doubles", kwargs={"slug": tournament.slug}),
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.club.name)
        self.assertContains(response, "Личный кабинет")
        self.assertContains(response, "body--club-panel")
        self.assertNotContains(response, "РЕЙТИНГ")

    def test_club_doubles_partner_search_is_limited_to_active_club_members(
        self,
    ) -> None:
        club_partner_user = User.objects.create_user(
            email="club-search-partner@test.local",
            password="testpass123",
            first_name="Анна",
            last_name="Клубная",
            phone="+79990000002",
        )
        club_partner = Player.objects.create(
            user=club_partner_user,
            skill_level="amateur",
            birth_date=date(1991, 1, 1),
        )
        ClubMember.objects.create(
            club=self.club,
            user=club_partner_user,
            role=ClubMemberRole.PLAYER,
            status=ClubMemberStatus.ACTIVE,
        )

        outsider_user = User.objects.create_user(
            email="platform-search-partner@test.local",
            password="testpass123",
            first_name="Анна",
            last_name="Платформа",
            phone="+79990000003",
        )
        outsider = Player.objects.create(
            user=outsider_user,
            skill_level="amateur",
            birth_date=date(1992, 1, 1),
        )

        tournament = self._create_club_tournament(
            slug="club-doubles-search",
            entry_fee=0,
            is_one_day=False,
            variant="doubles",
        )

        response = self.client.get(
            reverse("tournament_register_doubles", kwargs={"slug": tournament.slug}),
            {"q": "Анна"},
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, club_partner.user.last_name)
        self.assertNotContains(response, outsider.user.last_name)
        self.assertNotContains(response, f'value="{outsider.pk}"')

    def test_club_doubles_registration_rejects_partner_outside_club(self) -> None:
        outsider_user = User.objects.create_user(
            email="outsider-post@test.local",
            password="testpass123",
            first_name="Анна",
            last_name="Внешняя",
        )
        outsider = Player.objects.create(
            user=outsider_user,
            skill_level="amateur",
            birth_date=date(1992, 1, 1),
        )

        tournament = self._create_club_tournament(
            slug="club-doubles-post-check",
            entry_fee=0,
            is_one_day=False,
            variant="doubles",
        )

        response = self.client.post(
            reverse("tournament_register_doubles", kwargs={"slug": tournament.slug}),
            {"action": "add_partner", "partner_id": str(outsider.pk)},
            secure=True,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Для клубного турнира можно выбрать партнёра только из активных участников клуба.",
        )
        self.assertFalse(
            tournament.teams.filter(player1=self.player, player2=outsider).exists()
        )

    def test_member_without_club_plan_cannot_register_for_free_multiday_club_tournament(
        self,
    ) -> None:
        self._clear_member_plan()
        tournament = self._create_club_tournament(
            slug="club-multiday-free-no-plan",
            entry_fee=0,
            is_one_day=False,
        )

        response = self.client.post(
            reverse("tournament_register", kwargs={"slug": tournament.slug}),
            secure=True,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Для участия в турнирах клуба нужно выбрать тариф.",
        )
        self.assertFalse(tournament.participants.filter(pk=self.player.pk).exists())

    def test_member_with_expired_club_plan_cannot_register_for_free_multiday_club_tournament(
        self,
    ) -> None:
        assignment = ClubMemberPlan.objects.get(
            club_member=self.member, status="active"
        )
        assignment.ended_at = timezone.now() - timedelta(minutes=1)
        assignment.save(update_fields=["ended_at"])
        tournament = self._create_club_tournament(
            slug="club-multiday-free-expired-plan",
            entry_fee=0,
            is_one_day=False,
        )

        response = self.client.post(
            reverse("tournament_register", kwargs={"slug": tournament.slug}),
            secure=True,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Для участия в турнирах клуба нужно выбрать тариф.",
        )
        self.assertFalse(tournament.participants.filter(pk=self.player.pk).exists())

    def test_member_without_club_plan_is_redirected_to_payment_for_paid_multiday_club_tournament(
        self,
    ) -> None:
        self._clear_member_plan()
        tournament = self._create_club_tournament(
            slug="club-multiday-paid-no-plan",
            entry_fee=700,
            is_one_day=False,
        )

        response = self.client.post(
            reverse("tournament_register", kwargs={"slug": tournament.slug}),
            secure=True,
        )

        self._assert_tournament_payment_redirect(response, tournament)
        self.assertFalse(tournament.participants.filter(pk=self.player.pk).exists())

    def test_member_without_club_plan_can_register_for_free_one_day_club_tournament(
        self,
    ) -> None:
        self._clear_member_plan()
        tournament = self._create_club_tournament(
            slug="club-one-day-free-no-plan",
            entry_fee=0,
            is_one_day=True,
        )

        response = self.client.post(
            reverse("tournament_register", kwargs={"slug": tournament.slug}),
            secure=True,
        )

        self.assertRedirects(
            response,
            reverse("tournament_detail", kwargs={"slug": tournament.slug}),
            fetch_redirect_response=False,
        )
        self.assertTrue(tournament.participants.filter(pk=self.player.pk).exists())

    def test_member_without_club_plan_is_redirected_to_payment_for_paid_one_day_club_tournament(
        self,
    ) -> None:
        self._clear_member_plan()
        tournament = self._create_club_tournament(
            slug="club-one-day-paid-no-plan",
            entry_fee=500,
            is_one_day=True,
        )

        response = self.client.post(
            reverse("tournament_register", kwargs={"slug": tournament.slug}),
            secure=True,
        )

        self._assert_tournament_payment_redirect(response, tournament)
        self.assertFalse(tournament.participants.filter(pk=self.player.pk).exists())

    def test_global_subscription_does_not_replace_club_plan_for_free_multiday_club_tournament(
        self,
    ) -> None:
        self._clear_member_plan()
        self._create_global_subscription(slots=10)
        tournament = self._create_club_tournament(
            slug="club-multiday-free-global-sub",
            entry_fee=0,
            is_one_day=False,
        )

        response = self.client.post(
            reverse("tournament_register", kwargs={"slug": tournament.slug}),
            secure=True,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Для участия в турнирах клуба нужно выбрать тариф.",
        )
        self.assertFalse(tournament.participants.filter(pk=self.player.pk).exists())

    def test_global_subscription_does_not_replace_club_plan_for_paid_multiday_club_tournament(
        self,
    ) -> None:
        self._clear_member_plan()
        self._create_global_subscription(slots=10)
        tournament = self._create_club_tournament(
            slug="club-multiday-paid-global-sub",
            entry_fee=900,
            is_one_day=False,
        )

        response = self.client.post(
            reverse("tournament_register", kwargs={"slug": tournament.slug}),
            secure=True,
        )

        self._assert_tournament_payment_redirect(response, tournament)
        self.assertFalse(tournament.participants.filter(pk=self.player.pk).exists())

    def test_club_member_with_plan_can_register_for_paid_multiday_club_tournament_without_global_subscription(
        self,
    ) -> None:
        tournament = self._create_club_tournament(
            slug="club-multiday-paid-with-plan",
            entry_fee=850,
            is_one_day=False,
        )

        response = self.client.post(
            reverse("tournament_register", kwargs={"slug": tournament.slug}),
            secure=True,
        )

        self.assertRedirects(
            response,
            reverse("tournament_detail", kwargs={"slug": tournament.slug}),
            fetch_redirect_response=False,
        )
        self.assertTrue(tournament.participants.filter(pk=self.player.pk).exists())

    def test_member_without_club_plan_can_register_when_club_player_plans_disabled(
        self,
    ) -> None:
        self._clear_member_plan()
        self.club.use_player_plans = False
        self.club.save(update_fields=["use_player_plans"])
        tournament = self._create_club_tournament(
            slug="club-multiday-free-plans-disabled",
            entry_fee=0,
            is_one_day=False,
        )

        response = self.client.post(
            reverse("tournament_register", kwargs={"slug": tournament.slug}),
            secure=True,
        )

        self.assertRedirects(
            response,
            reverse("tournament_detail", kwargs={"slug": tournament.slug}),
            fetch_redirect_response=False,
        )
        self.assertTrue(tournament.participants.filter(pk=self.player.pk).exists())

    def test_paid_club_tournament_redirects_to_payment_when_club_player_plans_disabled(
        self,
    ) -> None:
        self._clear_member_plan()
        self.club.use_player_plans = False
        self.club.save(update_fields=["use_player_plans"])
        tournament = self._create_club_tournament(
            slug="club-multiday-paid-plans-disabled",
            entry_fee=950,
            is_one_day=False,
        )

        response = self.client.post(
            reverse("tournament_register", kwargs={"slug": tournament.slug}),
            secure=True,
        )

        self._assert_tournament_payment_redirect(response, tournament)
        self.assertFalse(tournament.participants.filter(pk=self.player.pk).exists())

    def test_member_without_club_plan_is_redirected_to_payment_for_paid_doubles_club_tournament(
        self,
    ) -> None:
        self._clear_member_plan()
        tournament = self._create_club_tournament(
            slug="club-doubles-paid-no-plan",
            entry_fee=650,
            is_one_day=False,
            variant="doubles",
        )
        next_url = f"https://testserver{reverse('tournament_register_doubles', kwargs={'slug': tournament.slug})}"

        response = self.client.get(
            reverse("tournament_register_doubles", kwargs={"slug": tournament.slug}),
            secure=True,
        )

        self._assert_tournament_payment_redirect(
            response,
            tournament,
            next_url=next_url,
        )


class MatchDetailPlayerActionsRedirectTestCase(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.user = User.objects.create_user(
            email="match-player-2@test.local",
            password="testpass123",
        )
        self.player = Player.objects.create(user=self.user)
        self.opponent = Player.objects.create(
            user=User.objects.create_user(
                email="match-opponent-2@test.local",
                password="testpass123",
            )
        )
        self.tournament = Tournament.objects.create(
            name="Турнир для матча 2",
            slug="match-detail-actions-2",
            city="Москва",
            start_date=date.today(),
            format="round_robin",
        )
        self.match = Match.objects.create(
            tournament=self.tournament,
            player1=self.player,
            player2=self.opponent,
            status=Match.MatchStatus.SCHEDULED,
        )
        self.client.force_login(self.user)

    def test_propose_result_redirects_back_to_match_detail_when_next_passed(
        self,
    ) -> None:
        next_url = reverse("match_detail", kwargs={"pk": self.match.pk})

        response = self.client.post(
            reverse("propose_result", kwargs={"pk": self.match.pk}),
            {
                "next": next_url,
                "result": "win",
                "p1s1": "6",
                "p2s1": "4",
                "p1s2": "6",
                "p2s2": "3",
            },
            secure=True,
        )

        self.assertRedirects(response, next_url, fetch_redirect_response=False)
        self.assertEqual(self.match.result_proposals.count(), 1)

    def test_match_detail_works_for_match_without_tournament(self) -> None:
        sparring_match = Match.objects.create(
            player1=self.player,
            player2=self.opponent,
            match_type=Match.MatchType.SPARRING,
            status=Match.MatchStatus.SCHEDULED,
        )

        response = self.client.get(
            reverse("match_detail", kwargs={"pk": sparring_match.pk}),
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Действия игрока")


class TournamentPostpaymentServiceTestCase(TestCase):
    """Тесты сервиса постоплаты турнира."""

    def setUp(self) -> None:
        self.user = User.objects.create_user(email="postpay@test.local", password="x")
        self.user2 = User.objects.create_user(email="postpay2@test.local", password="x")
        self.player = Player.objects.create(
            user=self.user, skill_level=SkillLevel.AMATEUR
        )
        self.player2 = Player.objects.create(
            user=self.user2, skill_level=SkillLevel.AMATEUR
        )
        self.tournament = Tournament.objects.create(
            name="Postpayment test",
            slug="postpayment-test",
            city="Москва",
            start_date=date.today(),
            format="single_elimination",
            entry_fee=1000,
            allow_postpayment=True,
            is_one_day=False,
            max_participants=32,
        )
        self.tournament.allowed_categories.create(category=SkillLevel.AMATEUR)
        self.tournament.participants.add(self.player, self.player2)

    def test_get_pending_postpayment_users_returns_uncovered_players(self) -> None:
        pending_users = get_pending_postpayment_users(self.tournament)
        self.assertEqual({u.id for u in pending_users}, {self.user.id, self.user2.id})

    def test_mark_registration_covered_excludes_user_from_pending(self) -> None:
        mark_registration_covered(
            self.tournament,
            self.user,
            TournamentRegistrationCoverage.CoverageType("subscription_slot"),
        )
        pending_users = get_pending_postpayment_users(self.tournament)
        self.assertEqual({u.id for u in pending_users}, {self.user2.id})

    def test_paid_invoice_excludes_user_from_pending(self) -> None:
        TournamentPostpaymentInvoice.objects.create(
            tournament=self.tournament,
            user=self.user2,
            amount=1000,
            due_at=timezone.now() + timedelta(hours=12),
            status=TournamentPostpaymentInvoice.Status.PAID,
        )
        pending_users = get_pending_postpayment_users(self.tournament)
        self.assertEqual({u.id for u in pending_users}, {self.user.id})
