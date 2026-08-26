"""Очередь целей Яндекс.Метрики через сессию.

Формы работают по схеме POST → redirect → messages: цель нельзя отправить
в том же ответе, что и редирект. Вьюха кладёт имя цели в сессию, context
processor забирает и очищает её на следующей странице, ``base.html`` вызывает
``ym(..., 'reachGoal', ...)``.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest

SESSION_KEY = "metrika_goals"

#: Имена целей, согласованные с рекламой.
TOURNAMENT_REGISTRATION_SUCCESS = "tournament_registration_success"
TRAINING_ENROLL_SUCCESS = "training_enroll_success"
COACH_APPLICATION_SUCCESS = "coach_application_success"
TOURNAMENT_CTA_CLICK = "tournament_cta_click"
TOURNAMENT_PAYMENT_STARTED = "tournament_payment_started"


def queue_metrika_goal(
    request: HttpRequest,
    goal: str,
    params: dict[str, Any] | None = None,
) -> None:
    """Поставить цель в очередь на отправку после редиректа.

    Args:
        request: Текущий HTTP-запрос с сессией.
        goal: Имя цели в счётчике Метрики.
        params: Доп. параметры (зона, формат, id турнира) для разреза кампаний.
    """
    if not goal:
        return
    goals = list(request.session.get(SESSION_KEY) or [])
    entry: dict[str, Any] = {"goal": goal}
    if params:
        # Метрика принимает только простые типы; отбрасываем пустые значения.
        cleaned = {
            key: value
            for key, value in params.items()
            if value is not None and value != ""
        }
        if cleaned:
            entry["params"] = cleaned
    goals.append(entry)
    request.session[SESSION_KEY] = goals
    request.session.modified = True


def pop_metrika_goals(request: HttpRequest) -> list[dict[str, Any]]:
    """Забрать и очистить очередь целей для текущей страницы.

    Args:
        request: Текущий HTTP-запрос с сессией.

    Returns:
        list[dict[str, Any]]: Список целей; при повторной загрузке страницы
        очередь уже пуста, дублей не будет.
    """
    goals = list(request.session.pop(SESSION_KEY, []) or [])
    if goals:
        request.session.modified = True
    return goals


def tournament_goal_params(tournament) -> dict[str, Any]:
    """Собрать параметры цели по турниру для разреза кампаний.

    Args:
        tournament: Экземпляр ``Tournament``.

    Returns:
        dict[str, Any]: id, слаг, зона, регион, формат и уровень.
    """
    categories: list[str] = []
    try:
        categories = list(
            tournament.allowed_categories.values_list("category", flat=True)
        )
    except Exception:  # noqa: BLE001 — у исторических объектов связи может не быть
        categories = []
    return {
        "tournament_id": getattr(tournament, "pk", None),
        "tournament_slug": getattr(tournament, "slug", ""),
        "region": getattr(tournament, "region", "") or "",
        "geo_area": getattr(getattr(tournament, "geo_area", None), "slug", "") or "",
        "variant": getattr(tournament, "variant", "") or "",
        "category": ",".join(categories),
    }
