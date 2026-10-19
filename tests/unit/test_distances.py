"""Расстояния для лент событий без зависимости от приложений."""

import pytest

from apps.core.distances import haversine_km


@pytest.mark.parametrize(
    ("points", "expected"),
    [
        ((0.0, 0.0, 0.0, 0.0), 0.0),
        ((0.0, 0.0, 0.0, 1.0), 111.1949266),
        ((0.0, 0.0, 0.0, 180.0), 20015.086796),
    ],
)
def test_distance_when_valid_points_given_should_return_great_circle_kilometers(
    points: tuple[float, float, float, float],
    expected: float,
) -> None:
    """Проверяет нулевое расстояние, один градус и противоположные точки.

    Args:
        points: Координаты двух точек.
        expected: Ожидаемое расстояние.
    """
    assert haversine_km(*points) == pytest.approx(expected, rel=1e-6)
