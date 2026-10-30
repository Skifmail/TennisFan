"""Независимые от аккаунтов спортивные правила детской лиги."""

from datetime import date

from django.core.exceptions import ValidationError
from django.db import models


class Ball(models.TextChoices):
    RED = "red", "Красный мяч"
    ORANGE = "orange", "Оранжевый мяч"
    GREEN = "green", "Зелёный мяч"
    YELLOW = "yellow", "Жёлтый мяч"


class AgeRule(models.TextChoices):
    START_DATE = "start_date", "Возраст на дату начала"
    YEAR_END = "year_end", "Возраст на 31 декабря года турнира"


class ScoreFormat(models.TextChoices):
    TIEBREAK_7 = "tb7", "Тай-брейк до 7"
    TIEBREAK_10 = "tb10", "Матч-тай-брейк до 10"
    THREE_TIEBREAKS = "three_tb", "До двух выигранных тай-брейков до 7"
    SHORT_SET = "short_set", "Сет до 4, тай-брейк при 4:4"
    REGULAR_SET = "regular_set", "Сет до 6, тай-брейк при 6:6"


BALL_GUIDE: tuple[dict[str, str], ...] = (
    {
        "code": str(Ball.RED),
        "name": "Красный",
        "age": "До 8 лет",
        "court": "Корт 11–13 м",
        "description": "Первый соревновательный опыт. Мяч примерно на 75% медленнее стандартного.",
    },
    {
        "code": str(Ball.ORANGE),
        "name": "Оранжевый",
        "age": "Ориентир: 8–10 лет",
        "court": "Корт около 18 м",
        "description": "Больше пространства для игры. Мяч примерно на 50% медленнее стандартного.",
    },
    {
        "code": str(Ball.GREEN),
        "name": "Зелёный",
        "age": "Ориентир: 9–10 лет",
        "court": "Полноразмерный корт",
        "description": "Подготовка к стандартному теннису. Мяч примерно на 25% медленнее стандартного.",
    },
    {
        "code": str(Ball.YELLOW),
        "name": "Жёлтый",
        "age": "После ступеней Tennis10s",
        "court": "Полноразмерный корт",
        "description": "Стандартный мяч. Возрастные условия указаны в регламенте турнира.",
    },
)


def age_on(birth_date: date, reference: date) -> int:
    """Вычисляет полный возраст на заданную дату, включая високосные годы."""
    return (
        reference.year
        - birth_date.year
        - ((reference.month, reference.day) < (birth_date.month, birth_date.day))
    )


def validate_score(score_format: str, scores: list[list[int]]) -> int:
    """Проверяет завершённый счёт и возвращает сторону победителя (1 или 2)."""
    if score_format not in ScoreFormat.values:
        raise ValidationError("Неизвестный формат счёта.")
    expected_max = 3 if score_format == ScoreFormat.THREE_TIEBREAKS else 1
    if not isinstance(scores, list) or not 1 <= len(scores) <= expected_max:
        raise ValidationError("Неверное количество сетов или тай-брейков.")
    wins = [0, 0]
    for pair in scores:
        if (
            not isinstance(pair, list)
            or len(pair) != 2
            or any(
                not isinstance(v, int) or isinstance(v, bool) or v < 0 or v > 999
                for v in pair
            )
        ):
            raise ValidationError(
                "Счёт должен содержать пары неотрицательных целых чисел."
            )
        if max(wins) == 2:
            raise ValidationError(
                "После определения победителя лишние партии недопустимы."
            )
        high, low = max(pair), min(pair)
        if score_format in (ScoreFormat.SHORT_SET, ScoreFormat.REGULAR_SET):
            target = 4 if score_format == ScoreFormat.SHORT_SET else 6
            valid = (high == target and low <= target - 2) or (
                high == target + 1 and low in (target - 1, target)
            )
        else:
            target = 10 if score_format == ScoreFormat.TIEBREAK_10 else 7
            valid = (high == target and low <= target - 2) or (
                high > target and high - low == 2
            )
        if not valid:
            raise ValidationError(
                "Счёт не соответствует выбранному формату завершённого матча."
            )
        wins[0 if pair[0] > pair[1] else 1] += 1
    needed = 2 if score_format == ScoreFormat.THREE_TIEBREAKS else 1
    if max(wins) != needed:
        raise ValidationError("Матч ещё не завершён.")
    return 1 if wins[0] > wins[1] else 2
