"""Порядок игроков в публичном рейтинге: победы → матчи → стаж."""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.sports import Sport
from apps.users.models import Player, PlayerSportProfile
from tests.support.factories import make_player


class RatingOrderTestCase(TestCase):
    """Свежий игрок с высоким уровнем силы не попадает в топ без матчей."""

    def setUp(self) -> None:
        self.champion = self._make_player(
            suffix="champion",
            first_name="Чемпион",
            last_name="Турниров",
            points=3000.0,
            wins=10,
            matches=20,
            days_on_platform=10,
        )
        self.veteran = self._make_player(
            suffix="veteran",
            first_name="Опытный",
            last_name="Игрок",
            points=2000.0,
            wins=5,
            matches=10,
            days_on_platform=100,
        )
        self.active_loser = self._make_player(
            suffix="active-loser",
            first_name="Активный",
            last_name="БезПобед",
            points=1000.0,
            wins=0,
            matches=50,
            days_on_platform=200,
        )
        self.old_zero = self._make_player(
            suffix="old-zero",
            first_name="Старый",
            last_name="БезМатчей",
            points=1500.0,
            wins=0,
            matches=0,
            days_on_platform=400,
        )
        self.newbie = self._make_player(
            suffix="newbie",
            first_name="Новый",
            last_name="Участник",
            points=7000.0,
            wins=0,
            matches=0,
            days_on_platform=0,
        )

    def _make_player(
        self,
        *,
        suffix: str,
        first_name: str,
        last_name: str,
        points: float,
        wins: int,
        matches: int,
        days_on_platform: int,
    ) -> Player:
        """Создать видимого игрока с результатами и датой регистрации."""
        player = make_player(
            email_suffix=suffix,
            first_name=first_name,
            last_name=last_name,
            points=points,
        )
        Player.objects.filter(pk=player.pk).update(
            matches_won=wins,
            matches_played=matches,
            is_verified=True,
            created_at=timezone.now() - timedelta(days=days_on_platform),
        )
        player.refresh_from_db()
        return player

    def test_rating_page_sorts_by_results_not_starting_strength(self) -> None:
        """Страница рейтинга ставит нового игрока с силой 7.00 в конец."""
        response = self.client.get(reverse("rating"), secure=True)

        self.assertEqual(response.status_code, 200)
        context = response.context
        assert context is not None
        names = [str(player) for player in context["players_page"].object_list]
        self.assertEqual(
            names,
            [
                "Чемпион Турниров",
                "Опытный Игрок",
                "Активный БезПобед",
                "Старый БезМатчей",
                "Новый Участник",
            ],
        )

    def test_home_top_uses_same_results_order(self) -> None:
        """Топ на главной строится по той же формуле, а не по FAN."""
        response = self.client.get(reverse("home"), secure=True)

        self.assertEqual(response.status_code, 200)
        context = response.context
        assert context is not None
        names = [str(player) for player in context["top_players"]]
        self.assertEqual(names[0], "Чемпион Турниров")
        self.assertEqual(names[-1], "Новый Участник")

    def test_missing_tennis_strength_excludes_only_new_player(self) -> None:
        """Незаданный уровень скрывает новичка, сохраняя порядок остальных."""
        Player.objects.filter(pk__in=[self.newbie.pk, self.champion.pk]).update(
            tennis_entry_level_set=False
        )
        for route, key in (("home", "top_players"), ("rating", "players_page")):
            response = self.client.get(reverse(route), secure=True)
            players = response.context[key]
            if key == "players_page":
                players = players.object_list
            ids = [player.pk for player in players]
            self.assertNotIn(self.newbie.pk, ids)
            self.assertEqual(ids[0], self.champion.pk)


class PadelRatingOrderTestCase(TestCase):
    """Для падела балл считается по статистике профиля вида спорта."""

    def setUp(self) -> None:
        self.champion = self._make_padel_player(
            suffix="padel-champion",
            first_name="Падел",
            last_name="Чемпион",
            points=3000.0,
            wins=10,
            matches=20,
            days_on_platform=10,
        )
        self.veteran = self._make_padel_player(
            suffix="padel-veteran",
            first_name="Падел",
            last_name="Ветеран",
            points=2000.0,
            wins=5,
            matches=10,
            days_on_platform=100,
        )
        self.newbie = self._make_padel_player(
            suffix="padel-newbie",
            first_name="Падел",
            last_name="Новичок",
            points=7000.0,
            wins=0,
            matches=0,
            days_on_platform=0,
        )

    def _make_padel_player(
        self,
        *,
        suffix: str,
        first_name: str,
        last_name: str,
        points: float,
        wins: int,
        matches: int,
        days_on_platform: int,
    ) -> Player:
        """Создать игрока с профилем падела и результатами."""
        player = make_player(
            email_suffix=suffix,
            first_name=first_name,
            last_name=last_name,
            points=0.0,
        )
        Player.objects.filter(pk=player.pk).update(
            is_verified=True,
            created_at=timezone.now() - timedelta(days=days_on_platform),
        )
        PlayerSportProfile.objects.create(
            player=player,
            sport=Sport.PADEL,
            total_points=points,
            hidden_rating=points,
            matches_won=wins,
            matches_played=matches,
            entry_level_set=True,
        )
        player.refresh_from_db()
        return player

    def test_padel_rating_uses_profile_wins(self) -> None:
        """В падел-рейтинге новый профиль с 7000 FAN уходит в конец."""
        response = self.client.get(
            reverse("rating"),
            {"sport": "padel"},
            secure=True,
        )

        self.assertEqual(response.status_code, 200)
        context = response.context
        assert context is not None
        names = [str(player) for player in context["players_page"].object_list]
        self.assertEqual(
            names,
            ["Падел Чемпион", "Падел Ветеран", "Падел Новичок"],
        )

    def test_padel_does_not_require_tennis_strength(self) -> None:
        """Отсутствие уровня тенниса не скрывает игрока в паделе."""
        Player.objects.filter(pk=self.newbie.pk).update(tennis_entry_level_set=False)
        for route, key in (("home", "top_players"), ("rating", "players_page")):
            response = self.client.get(reverse(route), {"sport": "padel"}, secure=True)
            players = response.context[key]
            if key == "players_page":
                players = players.object_list
            self.assertIn(self.newbie.pk, [player.pk for player in players])
