"""
Elo-based rating calculation engine for tennis matches.

Algorithm (per plan):
    1. Expected result  E_A = 1 / (1 + 10^((R_B - R_A) / 750))
    2. Actual result     S_A = 0.7 * (games_won_A / total_games) + 0.3 * win_bonus_A
    3. New rating        R_new = R_old + K * (S_A - E_A)

K-factor:
    - 250  if player has played fewer than 10 total matches  (calibration phase)
    -  50  otherwise  (stabilised rating)

The divisor 750 in the logistic curve means that a 750-point gap gives the
stronger player ~90 % expected win probability, which is tuned for amateur
tennis where upsets are more common than in professional play.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ELO_DIVISOR: int = 800  # Rating gap divisor for expected score calculation
K_CALIBRATION: int = 250  # K-factor during first 10 matches
K_STABLE: int = 50  # K-factor after 10+ matches
CALIBRATION_THRESHOLD: int = 10  # Number of matches before K stabilises
GAMES_WEIGHT: float = 0.7  # Weight of game percentage in S
WIN_BONUS_WEIGHT: float = 0.3  # Weight of match-win bonus in S


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class PlayerRatingSnapshot:
    """Immutable snapshot of player state needed for a single calculation."""

    rating: float
    total_matches: int


@dataclass
class MatchScore:
    """Raw set-by-set score for one side of the match."""

    set1: int = 0
    set2: int = 0
    set3: int = 0

    @property
    def total_games(self) -> int:
        return self.set1 + self.set2 + self.set3


@dataclass
class RatingDelta:
    """Result of a single rating calculation for one player pair."""

    new_rating_a: float
    new_rating_b: float
    delta_a: float
    delta_b: float
    expected_a: float
    expected_b: float
    actual_a: float
    actual_b: float


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------
def get_k_factor(total_matches: int) -> int:
    """Return the K-factor for a player based on how many matches they've played.

    Args:
        total_matches: Total number of completed matches across all tournaments.

    Returns:
        K_CALIBRATION (250) if < 10, otherwise K_STABLE (50).
    """
    return K_CALIBRATION if total_matches < CALIBRATION_THRESHOLD else K_STABLE


def expected_score(rating_a: float, rating_b: float) -> float:
    """Calculate the expected result for player A using the logistic curve.

    E_A = 1 / (1 + 10^((R_B - R_A) / 750))

    Args:
        rating_a: Current rating of player A.
        rating_b: Current rating of player B.

    Returns:
        Expected score for player A in range (0, 1).
    """
    exponent = (rating_b - rating_a) / ELO_DIVISOR
    return 1.0 / (1.0 + 10**exponent)


def actual_score(games_won: int, total_games: int, won_match: bool) -> float:
    """Calculate the actual result S for a player.

    S = 0.7 * (games_won / total_games) + 0.3 * win_bonus

    Args:
        games_won: Number of games the player won across all sets.
        total_games: Total games in the match (both players combined).
        won_match: Whether the player won the match overall.

    Returns:
        Actual score in range [0, 1].
    """
    if total_games == 0:
        return 0.0
    game_pct = games_won / total_games
    win_bonus = 1.0 if won_match else 0.0
    return GAMES_WEIGHT * game_pct + WIN_BONUS_WEIGHT * win_bonus


def calculate_new_ratings(
    player_a: PlayerRatingSnapshot,
    player_b: PlayerRatingSnapshot,
    score_a: MatchScore,
    score_b: MatchScore,
    a_won_match: bool,
) -> RatingDelta:
    """Calculate new Elo-based ratings for both players after a match.

    Args:
        player_a: Rating snapshot for player A.
        player_b: Rating snapshot for player B.
        score_a: Games won by player A in each set.
        score_b: Games won by player B in each set.
        a_won_match: True if player A won the overall match.

    Returns:
        RatingDelta with new ratings and intermediate values for auditing.
    """
    k_a = get_k_factor(player_a.total_matches)
    k_b = get_k_factor(player_b.total_matches)

    e_a = expected_score(player_a.rating, player_b.rating)
    e_b = 1.0 - e_a

    games_a = score_a.total_games
    games_b = score_b.total_games
    total_games = games_a + games_b

    s_a = actual_score(games_a, total_games, a_won_match)
    s_b = actual_score(games_b, total_games, not a_won_match)

    delta_a = k_a * (s_a - e_a)
    delta_b = k_b * (s_b - e_b)

    new_a = player_a.rating + delta_a
    new_b = player_b.rating + delta_b

    logger.info(
        "Rating calc: A(%.1f, K=%d) vs B(%.1f, K=%d) | "
        "E(%.3f/%.3f) S(%.3f/%.3f) | Δ(%.1f/%.1f) → (%.1f/%.1f)",
        player_a.rating,
        k_a,
        player_b.rating,
        k_b,
        e_a,
        e_b,
        s_a,
        s_b,
        delta_a,
        delta_b,
        new_a,
        new_b,
    )

    return RatingDelta(
        new_rating_a=round(new_a, 1),
        new_rating_b=round(new_b, 1),
        delta_a=round(delta_a, 1),
        delta_b=round(delta_b, 1),
        expected_a=round(e_a, 4),
        expected_b=round(e_b, 4),
        actual_a=round(s_a, 4),
        actual_b=round(s_b, 4),
    )
