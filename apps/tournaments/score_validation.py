"""Правила завершённого счёта для тенниса и падела, общие для сайта и бота."""

from collections.abc import Sequence

from .models import Match, MatchFormat


class ProposalValidationError(ValueError):
    """Несогласованный, незавершённый или недопустимый счёт матча."""


def _standard_set_finished(winner: int, loser: int) -> bool:
    """Завершённый сет до шести геймов с тай-брейком при 6:6."""
    return (winner == 6 and loser <= 4) or (winner == 7 and loser in (5, 6))


def _deciding_tiebreak_finished(winner: int, loser: int) -> bool:
    """Завершённый супертай-брейк до десяти с преимуществом в два очка."""
    return (winner == 10 and loser <= 8) or (
        winner > 10 and loser >= 9 and winner - loser == 2
    )


def validate_match_score(
    match: Match, scores: Sequence[tuple[int | None, int | None]]
) -> None:
    """Проверить сеты, их порядок и число побед по настроенному формату.

    При отсутствии формата сохраняется совместимость с матчами из одного сета
    или до двух выигранных сетов. Третий сет может быть супертай-брейком.
    """
    filled: list[tuple[int, int]] = []
    gap = False
    for a, b in scores:
        if a is None and b is None:
            gap = True
            continue
        if gap:
            raise ProposalValidationError("Сеты заполняются по порядку, без пропусков.")
        if a is None or b is None:
            raise ProposalValidationError("В каждом сете укажите геймы обеих сторон.")
        if any(
            not isinstance(value, int)
            or isinstance(value, bool)
            or not 0 <= value <= 32767
            for value in (a, b)
        ):
            raise ProposalValidationError("Геймы должны быть неотрицательными числами.")
        filled.append((a, b))
    if not filled or len(filled) > 3:
        raise ProposalValidationError("Укажите от одного до трёх завершённых сетов.")

    match_format = match.tournament.match_format if match.tournament else ""
    single_set = match_format in (MatchFormat.SET_6, MatchFormat.SET_TIEBREAK)
    fast4 = match_format == MatchFormat.FAST4
    required_wins = 1 if single_set or (not match_format and len(filled) == 1) else 2
    wins = [0, 0]
    for index, (a, b) in enumerate(filled):
        if max(wins) >= required_wins:
            raise ProposalValidationError("После победы в матче лишние сеты не нужны.")
        winner, loser = max(a, b), min(a, b)
        if index == 2:
            valid = _deciding_tiebreak_finished(winner, loser) or (
                not fast4 and _standard_set_finished(winner, loser)
            )
        elif fast4:
            valid = winner == 4 and loser <= 3
        else:
            valid = _standard_set_finished(winner, loser)
        if not valid:
            raise ProposalValidationError(
                f"Недопустимый или незавершённый счёт {index + 1}-го сета: {a}:{b}."
            )
        wins[0 if a > b else 1] += 1
    if max(wins) != required_wins:
        raise ProposalValidationError("Матч не завершён: не хватает выигранного сета.")
