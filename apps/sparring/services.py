"""
Сервисы для работы со спаррингами.
"""

import logging
from datetime import timedelta

from django.utils import timezone

from apps.tournaments.models import Match

logger = logging.getLogger(__name__)


def create_match_from_response(sparring_response) -> Match:
    """
    Создает матч из отклика на спарринг.

    Args:
        sparring_response: SparringResponse объект

    Returns:
        Созданный Match объект

    Raises:
        ValueError: Если данные некорректны
    """
    request = sparring_response.sparring_request
    author = request.player
    respondent = sparring_response.respondent

    if author.id == respondent.id:
        raise ValueError("Нельзя создать матч с самим собой")

    # Дружеский матч не влияет на рейтинг и силу
    rating_status = (
        Match.RatingCalcStatus.NOT_APPLICABLE
        if request.is_friendly
        else Match.RatingCalcStatus.PENDING
    )

    match = Match.objects.create(
        tournament=None,
        match_type=Match.MatchType.SPARRING,
        sparring_response=sparring_response,
        player1=author,
        player2=respondent,
        status=Match.MatchStatus.SCHEDULED,
        deadline=timezone.now() + timedelta(days=7),
        rating_status=rating_status,
    )

    logger.info(
        "Created sparring match %s from response %s (request %s)",
        match.pk,
        sparring_response.pk,
        request.pk,
    )

    return match  # type: ignore[no-any-return]
