"""
Утилиты турниров (участники матча и т.д.).
Используются в views и в telegram_bot без циклических импортов.
"""

from datetime import datetime, timedelta
from typing import cast

from django.utils import timezone
from django.utils.text import slugify

from .models import Match, Tournament, TournamentStatus

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
