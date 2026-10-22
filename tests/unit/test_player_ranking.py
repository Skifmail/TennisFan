"""Юнит-тесты рейтингового балла игроков."""

from __future__ import annotations

from django.test import SimpleTestCase

from apps.users.ranking import ranking_score


class RankingScoreTests(SimpleTestCase):
    """Формула балла: победы важнее матчей, качество даёт бонус."""

    def test_new_player_without_matches_has_zero(self) -> None:
        """Игрок без матчей получает 0 и не может обогнать играющих."""
        self.assertEqual(ranking_score(0, 0), 0.0)
        self.assertEqual(ranking_score(None, None), 0.0)

    def test_one_win_worth_more_than_many_losses(self) -> None:
        """Одна победа выше 100 матчей без побед."""
        self.assertGreater(ranking_score(1, 1), ranking_score(0, 100))

    def test_matches_add_experience_points_without_wins(self) -> None:
        """Даже без побед сыгранные матчи дают баллы за активность."""
        self.assertGreater(ranking_score(0, 20), ranking_score(0, 10))

    def test_win_rate_bonus_rewards_quality(self) -> None:
        """5 побед из 5 ценятся выше 5 побед из 50."""
        self.assertGreater(ranking_score(5, 5), ranking_score(5, 50))

    def test_losses_can_reduce_score_at_equal_wins(self) -> None:
        """Серия поражений снижает процент побед и может опустить в рейтинге."""
        self.assertGreater(ranking_score(5, 10), ranking_score(5, 20))

    def test_extra_win_beats_extra_matches(self) -> None:
        """Шестая победа перекрывает преимущество в количестве матчей."""
        self.assertGreater(ranking_score(6, 6), ranking_score(5, 50))

    def test_more_wins_with_same_activity_are_higher(self) -> None:
        """При одинаковом числе матчей больше побед — выше позиция."""
        self.assertGreater(ranking_score(10, 20), ranking_score(9, 20))
