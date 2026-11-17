"""Дедлайны всех сеток учитывают продолжительность проведения турнира."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, time, timedelta
from itertools import product
from typing import Any
from unittest.mock import patch

from django.db.models import QuerySet
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.tournaments.fan import generate_bracket as generate_fan
from apps.tournaments.manual_pairing import (
    load_entities,
    save_manual_round,
    start_manual_tournament,
)
from apps.tournaments.manual_stages import create_qualifiers, create_two_knockouts
from apps.tournaments.models import (
    Match,
    Tournament,
    TournamentDuration,
    TournamentFormat,
    TournamentTeam,
    TournamentVariant,
)
from apps.tournaments.olympic_consolation import generate_bracket as generate_olympic
from apps.tournaments.round_robin import generate_bracket as generate_round_robin
from apps.tournaments.tvd import (
    generate_consolation_bracket,
    generate_groups,
    generate_main_bracket,
    generate_playoffs,
)
from apps.tournaments.utils import (
    generate_tournament_with_start_date,
    recalculate_tournament_match_deadlines,
)
from apps.tournaments.views import _tournament_manual_generate
from apps.users.models import Player
from tests.support.factories import make_player, make_tournament

GENERATORS: dict[str, Callable[[Tournament], tuple[bool, str]]] = {
    TournamentFormat.SINGLE_ELIMINATION: generate_fan,
    TournamentFormat.OLYMPIC_CONSOLATION: generate_olympic,
    TournamentFormat.ROUND_ROBIN: generate_round_robin,
    TournamentFormat.WEEKEND_DAY: generate_groups,
}
SHORT_DURATIONS: tuple[str, ...] = (
    TournamentDuration.SINGLE_DAY,
    TournamentDuration.WEEKEND,
)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class TournamentDurationDeadlinesTests(TestCase):
    """Проверяет основные, утешительные и ручные этапы одиночных и парных турниров."""

    players: list[Player]

    @classmethod
    def setUpTestData(cls) -> None:
        cls.players = [
            make_player(email_suffix=f"duration-deadline-{i}", points=2000 - i * 20)
            for i in range(36)
        ]

    def setUp(self) -> None:
        self.start = timezone.localdate() + timedelta(days=2)
        self.counter = 0
        for target in (
            "apps.telegram_bot.notifications.notify_tournament_start",
            "apps.telegram_bot.notifications.notify_match_created",
            "apps.telegram_bot.notifications.notify_bracket_formed",
        ):
            notification = patch(target)
            notification.start()
            self.addCleanup(notification.stop)

    def _tournament(
        self,
        tournament_format: str,
        duration: str,
        variant: str = TournamentVariant.SINGLES,
        count: int = 8,
        **overrides: Any,
    ) -> Tournament:
        """Создать турнир с достаточным числом реальных участников или команд."""
        self.counter += 1
        tournament = make_tournament(
            slug=f"duration-deadlines-{self.counter}",
            start_date=self.start,
            format=tournament_format,
            duration=duration,
            variant=variant,
            entry_fee=1000,
            match_days_per_round=7,
            max_participants=32,
            max_teams=32,
            **overrides,
        )
        if variant == TournamentVariant.DOUBLES:
            tournament.participants.set(self.players[: count * 2])
            for i in range(count):
                TournamentTeam.objects.create(
                    tournament=tournament,
                    player1=self.players[i * 2],
                    player2=self.players[i * 2 + 1],
                )
        else:
            tournament.participants.set(self.players[:count])
        return tournament

    @staticmethod
    def _matches(tournament: Tournament) -> QuerySet[Match]:
        """Выбрать матчи без зависимости от динамических атрибутов модели."""
        return Match.objects.filter(tournament=tournament)

    def _expected_short_deadline(self, tournament: Tournament) -> datetime:
        """Конец последнего дня проведения в текущем часовом поясе."""
        end = tournament.end_date
        assert end is not None
        return timezone.make_aware(datetime.combine(end, time.max))

    def _assert_short_deadlines(self, tournament: Tournament) -> None:
        """Все уже назначенные дедлайны совпадают с концом события."""
        deadlines = set(
            self._matches(tournament)
            .filter(deadline__isnull=False)
            .values_list("deadline", flat=True)
        )
        self.assertEqual(deadlines, {self._expected_short_deadline(tournament)})

    def _complete(self, match: Match) -> None:
        """Сыграть матч с победой первой стороны и штатным продвижением."""
        match.refresh_from_db()
        match.winner = match.player1
        match.winner_team = match.team1
        match.player1_set1 = 6
        match.player2_set1 = 0
        match.status = Match.MatchStatus.COMPLETED
        match.save()

    def test_duration_deadlines_for_every_format_and_variant(self) -> None:
        """Короткие события не получают недельные дедлайны; многодневные сохраняют шаг."""
        for tournament_format, duration, variant in product(
            TournamentFormat.values, TournamentDuration.values, TournamentVariant.values
        ):
            with self.subTest(
                format=tournament_format, duration=duration, variant=variant
            ):
                tournament = self._tournament(tournament_format, duration, variant)
                ok, message = GENERATORS[tournament_format](tournament)
                self.assertTrue(ok, message)
                self.assertFalse(
                    self._matches(tournament).filter(deadline__isnull=True).exists()
                )
                if duration in SHORT_DURATIONS:
                    self._assert_short_deadlines(tournament)
                else:
                    start = timezone.make_aware(datetime.combine(self.start, time.min))
                    for match in self._matches(tournament):
                        self.assertEqual(
                            match.deadline,
                            start + timedelta(days=7 * match.round_index),
                        )

    def test_duration_deadlines_for_fan_and_olympic_consolation(self) -> None:
        """Подвал и олимпийские матчи за места не выходят за короткое событие."""
        for tournament_format, duration, variant in product(
            (TournamentFormat.SINGLE_ELIMINATION, TournamentFormat.OLYMPIC_CONSOLATION),
            SHORT_DURATIONS,
            TournamentVariant.values,
        ):
            with self.subTest(
                format=tournament_format, duration=duration, variant=variant
            ):
                tournament = self._tournament(tournament_format, duration, variant)
                self.assertTrue(GENERATORS[tournament_format](tournament)[0])
                for match in self._matches(tournament).filter(
                    round_index=1, is_consolation=False
                ):
                    self._complete(match)
                self.assertTrue(
                    self._matches(tournament).filter(is_consolation=True).exists()
                )
                self._assert_short_deadlines(tournament)

    def test_duration_deadlines_for_all_group_playoff_shapes(self) -> None:
        """Плей-офф для 2–6 групп и его утешительные матчи укладываются в событие."""
        for count, duration, variant in product(
            (6, 9, 12, 15, 18), SHORT_DURATIONS, TournamentVariant.values
        ):
            with self.subTest(count=count, duration=duration, variant=variant):
                tournament = self._tournament(
                    TournamentFormat.WEEKEND_DAY, duration, variant, count=count
                )
                self.assertTrue(generate_groups(tournament)[0])
                for match in self._matches(tournament).filter(tvd_stage="group"):
                    self._complete(match)
                ok, message = generate_playoffs(tournament)
                self.assertTrue(ok, message)
                if count >= 12:
                    self.assertTrue(
                        self._matches(tournament).filter(is_consolation=True).exists()
                    )
                self._assert_short_deadlines(tournament)

    def test_duration_deadlines_for_separate_circular_group_brackets(self) -> None:
        """Отдельные круговые основная и утешительная сетки используют общий календарь."""
        for duration, variant in product(SHORT_DURATIONS, TournamentVariant.values):
            with self.subTest(duration=duration, variant=variant):
                tournament = self._tournament(
                    TournamentFormat.WEEKEND_DAY, duration, variant, count=12
                )
                self.assertTrue(generate_groups(tournament)[0])
                for match in self._matches(tournament).filter(tvd_stage="group"):
                    self._complete(match)
                self.assertTrue(generate_main_bracket(tournament, "circular")[0])
                self.assertTrue(generate_consolation_bracket(tournament, "circular")[0])
                self._assert_short_deadlines(tournament)

    def test_duration_deadlines_for_manual_rounds(self) -> None:
        """Ручные пары и автоматические следующие круги не растягивают короткий турнир."""
        for tournament_format, duration, variant in product(
            (TournamentFormat.SINGLE_ELIMINATION, TournamentFormat.ROUND_ROBIN),
            SHORT_DURATIONS,
            TournamentVariant.values,
        ):
            with self.subTest(
                format=tournament_format, duration=duration, variant=variant
            ):
                tournament = self._tournament(tournament_format, duration, variant)
                self.assertTrue(start_manual_tournament(tournament)[0])
                entities = load_entities(tournament)
                ok, message = save_manual_round(
                    tournament,
                    pairs=[
                        (entities[i], entities[i + 1])
                        for i in range(0, len(entities), 2)
                    ],
                    leftover=None,
                    auto_next=True,
                )
                self.assertTrue(ok, message)
                for match in self._matches(tournament).filter(round_index=1):
                    self._complete(match)
                self.assertTrue(
                    self._matches(tournament).filter(round_index=2).exists()
                )
                self._assert_short_deadlines(tournament)

    def test_duration_deadlines_for_manual_qualifiers_and_advancement(self) -> None:
        """Отборы и готовые финалы ручных этапов не продлевают событие на неделю."""
        for duration, variant in product(SHORT_DURATIONS, TournamentVariant.values):
            with self.subTest(duration=duration, variant=variant):
                tournament = self._tournament(
                    TournamentFormat.SINGLE_ELIMINATION, duration, variant
                )
                self.assertTrue(start_manual_tournament(tournament)[0])
                tokens = [entity.token for entity in load_entities(tournament)]
                create_qualifiers(tournament, {"A": tokens[:4], "B": tokens[4:]})
                self._assert_short_deadlines(tournament)
                for match in self._matches(tournament).filter(
                    manual_stage__kind="qualifier"
                ):
                    self._complete(match)
                create_two_knockouts(tournament, tokens[:4], tokens[4:])
                for match in (
                    self._matches(tournament)
                    .exclude(manual_stage__kind="qualifier")
                    .filter(round_index=1)
                ):
                    self._complete(match)
                self.assertFalse(
                    self._matches(tournament)
                    .exclude(manual_stage__kind="qualifier")
                    .filter(round_index=2, deadline__isnull=True)
                    .exists()
                )
                self._assert_short_deadlines(tournament)

    def test_duration_deadline_recalculation_respects_event_dates(self) -> None:
        """Пересчёт не переносит короткое событие в будущее при позднем запуске."""
        for duration in SHORT_DURATIONS:
            with (
                self.subTest(duration=duration),
                timezone.override("Asia/Yekaterinburg"),
            ):
                self.start = timezone.localdate() - timedelta(days=3)
                tournament = self._tournament(TournamentFormat.ROUND_ROBIN, duration)
                self.assertTrue(generate_round_robin(tournament)[0])
                updated = recalculate_tournament_match_deadlines(tournament)
                self.assertEqual(updated, self._matches(tournament).count())
                self._assert_short_deadlines(tournament)

    def test_duration_weekend_uses_explicit_earlier_end_date(self) -> None:
        """Выходной турнир с явно заданным одним днём завершается в этот день."""
        tournament = self._tournament(
            TournamentFormat.SINGLE_ELIMINATION,
            TournamentDuration.WEEKEND,
            end_date=self.start,
        )
        self.assertTrue(generate_fan(tournament)[0])
        self._assert_short_deadlines(tournament)

    def test_duration_fill_start_dates_for_every_format(self) -> None:
        """Общий запуск после набора фиксирует календарь всех коротких форматов."""
        for tournament_format, duration in product(
            TournamentFormat.values, SHORT_DURATIONS
        ):
            with self.subTest(format=tournament_format, duration=duration):
                tournament = self._tournament(tournament_format, duration)
                Tournament.objects.filter(pk=tournament.pk).update(
                    start_after_fill=True, start_date=None, end_date=None
                )
                tournament.refresh_from_db()
                ok, message, _ = _tournament_manual_generate(tournament)
                self.assertTrue(ok, message)
                tournament.refresh_from_db()
                self.assertEqual(tournament.start_date, timezone.localdate())
                self.assertEqual(
                    tournament.end_date,
                    timezone.localdate()
                    + timedelta(days=int(duration == TournamentDuration.WEEKEND)),
                )
                self._assert_short_deadlines(tournament)

    def test_duration_failed_fill_start_rolls_back_dates_and_partial_matches(
        self,
    ) -> None:
        """Ошибка генератора не оставляет дату старта и незавершённую структуру."""
        for duration in TournamentDuration.values:
            with self.subTest(duration=duration):
                tournament = self._tournament(
                    TournamentFormat.SINGLE_ELIMINATION, duration
                )
                Tournament.objects.filter(pk=tournament.pk).update(
                    start_after_fill=True, start_date=None, end_date=None
                )
                tournament.refresh_from_db()
                before = Tournament.objects.filter(pk=tournament.pk).values().get()

                def failed_generator(locked: Tournament) -> tuple[bool, str]:
                    """Воспроизвести отказ после частичного создания матча."""
                    Match.objects.create(
                        tournament=locked,
                        player1=self.players[0],
                        player2=self.players[1],
                        round_index=1,
                    )
                    return False, "Ошибка формирования сетки"

                ok, _ = generate_tournament_with_start_date(
                    tournament, failed_generator
                )
                self.assertFalse(ok)
                self.assertFalse(self._matches(tournament).exists())
                self.assertEqual(
                    Tournament.objects.filter(pk=tournament.pk).values().get(), before
                )
