"""
Отмена турнира: статус «Отменён», возврат лимитов регистраций участникам.
"""

import logging

from apps.subscriptions.fancoin import TOURNAMENT_REGISTRATION_COST
from apps.subscriptions.models import FancoinTransaction
from apps.users.models import Notification

from .models import Tournament, TournamentStatus

logger = logging.getLogger(__name__)


def _decrement_subscription_for_user(user, tournament: Tournament) -> None:
    """Вернуть FT пользователю при отмене турнира."""
    try:
        sub = getattr(user, "subscription", None)
        if sub:
            sub.refund_fancoin(
                TOURNAMENT_REGISTRATION_COST,
                reason=FancoinTransaction.Reason.TOURNAMENT_CANCEL,
                tournament=tournament,
            )
    except Exception as e:
        logger.warning(
            "Could not decrement subscription for user %s: %s",
            getattr(user, "pk", None),
            e,
        )


def cancel_tournament(
    tournament: Tournament,
    *,
    notify_message: str | None = None,
) -> bool:
    """
    Отменить турнир: установить статус «Отменён», вернуть лимиты регистраций
    всем зарегистрированным (участникам или членам команд) и отправить уведомления.
    Возвращает True при успехе.
    """
    if tournament.status == TournamentStatus.CANCELLED:
        logger.info("Tournament %s already cancelled", tournament.slug)
        return True

    tournament.status = TournamentStatus.CANCELLED
    tournament.save(update_fields=["status", "updated_at"])

    url = None
    try:
        from django.urls import reverse

        url = reverse("tournament_detail", args=[tournament.slug])
    except Exception:
        pass

    message = notify_message or (
        f"Турнир «{tournament.name}» отменён из-за недостаточного количества участников. "
        f"Баланс FT восстановлен (+{TOURNAMENT_REGISTRATION_COST})."
    )

    if tournament.is_doubles():
        # По одной «регистрации» на каждого игрока в команде (solo или полная пара)
        users_done: set[int] = set()
        for team in tournament.teams.select_related("player1__user", "player2__user"):
            for player in (team.player1, team.player2):
                if player is None:
                    continue
                u = getattr(player, "user", None)
                if u and u.pk not in users_done:
                    users_done.add(u.pk)
                    _decrement_subscription_for_user(u, tournament)
                    Notification.objects.create(
                        user=u,
                        message=message,
                        url=url or "",
                    )
    else:
        for player in tournament.participants.select_related("user").only("user_id"):
            user = getattr(player, "user", None)
            if user:
                _decrement_subscription_for_user(user, tournament)
                Notification.objects.create(
                    user=user,
                    message=message,
                    url=url or "",
                )

    logger.info(
        "Cancelled tournament %s (slug=%s), returned registration limits",
        tournament.name,
        tournament.slug,
    )
    return True
