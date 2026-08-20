"""Сервисы загрузки фото участниками турнира."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import Q

from .withdraw import is_player_withdrawn

if TYPE_CHECKING:
    from apps.users.models import Player

    from .models import Tournament, TournamentPhoto


def is_player_registered_in_tournament(tournament: Tournament, player: Player) -> bool:
    """Проверить, зарегистрирован ли игрок в турнире (одиночный или парный)."""
    if tournament.is_doubles():
        return bool(
            tournament.teams.filter(Q(player1=player) | Q(player2=player)).exists()
        )
    return bool(tournament.participants.filter(pk=player.pk).exists())


def is_active_tournament_participant(tournament: Tournament, player: Player) -> bool:
    """Проверить, является ли игрок активным участником (не снявшимся)."""
    if not is_player_registered_in_tournament(tournament, player):
        return False
    return not is_player_withdrawn(tournament, player)


def get_participant_photo_count(tournament: Tournament, player: Player) -> int:
    """Количество фото, загруженных участником в данный турнир."""
    return int(tournament.photos.filter(uploaded_by=player).count())


def can_participant_upload_photo(tournament: Tournament, player: Player) -> bool:
    """Может ли участник загрузить ещё одно фото."""
    from .models import TournamentPhoto

    if not is_active_tournament_participant(tournament, player):
        return False
    return (
        get_participant_photo_count(tournament, player)
        < TournamentPhoto.PARTICIPANT_PHOTO_LIMIT
    )


def can_participant_delete_photo(photo: TournamentPhoto, player: Player) -> bool:
    """Может ли участник удалить фото (только свои)."""
    return bool(photo.uploaded_by_id == player.pk)


def should_show_photo_upload_prompt(
    tournament: Tournament, player: Player | None
) -> bool:
    """Нужно ли показать участнику напоминание загрузить фото.

    Args:
        tournament: Турнир, страницу которого открыл игрок.
        player: Профиль текущего пользователя или ``None``.

    Returns:
        True, если турнир идёт, игрок в нём играет, своих фото ещё нет
        и он не отключал напоминание.
    """
    from .models import TournamentPhotoPromptDismissal, TournamentStatus

    if player is None:
        return False
    if tournament.status not in (
        TournamentStatus.ACTIVE,
        TournamentStatus.GROUP_STAGE,
        TournamentStatus.PLAYOFFS,
    ):
        return False
    if not is_active_tournament_participant(tournament, player):
        return False
    if get_participant_photo_count(tournament, player) > 0:
        return False
    return not TournamentPhotoPromptDismissal.objects.filter(
        tournament=tournament,
        player=player,
    ).exists()


def dismiss_photo_upload_prompt(tournament: Tournament, player: Player) -> None:
    """Сохранить отказ от напоминания загрузить фото в этот турнир.

    Args:
        tournament: Турнир, для которого отключается напоминание.
        player: Участник, который больше не хочет видеть окно.

    Returns:
        None
    """
    from .models import TournamentPhotoPromptDismissal

    TournamentPhotoPromptDismissal.objects.get_or_create(
        tournament=tournament,
        player=player,
    )


def get_next_photo_order(tournament: Tournament) -> int:
    """Следующий порядковый номер для фото в галерее турнира."""
    last = tournament.photos.order_by("-order").values_list("order", flat=True).first()
    if last is None:
        return 0
    return int(last) + 1
