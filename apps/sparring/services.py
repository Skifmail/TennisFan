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

    # Создаем матч
    match = Match.objects.create(
        tournament=None,  # Спарринговые матчи не связаны с турниром
        match_type=Match.MatchType.SPARRING,
        sparring_response=sparring_response,
        player1=author,
        player2=respondent,
        status=Match.MatchStatus.SCHEDULED,
        deadline=timezone.now() + timedelta(days=7),  # Дедлайн +7 дней
        rating_status=Match.RatingCalcStatus.PENDING,  # Рейтинг будет рассчитан
    )

    logger.info(
        "Created sparring match %s from response %s (request %s)",
        match.pk,
        sparring_response.pk,
        request.pk,
    )

    return match
