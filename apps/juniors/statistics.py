"""Закрытая спортивная история ребёнка без смешивания разных мячей."""

from typing import Any

from django.db.models import Q

from .models import ChildProfile, JuniorMatch
from .ranking import category_label
from .rules import Ball
from .services import public_child_label


def child_statistics(child: ChildProfile) -> dict[str, Any]:
    """Разделяет сыгранные матчи, технические результаты и свободные круги."""
    matches = list(
        JuniorMatch.objects.filter(
            Q(entry1__child=child) | Q(entry2__child=child)
        ).select_related("tournament", "entry1__child", "entry2__child", "winner")
    )
    rows = []
    by_ball = []
    for ball, label in Ball.choices:
        same_ball = [match for match in matches if match.tournament.ball == ball]
        played = [match for match in same_ball if match.status == "completed"]
        wins = sum(
            1 for match in played if match.winner and match.winner.child_id == child.pk
        )
        by_ball.append(
            {
                "ball": ball,
                "label": label,
                "played": len(played),
                "wins": wins,
                "losses": len(played) - wins,
                "win_rate": round(wins / len(played) * 100, 1) if played else None,
                "technical": sum(match.status == "walkover" for match in same_ball),
                "byes": sum(match.status == "bye" for match in same_ball),
            }
        )
    for match in sorted(
        matches,
        key=lambda item: (item.tournament.starts_at, item.round_number, item.position),
        reverse=True,
    ):
        opponent = (
            match.entry2
            if match.entry1 and match.entry1.child_id == child.pk
            else match.entry1
        )
        rows.append(
            {
                "id": match.pk,
                "tournament": match.tournament,
                "opponent": (
                    public_child_label(opponent.child)
                    if opponent
                    else "Свободный круг / ожидается"
                ),
                "status": match.get_status_display(),
                "scores": match.scores,
            }
        )
    positions = []
    seen = set()
    for snapshot in child.ranking_history.filter(valid=True).select_related("season"):
        key = (snapshot.season_id, snapshot.ball, snapshot.category)
        if key in seen:
            continue
        seen.add(key)
        previous = child.ranking_history.filter(
            valid=True,
            season_id=snapshot.season_id,
            ball=snapshot.ball,
            category=snapshot.category,
            pk__lt=snapshot.pk,
        ).first()
        positions.append(
            {
                "season": snapshot.season,
                "ball": snapshot.get_ball_display(),
                "category": category_label(snapshot.category),
                "position": snapshot.position,
                "change": previous.position - snapshot.position if previous else None,
                "points": snapshot.points,
            }
        )
    return {
        "positions": positions,
        "by_ball": by_ball,
        "history": rows,
        "awards": child.awards.filter(tournament__status="completed")
        .select_related("tournament", "season")
        .order_by("tournament__starts_at"),
    }
