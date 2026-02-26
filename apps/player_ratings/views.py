"""
API и страница оценки соперников: POST оценка матча, GET навыки игрока, форма опроса.
"""

import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods

from apps.tournaments.models import Match
from apps.users.models import Player

from .enums import SkillMetric
from .services import (
    get_match_rating_status,
    get_player_skills,
    submit_rating,
)

logger = logging.getLogger(__name__)


def _get_current_player(request):
    if not request.user.is_authenticated:
        return None
    return getattr(request.user, "player", None)


@require_http_methods(["GET", "POST"])
@login_required
@ensure_csrf_cookie
def rate_match(request, match_id: int):
    """
    GET: HTML — страница формы опроса; JSON — статус возможности оценки.
    POST: принять оценки (JSON), сохранить, пересчитать.
    """
    player = _get_current_player(request)
    if not player:
        if request.headers.get("Accept", "").find("application/json") >= 0:
            return JsonResponse(
                {"success": False, "error": "Нет профиля игрока"},
                status=403,
            )
        from django.shortcuts import redirect

        return redirect("auth")

    if request.method == "GET":
        match = get_object_or_404(Match, pk=match_id)
        status = get_match_rating_status(match, player)
        if request.headers.get("Accept", "").find("application/json") >= 0:
            return JsonResponse({"success": True, "rating_status": status})
        metrics = [
            {"name": name, "label": label} for name, label in SkillMetric.choices
        ]
        return render(
            request,
            "player_ratings/rate_match_form.html",
            {
                "match": match,
                "rating_status": status,
                "metrics": metrics,
                "rate_api_url": request.build_absolute_uri(request.path),
                "my_matches_url": reverse("my_matches"),
            },
        )

    # POST
    try:
        payload = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse(
            {"success": False, "error": "Неверный JSON"},
            status=400,
        )
    success, message, rating = submit_rating(match_id, player, payload)
    if not success:
        return JsonResponse(
            {"success": False, "error": message},
            status=400,
        )
    return JsonResponse(
        {
            "success": True,
            "message": message,
            "rating_id": rating.pk if rating else None,
            "redirect_url": reverse("my_matches"),
        }
    )


@require_GET
def player_skills(request, player_id: int):
    """
    GET /ratings/players/{id}/skills/ — агрегированные навыки.
    Для чужого профиля — только публичные; для своего — + recommend_to_improve.

    Пример ответа:
    {
      "success": true,
      "skills": {
        "metrics": [
          {
            "name": "serve",
            "label": "Подача",
            "average_raw": 7.5,
            "average_weighted": 7.2,
            "votes_count": 12,
            "display_value": 7.2,
            "insufficient_data": false,
            "stars_filled": 4
          },
          ...
        ],
        "recommend_to_improve": [
          {
            "name": "net_play",
            "label": "Игра у сетки",
            "average_weighted": 5.8,
            "votes_count": 10
          }
        ]
      }
    }
    """
    try:
        player = Player.objects.get(pk=player_id)
    except Player.DoesNotExist:
        return JsonResponse(
            {"success": False, "error": "Игрок не найден"},
            status=404,
        )
    data = get_player_skills(
        player,
        request.user,
        include_lowest_three=True,
    )
    return JsonResponse({"success": True, "skills": data})
