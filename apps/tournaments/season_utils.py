"""
Утилиты для работы с сезонами и сезонными очками.

Сезоны:
- Зимний: Октябрь - Апрель (7 месяцев)
- Летний: Май - Сентябрь (5 месяцев)
"""

from datetime import date
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from django.db.models import QuerySet


class Season(NamedTuple):
    """Информация о сезоне."""

    name: str  # "Зима" или "Лето"
    year: int  # Год начала сезона (для зимы - год начала, для лета - текущий год)
    start_month: int  # Месяц начала сезона (10 для зимы, 5 для лета)
    end_month: int  # Месяц конца сезона (4 для зимы, 9 для лета)


def get_current_season(date_obj: date | None = None) -> Season:
    """Определить текущий сезон на основе даты.

    Args:
        date_obj: Дата для определения сезона. Если None, используется текущая дата.

    Returns:
        Season с информацией о текущем сезоне.
    """
    if date_obj is None:
        date_obj = date.today()

    month = date_obj.month
    year = date_obj.year

    # Зимний сезон: Октябрь (10) - Апрель (4)
    if month >= 10:  # Октябрь, Ноябрь, Декабрь
        return Season(name="Зима", year=year, start_month=10, end_month=4)
    elif month <= 4:  # Январь, Февраль, Март, Апрель
        return Season(name="Зима", year=year - 1, start_month=10, end_month=4)
    else:  # Май - Сентябрь
        return Season(name="Лето", year=year, start_month=5, end_month=9)


def get_season_display(season: Season) -> str:
    """Получить отображаемое название сезона.

    Args:
        season: Объект Season.

    Returns:
        Строка вида "Зима 2024" или "Лето 2025".
    """
    if season.name == "Зима":
        # Для зимы показываем год начала (октябрь какого года)
        return f"{season.name} {season.year}"
    else:
        # Для лета показываем текущий год
        return f"{season.name} {season.year}"


def annotate_season_pts(queryset: "QuerySet") -> "QuerySet":
    """Добавить аннотацию ``season_pts`` из связанных SeasonPoints.

    У модели SeasonPoints связь OneToOne с игроком: в строке хранятся очки
    накопленного сезона. Сброс при смене сезона выполняет команда
    ``reset_season_points``, а не фильтр по ``season_name``/``season_year`` в SQL.

    Args:
        queryset: QuerySet игроков с ``select_related("season_points")``.

    Returns:
        QuerySet с полем ``season_pts`` (int).
    """
    from django.db.models import F, Value
    from django.db.models.functions import Coalesce

    return queryset.annotate(
        season_pts=Coalesce(F("season_points__current_season_points"), Value(0))
    )


def get_season_key(season: Season) -> str:
    """Получить уникальный ключ сезона для использования в БД.

    Args:
        season: Объект Season.

    Returns:
        Строка вида "winter_2024" или "summer_2025".
    """
    season_code = "winter" if season.name == "Зима" else "summer"
    return f"{season_code}_{season.year}"
