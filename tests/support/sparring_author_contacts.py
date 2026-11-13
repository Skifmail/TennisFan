"""Общие данные для проверки раскрытия контактов автора после отклика."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from apps.sparring.models import SparringRequest
from apps.users.models import Player
from tests.support.factories import make_player, make_subscription


@dataclass(frozen=True)
class SparringAuthorContactsScenario:
    """Автор с контактами, активированный зритель и открытая одиночная заявка."""

    author: Player
    respondent: Player
    sparring: SparringRequest


def make_author_contacts_scenario(
    *, suffix: str = "author-contacts", worst_case: bool = False
) -> SparringAuthorContactsScenario:
    """Создать готовые профили и подписку для настоящего HTTP-отклика.

    Args:
        suffix: Уникальная часть адресов тестовых пользователей.
        worst_case: Использовать длинные имена и неразрывный текст заявки.

    Returns:
        Участники и заявка со всеми четырьмя внешними способами связи.
    """
    author = make_player(
        email_suffix=f"{suffix}-author",
        first_name="Александр" * 8 if worst_case else "Иван",
        last_name="ОченьДлиннаяФамилия" * 6 if worst_case else "Автор",
        phone="8 (999) 123-45-67",
        email_verified=True,
    )
    Player.objects.filter(pk=author.pk).update(
        city="Москва",
        birth_date=date(1990, 1, 1),
        is_verified=True,
        telegram="author_player",
        whatsapp="8 (999) 987-65-43",
        max_contact="https://max.ru/u/author_player",
    )
    author.refresh_from_db()
    respondent = make_player(
        email_suffix=f"{suffix}-viewer",
        first_name="Леонид",
        last_name="Игрок",
        phone="+79990000002",
        email_verified=True,
    )
    Player.objects.filter(pk=respondent.pk).update(
        city="Москва", birth_date=date(1994, 1, 1), is_verified=True
    )
    respondent.refresh_from_db()
    make_subscription(
        respondent.user,
        tier_name=f"{suffix}-silver",
        tier_kwargs={"has_sparring": True},
    )
    sparring = SparringRequest.objects.create(
        player=author,
        city="Москва",
        description="Спарринг" * 40 if worst_case else "Ищу партнёра на вечернюю игру.",
        preferred_days="Понедельник и четверг",
        preferred_time="19:00–21:00",
        preferred_location=(
            "НазваниеКортонаБезПробелов" * 6 if worst_case else "Корт Орион"
        ),
    )
    return SparringAuthorContactsScenario(author, respondent, sparring)
