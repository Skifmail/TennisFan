"""
Утилиты турниров (участники матча и т.д.).
Используются в views и в telegram_bot без циклических импортов.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from apps.clubs.models import Club
    from apps.users.models import Player

from django.conf import settings
from django.db.models import (
    Case,
    DateTimeField,
    F,
    IntegerField,
    Q,
    QuerySet,
    When,
)
from django.db.models.expressions import OrderBy
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.text import slugify

from .models import Match, Tournament, TournamentStatus

logger = logging.getLogger(__name__)

SLUG_MAX_LENGTH = 50
SUFFIX_RESERVED = 4


def generate_unique_tournament_slug(
    name: str,
    slug: str | None = None,
    instance: Tournament | None = None,
) -> str:
    """
    Генерирует уникальный slug для турнира.

    Если slug уже занят другим турниром или пустой — создаётся уникальный вариант
    путём добавления суффикса -2, -3 и т.д. Базовый slug обрезается до 46 символов,
    чтобы оставить место для суффикса (лимит SlugField — 50 символов).

    Args:
        name: Название турнира (используется, если slug пустой).
        slug: Текущее значение slug (может быть из prepopulated или ввода пользователя).
        instance: Редактируемый экземпляр турнира (исключается из проверки уникальности).

    Returns:
        Уникальный slug, подходящий под ограничение max_length.
    """
    raw_slug = (slug or "").strip()
    base = raw_slug or cast(str, slugify(name, allow_unicode=True))
    if not base:
        base = "tournament"
    base = base[: SLUG_MAX_LENGTH - SUFFIX_RESERVED].rstrip("-")
    candidate = base
    n = 1
    queryset = Tournament.objects.all()
    if instance and instance.pk:
        queryset = queryset.exclude(pk=instance.pk)
    while queryset.filter(slug=candidate).exists():
        n += 1
        suffix = f"-{n}"
        if len(base) + len(suffix) > SLUG_MAX_LENGTH:
            base = base[: SLUG_MAX_LENGTH - len(suffix)].rstrip("-") or "tournament"
        candidate = base + suffix
    return candidate


def tournament_deadline_schedule_start(tournament: Tournament) -> datetime:
    """Базовая точка для расчёта deadline матчей при формировании сетки.

    Используется ``start_date`` турнира, но не раньше начала текущих суток
    (локальная TZ): если сетка создаётся позже запланированного старта
    (задержка постоплаты, ручной запуск), дедлайны отсчитываются от фактической
    даты генерации.

    Args:
        tournament (Tournament): Турнир.

    Returns:
        datetime: Aware datetime начала отсчёта дедлайнов.
    """
    local_today = timezone.localdate()
    today_start = cast(
        datetime,
        timezone.make_aware(datetime.combine(local_today, datetime.min.time())),
    )
    if not tournament.start_date:
        return today_start

    planned_date = tournament.start_date
    if isinstance(planned_date, str):
        planned_date = datetime.strptime(planned_date, "%Y-%m-%d").date()
    planned_start = cast(
        datetime,
        timezone.make_aware(datetime.combine(planned_date, datetime.min.time())),
    )
    return max(planned_start, today_start)


def recalculate_tournament_match_deadlines(tournament: Tournament) -> int:
    """Пересчитать deadline всех матчей турнира от актуальной базовой даты.

    Используйте, если сетка была сформирована позже ``start_date`` и дедлайны
    оказались в прошлом.

    Args:
        tournament (Tournament): Турнир с уже созданными матчами.

    Returns:
        int: Число обновлённых матчей.
    """
    days = int(getattr(tournament, "match_days_per_round", 7) or 7)
    delta = timedelta(days=days)
    start = tournament_deadline_schedule_start(tournament)
    updated = 0
    for match in tournament.matches.filter(round_index__isnull=False):
        round_index = match.round_index
        if not round_index:
            continue
        match.deadline = start + delta * round_index
        match.save(update_fields=["deadline"])
        updated += 1
    return updated


_UPCOMING_MATCH_STATUSES = (
    Match.MatchStatus.SCHEDULED,
    Match.MatchStatus.IN_PROGRESS,
)


def annotate_match_effective_date(queryset: QuerySet[Match]) -> QuerySet[Match]:
    """Добавить поле ``effective_date`` для сортировки матчей в списках.

    Args:
        queryset (QuerySet[Match]): Исходный queryset матчей.

    Returns:
        QuerySet[Match]: Queryset с аннотацией ``effective_date``.
    """
    if "effective_date" in queryset.query.annotations:
        return queryset
    return queryset.annotate(
        effective_date=Coalesce(
            "scheduled_datetime",
            "deadline",
            "completed_datetime",
        ),
    )


def order_player_matches_for_display(queryset: QuerySet[Match]) -> QuerySet[Match]:
    """Сортировка матчей игрока: ближайшие запланированные первыми.

    Запланированные и идущие — по возрастанию ``effective_date``.
    Завершённые и отменённые — по убыванию (свежие сверху).

    Args:
        queryset (QuerySet[Match]): Queryset матчей (можно без ``effective_date``).

    Returns:
        QuerySet[Match]: Отсортированный queryset.
    """
    qs = annotate_match_effective_date(queryset)
    in_game = _UPCOMING_MATCH_STATUSES
    return qs.annotate(
        _player_match_list_group=Case(
            When(status__in=in_game, then=0),
            default=1,
            output_field=IntegerField(),
        ),
        _player_match_sort_asc=Case(
            When(status__in=in_game, then=F("effective_date")),
            default=None,
            output_field=DateTimeField(),
        ),
        _player_match_sort_desc=Case(
            When(~Q(status__in=in_game), then=F("effective_date")),
            default=None,
            output_field=DateTimeField(),
        ),
    ).order_by(
        "_player_match_list_group",
        "_player_match_sort_asc",
        OrderBy(F("_player_match_sort_desc"), descending=True, nulls_last=True),
        "pk",
    )


_UNFINISHED_MATCH_STATUSES = (
    Match.MatchStatus.SCHEDULED,
    Match.MatchStatus.IN_PROGRESS,
)


def _player_tournament_matches_q(player: "Player") -> Q:
    """Фильтр матчей турнира, в которых участвует игрок."""
    return (
        Q(player1=player)
        | Q(player2=player)
        | Q(team1__player1=player)
        | Q(team1__player2=player)
        | Q(team2__player1=player)
        | Q(team2__player2=player)
    )


def find_blocking_earlier_tournament_match(
    match: Match,
    player: "Player",
) -> Match | None:
    """Найти более ранний незавершённый матч того же турнира.

    Игрок не может внести результат, пока не завершены матчи с более ранним
    дедлайном (или датой) в рамках одного турнира.

    Args:
        match (Match): Матч, для которого проверяется ввод результата.
        player (Player): Игрок, вносящий результат.

    Returns:
        Match | None: Блокирующий матч или ``None``, если порядок соблюдён.
    """
    if not match.tournament_id:
        return None
    if match.match_type != Match.MatchType.TOURNAMENT:
        return None
    if match.status not in _UNFINISHED_MATCH_STATUSES:
        return None

    current_row = (
        annotate_match_effective_date(Match.objects.filter(pk=match.pk))
        .values_list("effective_date", "pk")
        .first()
    )
    if not current_row or current_row[0] is None:
        return None

    current_date, current_pk = current_row
    blocking = (
        annotate_match_effective_date(
            Match.objects.filter(
                tournament_id=match.tournament_id,
                match_type=Match.MatchType.TOURNAMENT,
                status__in=_UNFINISHED_MATCH_STATUSES,
            ).filter(_player_tournament_matches_q(player))
        )
        .filter(
            Q(effective_date__lt=current_date)
            | Q(effective_date=current_date, pk__lt=current_pk)
        )
        .select_related(
            "player1__user",
            "player2__user",
            "team1__player1__user",
            "team1__player2__user",
            "team2__player1__user",
            "team2__player2__user",
        )
        .order_by("effective_date", "pk")
        .first()
    )
    return cast(Match | None, blocking)


def format_tournament_match_order_block_message(
    blocking_match: Match,
    player: "Player",
) -> str:
    """Текст ошибки при нарушении порядка внесения результатов в турнире.

    Args:
        blocking_match (Match): Матч, который нужно завершить раньше.
        player (Player): Игрок, которому показывается сообщение.

    Returns:
        str: Сообщение для UI или flash-сообщения.
    """
    if blocking_match.deadline:
        date_label = timezone.localtime(blocking_match.deadline).strftime("%d.%m.%Y")
    elif blocking_match.scheduled_datetime:
        date_label = timezone.localtime(blocking_match.scheduled_datetime).strftime(
            "%d.%m.%Y"
        )
    else:
        date_label = "более раннюю дату"
    opponents = get_match_opponents_for_player(blocking_match, player)
    opponent_label = ", ".join(
        p.user.get_full_name() or p.user.email or str(p) for p in opponents
    )
    if not opponent_label:
        opponent_label = "соперником"
    return (
        f"Сначала завершите матч от {date_label} ({opponent_label}). "
        "В турнире результаты вносятся по порядку дедлайнов."
    )


def attach_tournament_result_order_flags(
    matches: Iterable[Match],
    player: "Player",
) -> None:
    """Пометить матчи флагами блокировки ввода результата по порядку дат.

    Args:
        matches (Iterable[Match]): Список матчей игрока.
        player (Player): Текущий игрок.

    Returns:
        None: Атрибуты ``result_order_blocked_by`` и ``result_order_block_message``
        задаются на объектах матчей.
    """
    for match in matches:
        blocker = find_blocking_earlier_tournament_match(match, player)
        match.result_order_blocked_by = blocker
        match.result_order_block_message = (
            format_tournament_match_order_block_message(blocker, player)
            if blocker
            else ""
        )


def mark_tournament_bracket_generated(tournament: Tournament) -> None:
    """Отметить сформированную сетку и перевести турнир в активный статус.

    Для форматов FAN, кругового и олимпийской системы при успешной генерации
    матчей статус меняется с «Предстоящий» на «Активный». ТВД использует
    собственные статусы (групповой этап / плей-офф).

    Args:
        tournament (Tournament): Турнир после создания матчей.

    Returns:
        None: Поля сохраняются в базе данных.
    """
    tournament.bracket_generated = True
    update_fields = ["bracket_generated", "updated_at"]
    if tournament.status == TournamentStatus.UPCOMING:
        tournament.status = TournamentStatus.ACTIVE
        update_fields.append("status")
    tournament.save(update_fields=update_fields)


def get_tournament_participant_users(tournament: Tournament) -> list:
    """
    Список пользователей (User) — всех участников турнира (для рассылки уведомлений).
    Одиночный: participants. Парный: игроки из всех команд (player1, player2).
    """
    users = set()
    if tournament.is_doubles():
        for team in tournament.teams.select_related("player1__user", "player2__user"):
            if (
                team.player1_id
                and getattr(team.player1, "user_id", None)
                and not getattr(team.player1, "is_bye", False)
            ):
                users.add(team.player1.user)
            if (
                team.player2_id
                and getattr(team.player2, "user_id", None)
                and not getattr(team.player2, "is_bye", False)
            ):
                users.add(team.player2.user)
    else:
        for player in tournament.participants.select_related("user"):
            if getattr(player, "user_id", None) and not getattr(
                player, "is_bye", False
            ):
                users.add(player.user)
    return list(users)


def get_match_participants(match: Match) -> set:
    """
    Возвращает множество игроков (Player) — участников матча.
    Одиночные: player1, player2. Парные по командам: team1/team2. Парный спарринг: player1, partner1, player2, partner2.
    """
    participants = set()
    if match.team1_id and match.team2_id:
        for team in (match.team1, match.team2):
            if team:
                if team.player1_id:
                    participants.add(team.player1)
                if team.player2_id:
                    participants.add(team.player2)
    else:
        if match.player1_id:
            participants.add(match.player1)
        if match.player2_id:
            participants.add(match.player2)
        if match.partner1_id:
            participants.add(match.partner1)
        if match.partner2_id:
            participants.add(match.partner2)
    return participants


def get_match_participant_users(match: Match) -> list:
    """Список пользователей (User) — участников матча (для уведомлений). Без bye-игроков."""
    participants = get_match_participants(match)
    return [
        p.user
        for p in participants
        if getattr(p, "user_id", None) and not getattr(p, "is_bye", False)
    ]


def get_match_opponents_for_player(match: Match, player) -> list:
    """
    Возвращает игроков (Player) противоположной стороны матча для заданного игрока.

    Используется для персональных уведомлений (показать контакты соперника).
    В парном матче возвращаются только игроки другой команды/пары; сокомандник не входит.
    bye-игроки исключаются.

    Args:
        match (Match): Матч, для которого определяются соперники.
        player (Player): Игрок, относительно которого ищется противоположная сторона.

    Returns:
        list: Список объектов ``Player`` — соперники игрока (без bye-игроков).
    """
    if match.partner1_id and match.partner2_id:
        side1 = (match.player1, match.partner1)
        side2 = (match.player2, match.partner2)
        if player in side1:
            opponents = side2
        elif player in side2:
            opponents = side1
        else:
            return []
        return [p for p in opponents if p and not getattr(p, "is_bye", False)]
    if match.team1_id and match.team2_id:
        if not match.team1 or not match.team2:
            return []
        in_team1 = player in (match.team1.player1, match.team1.player2)
        in_team2 = player in (match.team2.player1, match.team2.player2)
        if in_team1:
            opponent_team = match.team2
        elif in_team2:
            opponent_team = match.team1
        else:
            return []
        return [
            p
            for p in (opponent_team.player1, opponent_team.player2)
            if p and not getattr(p, "is_bye", False)
        ]
    # Одиночный матч
    other = None
    if match.player1_id and player != match.player1:
        other = match.player1
    elif match.player2_id and player != match.player2:
        other = match.player2
    if not other or getattr(other, "is_bye", False):
        return []
    return [other]


def get_opponents_contacts_short(opponents: list) -> str:
    """Компактная строка контактов соперника(ов) для ЛК-уведомления (plain text).

    Включает заполненные контакты: Telegram, WhatsApp, MAX, телефон. Формат
    рассчитан на короткое сообщение (поле ``Notification.message`` ограничено
    255 символами).

    Args:
        opponents (list): Список объектов ``Player`` — соперники получателя.

    Returns:
        str: Строка вида ``"TG @user, тел. +7..."``; для парных матчей контакты
        каждого соперника предваряются его именем. Если контактов нет —
        ``"контакты не указаны"``.
    """
    multi = len([o for o in opponents if o and not getattr(o, "is_bye", False)]) > 1
    chunks: list[str] = []
    for opp in opponents:
        if not opp or getattr(opp, "is_bye", False):
            continue
        items: list[str] = []
        telegram = str(getattr(opp, "telegram", "") or "").strip().lstrip("@")
        if telegram:
            items.append(f"TG @{telegram}")
        whatsapp = str(getattr(opp, "whatsapp", "") or "").strip()
        if whatsapp:
            items.append(f"WhatsApp {whatsapp}")
        max_display = getattr(opp, "max_contact_display", None)
        if max_display:
            items.append(f"MAX {max_display}")
        phone = str(getattr(getattr(opp, "user", None), "phone", "") or "").strip()
        if phone:
            items.append(f"тел. {phone}")
        joined = ", ".join(items) if items else "контакты не указаны"
        chunks.append(f"{opp.get_display_name()}: {joined}" if multi else joined)
    return "; ".join(chunks) if chunks else "контакты не указаны"


def get_match_opponent_users(match: Match, exclude_player) -> list:
    """
    Пользователи только противоположной стороны (соперники).
    В парном матче возвращаются только игроки другой команды; сокомандник не входит.
    Без bye-игроков.
    """
    return [
        p.user
        for p in get_match_opponents_for_player(match, exclude_player)
        if getattr(p, "user_id", None)
    ]


@dataclass(frozen=True)
class PlayerTrophy:
    """Кубок игрока за призовое место в завершённом турнире.

    Attributes:
        place (int): Занятое место (1–3).
        tournament_name (str): Название турнира.
        tournament_slug (str): Слаг турнира для ссылки на его страницу.
        date (date | None): Дата окончания (или начала) турнира.
    """

    place: int
    tournament_name: str
    tournament_slug: str
    date: date | None

    @property
    def image_path(self) -> str:
        """Путь к изображению кубка относительно каталога static.

        Returns:
            str: Относительный путь вида ``images/trophy-place-1.png``.
        """
        return f"images/trophy-place-{self.place}.png"


def get_players_trophies_map(
    player_ids: Iterable[int],
    club: "Club | None" = None,
) -> dict[int, list[PlayerTrophy]]:
    """Собирает кубки за призовые места (1–3) для набора игроков одним запросом.

    Место берётся из ``TournamentPlayerResult.place`` (олимпийская система,
    круговой формат, ТВД). Для одноэтапной сетки, где место не записывается,
    победитель основной сетки считается 1-м местом, финалист — 2-м.

    Args:
        player_ids (Iterable[int]): Идентификаторы игроков.
        club (Club | None): Клуб для фильтрации турниров. ``None`` — только
            платформенные турниры (без клуба).

    Returns:
        dict[int, list[PlayerTrophy]]: Кубки по id игрока; каждый список
        отсортирован по месту (1-е выше), внутри места — от новых турниров
        к старым.
    """
    from .models import TournamentPlayerResult

    results = (
        TournamentPlayerResult.objects.filter(
            player_id__in=list(player_ids),
            tournament__status=TournamentStatus.COMPLETED,
        )
        .filter(
            Q(place__in=(1, 2, 3))
            | Q(
                place__isnull=True,
                is_consolation=False,
                round_eliminated__in=(
                    TournamentPlayerResult.RoundEliminated.WINNER,
                    TournamentPlayerResult.RoundEliminated.FINAL,
                ),
            )
        )
        .select_related("tournament")
    )
    if club is None:
        results = results.filter(tournament__club__isnull=True)
    else:
        results = results.filter(tournament__club=club)

    trophies_map: dict[int, list[PlayerTrophy]] = {}
    for result in results:
        place = result.place
        if place is None:
            # Одноэтапная сетка: место не записано, определяем по раунду вылета.
            is_winner = (
                result.round_eliminated == TournamentPlayerResult.RoundEliminated.WINNER
            )
            place = 1 if is_winner else 2
        trophies_map.setdefault(result.player_id, []).append(
            PlayerTrophy(
                place=place,
                tournament_name=result.tournament.name,
                tournament_slug=result.tournament.slug,
                date=result.tournament.end_date or result.tournament.start_date,
            )
        )
    for trophies in trophies_map.values():
        trophies.sort(
            key=lambda t: (t.place, -(t.date.toordinal() if t.date else 0)),
        )
    return trophies_map


def get_player_trophies(
    player: "Player",
    club: "Club | None" = None,
) -> list[PlayerTrophy]:
    """Собирает кубки игрока за призовые места (1–3) в завершённых турнирах.

    Args:
        player (Player): Игрок, для которого собираются кубки.
        club (Club | None): Клуб для фильтрации турниров. ``None`` — только
            платформенные турниры (без клуба).

    Returns:
        list[PlayerTrophy]: Кубки, отсортированные по месту (1-е выше),
        внутри места — от новых турниров к старым.
    """
    return get_players_trophies_map([player.pk], club=club).get(player.pk, [])


def notify_participants_match_deadline_changed(
    match: Match,
    *,
    old_deadline,
    new_deadline,
) -> tuple[int, int]:
    """Уведомить участников матча об изменении дедлайна (ЛК + email).

    Args:
        match (Match): Матч с обновлённым дедлайном.
        old_deadline: Предыдущий дедлайн (datetime | None).
        new_deadline: Новый дедлайн (datetime | None).

    Returns:
        tuple[int, int]: ``(число уведомлений в ЛК, число отправленных писем)``.
    """
    from django.urls import reverse

    from apps.core.email_service import send_match_deadline_changed_email
    from apps.users.models import Notification

    tournament = match.tournament
    if tournament is None:
        return 0, 0

    new_str = (
        timezone.localtime(new_deadline).strftime("%d.%m.%Y") if new_deadline else "—"
    )
    old_str = (
        timezone.localtime(old_deadline).strftime("%d.%m.%Y") if old_deadline else ""
    )
    match_path = reverse("match_detail", kwargs={"pk": match.pk})
    base_url = getattr(settings, "TELEGRAM_BOT_SITE_BASE_URL", "").strip().rstrip("/")
    if not base_url:
        domain = getattr(settings, "SITE_DOMAIN", "tennisfan.ru")
        base_url = f"https://{domain}"
    match_url = f"{base_url}{match_path}"

    tour_name = tournament.name or "турнир"
    if len(tour_name) > 60:
        tour_name = tour_name[:57] + "..."
    msg_lk = f"Дедлайн матча в турнире «{tour_name}» изменён: до {new_str}."
    if len(msg_lk) > 255:
        msg_lk = msg_lk[:252] + "..."

    notified = 0
    emailed = 0
    for user in dict.fromkeys(get_match_participant_users(match)):
        try:
            Notification.objects.create(user=user, message=msg_lk, url=match_path)
            notified += 1
        except Exception:
            logger.exception(
                "Не удалось создать уведомление о дедлайне для user=%s match=%s",
                getattr(user, "pk", None),
                match.pk,
            )
        try:
            if send_match_deadline_changed_email(
                user,
                tournament,
                new_deadline_str=new_str,
                old_deadline_str=old_str,
                round_name=match.round_name or "",
                match_url=match_url,
            ):
                emailed += 1
        except Exception:
            logger.exception(
                "Не удалось отправить email о дедлайне user=%s match=%s",
                getattr(user, "pk", None),
                match.pk,
            )
    return notified, emailed
