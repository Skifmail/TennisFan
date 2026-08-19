"""E2E: публичные страницы турниров и матчей."""

from datetime import date, timedelta

from django.conf import settings
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.clubs.models import (
    Club,
)
from apps.tournaments.models import (
    Match,
    Tournament,
    TournamentStatus,
)
from apps.users.models import Player, User


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
        self.assertNotContains(response, completed.name)
        self.assertContains(
            response,
            reverse("tournament_register", kwargs={"slug": open_tournament.slug}),
        )

        completed_filter = self.client.get(
            reverse("tournament_list"),
            {"status": "completed"},
            secure=True,
        )
        self.assertEqual(completed_filter.status_code, 200)
        self.assertContains(completed_filter, completed.name)
        self.assertContains(completed_filter, "Турнир завершён")

        archive = self.client.get(reverse("tournament_archive"), secure=True)
        self.assertEqual(archive.status_code, 200)
        self.assertContains(archive, "Архив турниров")
        self.assertContains(archive, completed.name)
        self.assertNotContains(archive, open_tournament.name)

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


class MyMatchesOrderingTestCase(TestCase):
    """Ближайший матч первым в детальном списке «Мои матчи»."""

    def setUp(self) -> None:
        self.client = Client()
        self.user = User.objects.create_user(
            email="my-matches-order@test.local",
            password="x",
        )
        self.player = Player.objects.create(user=self.user)
        self.opponent = Player.objects.create(
            user=User.objects.create_user(
                email="my-matches-order-op@test.local",
                password="x",
            )
        )
        self.client.force_login(self.user)
        self.tournament = Tournament.objects.create(
            name="Турнир сортировки матчей",
            slug="my-matches-order-tm",
            city="Москва",
            start_date=date.today(),
            format="round_robin",
        )
        now = timezone.now()
        Match.objects.create(
            tournament=self.tournament,
            player1=self.player,
            player2=self.opponent,
            status=Match.MatchStatus.SCHEDULED,
            deadline=now + timedelta(days=28),
        )
        Match.objects.create(
            tournament=self.tournament,
            player1=self.player,
            player2=self.opponent,
            status=Match.MatchStatus.SCHEDULED,
            deadline=now + timedelta(days=7),
        )

    def test_tournament_matches_sorted_nearest_first(self) -> None:
        url = reverse("my_matches") + f"?tournament={self.tournament.slug}"
        response = self.client.get(url, secure=True)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        near = (timezone.now() + timedelta(days=7)).strftime("%d.%m.%Y")
        far = (timezone.now() + timedelta(days=28)).strftime("%d.%m.%Y")
        self.assertLess(content.index(near), content.index(far))


class TournamentPublicListOrderingTestCase(TestCase):
    """Активные турниры выше предстоящих на главной и в /tournaments/."""

    def setUp(self) -> None:
        self.client = Client()
        self.active = Tournament.objects.create(
            name="Активный Воскресенск тест",
            slug="ordering-active-rr",
            city="Москва",
            start_date=date(2020, 1, 1),
            format="round_robin",
            status=TournamentStatus.ACTIVE,
        )
        self.upcoming = Tournament.objects.create(
            name="Набор Раменский тест",
            slug="ordering-upcoming-olympic",
            city="Москва",
            start_date=date.today() + timedelta(days=30),
            format="single_elimination",
            status=TournamentStatus.UPCOMING,
        )
        self.cancelled = Tournament.objects.create(
            name="Отменённый Королёв тест",
            slug="ordering-cancelled-rr",
            city="Москва",
            start_date=date.today() + timedelta(days=60),
            format="round_robin",
            status=TournamentStatus.CANCELLED,
        )

    def test_tournament_list_shows_active_before_upcoming(self) -> None:
        response = self.client.get(reverse("tournament_list"), secure=True)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertLess(
            content.index(self.active.name),
            content.index(self.upcoming.name),
        )

    def test_tournament_list_shows_cancelled_last(self) -> None:
        response = self.client.get(reverse("tournament_list"), secure=True)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertLess(
            content.index(self.active.name),
            content.index(self.cancelled.name),
        )
        self.assertLess(
            content.index(self.upcoming.name),
            content.index(self.cancelled.name),
        )

    def test_home_shows_active_before_upcoming(self) -> None:
        response = self.client.get(reverse("home"), secure=True)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertLess(
            content.index(self.active.name),
            content.index(self.upcoming.name),
        )


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
        self.assertContains(response, "Сохранить результат")
        self.assertContains(response, "Неявка соперника")
        self.assertContains(response, "Моя неявка")


@override_settings(
    STORAGES={
        **settings.STORAGES,
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    }
)
class TournamentMatchResultOrderTestCase(TestCase):
    """Результаты в турнире вносятся по порядку дедлайнов."""

    def setUp(self) -> None:
        self.client = Client()
        self.user = User.objects.create_user(
            email="order-result@test.local",
            password="testpass123",
        )
        self.player = Player.objects.create(user=self.user)
        self.opponent = Player.objects.create(
            user=User.objects.create_user(
                email="order-result-op@test.local",
                password="testpass123",
            )
        )
        self.tournament = Tournament.objects.create(
            name="Порядок результатов",
            slug="result-order-rr",
            city="Москва",
            start_date=date.today(),
            format="round_robin",
            status=TournamentStatus.ACTIVE,
        )
        now = timezone.now()
        self.early_match = Match.objects.create(
            tournament=self.tournament,
            player1=self.player,
            player2=self.opponent,
            status=Match.MatchStatus.SCHEDULED,
            deadline=now + timedelta(days=7),
            round_index=1,
        )
        self.late_match = Match.objects.create(
            tournament=self.tournament,
            player1=self.player,
            player2=self.opponent,
            status=Match.MatchStatus.SCHEDULED,
            deadline=now + timedelta(days=14),
            round_index=2,
        )
        self.client.force_login(self.user)
        self._proposal_payload = {
            "result": "win",
            "p1s1": "6",
            "p2s1": "4",
            "p1s2": "6",
            "p2s2": "3",
        }

    def test_cannot_propose_result_for_later_match_first(self) -> None:
        response = self.client.post(
            reverse("propose_result", kwargs={"pk": self.late_match.pk}),
            self._proposal_payload,
            secure=True,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.late_match.result_proposals.count(), 0)
        detail = self.client.get(
            reverse("match_detail", kwargs={"pk": self.late_match.pk}),
            secure=True,
        )
        self.assertContains(detail, "Сначала завершите матч")
        self.assertNotContains(detail, 'name="p1s1"')

    def test_can_propose_after_earlier_match_completed(self) -> None:
        self.early_match.status = Match.MatchStatus.COMPLETED
        self.early_match.winner = self.player
        self.early_match.player1_set1 = 6
        self.early_match.player2_set1 = 4
        self.early_match.player1_set2 = 6
        self.early_match.player2_set2 = 3
        self.early_match.save()

        response = self.client.post(
            reverse("propose_result", kwargs={"pk": self.late_match.pk}),
            self._proposal_payload,
            secure=True,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.late_match.result_proposals.count(), 1)


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
