"""Контекст положения о турнире, общий для PDF и Word.

Тексты разделов собираются из полей турнира и коротких правил формата,
чтобы PDF и DOCX не расходились.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from django.utils import timezone

from apps.tournaments.models import Tournament, TournamentFormat
from apps.users.skill_levels import skill_with_ntrp

SECTION_GENERAL = "Общие положения"
SECTION_SCHEDULE = "Сроки и место проведения"
SECTION_PARTICIPANTS = "Участники"
SECTION_FEE = "Регистрация и взнос"
SECTION_SYSTEM = "Система проведения"
SECTION_WALKOVER = "Неявки и сроки матчей"
SECTION_POINTS = "Система начисления очков"
SECTION_EXTRA = "Дополнительные положения"
SECTION_DESCRIPTION = "Описание турнира"

_MATCH_FORMAT_TEXT: dict[str, str] = {
    "1_set_6": "1 сет до 6 геймов. При счёте 6:6 сет играется до 7.",
    "1_set_tiebreak": "1 сет до 6 геймов, при 6:6 — тай-брейк до 7 очков.",
    "2_sets": (
        "Матч из двух сетов. Побеждает тот, кто выиграет оба; "
        "при счёте 1:1 играется решающий сет или, по договорённости, тай-брейк."
    ),
    "fast4": "Два коротких сета до 4 геймов, при счёте 1:1 — супертай-брейк до 10 очков.",
}


def _lines(*parts: str) -> tuple[str, ...]:
    """Собрать абзацы правил в кортеж, удобный для mypy."""
    return parts


_FORMAT_RULES: dict[str, tuple[str, ...]] = {
    "single_elimination": _lines(
        "Турнир играется на выбывание: проигравший в основной сетке выбывает, "
        "победитель проходит в следующий раунд.",
        "Участники посеяны по рейтингу, сильнейшие не встречаются в первом круге. "
        "Если число участников не степень двойки, свободные места занимает "
        "служебный участник «Свободный круг»: соперник проходит дальше без игры.",
        "Проигравшие в первом круге играют дополнительную сетку («подвал»), "
        "чтобы при достаточном составе каждый провёл минимум два матча.",
        "Очки рейтинга начисляются в момент вылета: чем дальше прошёл участник, "
        "тем больше очков он получает.",
    ),
    "olympic_consolation": _lines(
        "Турнир играется по олимпийской системе с определением всех мест.",
        "Основная сетка — на выбывание, посев по рейтингу. При нечётном составе "
        "используется «Свободный круг».",
        "Проигравший в раунде основной сетки переходит в утешительную сетку "
        "за соответствующий диапазон мест (3–4, 5–8 и далее). "
        "Итоговое место определяется только сыгранным матчем.",
        "Очки рейтинга начисляются по занятому месту после последнего матча участника.",
    ),
    "round_robin": _lines(
        "Каждый участник играет с каждым ровно один раз.",
        "При нечётном составе в каждом туре один участник свободен от игры.",
        "Победитель определяется по числу побед: 1 очко за победу, 0 за поражение. "
        "При равенстве учитываются личная встреча, затем разница сетов и геймов.",
    ),
    "weekend_day": _lines(
        "Турнир проходит в два этапа: групповой и плей-офф.",
        "Участники распределяются змейкой по рейтингу в 2–6 групп. "
        "В группе каждый играет с каждым.",
        "В основную сетку плей-офф выходят занявшие 1-е и 2-е места. "
        "Пары первого раунда составляются так, чтобы участники одной группы "
        "не встретились сразу. Занявшие 3-е места играют утешительную сетку.",
        "Очки рейтинга начисляются по итоговому месту.",
    ),
}


@dataclass(frozen=True)
class InfoRow:
    """Строка таблицы «параметр — значение»."""

    label: str
    value: str


@dataclass(frozen=True)
class Section:
    """Раздел положения."""

    title: str
    paragraphs: tuple[str, ...] = ()
    rows: tuple[InfoRow, ...] = ()
    bullets: tuple[str, ...] = ()


@dataclass(frozen=True)
class RegulationContext:
    """Готовое положение о турнире."""

    tournament_name: str
    sport_label: str
    format_label: str
    variant_label: str
    organizer_name: str
    generated_on: str
    public_url: str
    sections: tuple[Section, ...]


def build_regulation_context(
    tournament: Tournament,
    *,
    public_url: str = "",
    generated_at: datetime | None = None,
) -> RegulationContext:
    """Собрать разделы положения из данных турнира.

    Args:
        tournament: Турнир, для которого формируется документ.
        public_url: Абсолютная ссылка на публичную страницу.
        generated_at: Момент формирования. По умолчанию — текущее время.

    Returns:
        RegulationContext: Разделы, общие для PDF и Word.
    """
    moment = generated_at or timezone.localtime()
    if timezone.is_aware(moment):
        moment = timezone.localtime(moment)
    sections = (
        _general_section(tournament),
        _schedule_section(tournament),
        _participants_section(tournament),
        _fee_section(tournament),
        _system_section(tournament),
        _walkover_section(tournament),
        _points_section(tournament),
        _extra_section(tournament),
        _description_section(tournament),
    )
    club = tournament.club
    return RegulationContext(
        tournament_name=tournament.name,
        sport_label=tournament.get_sport_display(),
        format_label=tournament.get_format_display(),
        variant_label=tournament.get_variant_display(),
        organizer_name=club.name if club else "TennisFan",
        generated_on=moment.strftime("%d.%m.%Y"),
        public_url=public_url,
        sections=tuple(section for section in sections if section is not None),
    )


def _general_section(tournament: Tournament) -> Section:
    """Цели, организатор и вид соревнования."""
    club = tournament.club
    if club is None:
        organizer = "Организатор — платформа TennisFan."
    else:
        contacts = ", ".join(
            part for part in (club.address, club.phone, club.email) if part
        )
        organizer = f"Организатор — {club.name}."
        if contacts:
            organizer = f"{organizer} Контакты: {contacts}."
    return Section(
        title=SECTION_GENERAL,
        paragraphs=(
            "Положение определяет порядок проведения турнира, условия допуска, "
            "систему розыгрыша и начисление рейтинговых очков на платформе TennisFan.",
            organizer,
        ),
        rows=(
            InfoRow("Название", tournament.name),
            InfoRow("Вид спорта", tournament.get_sport_display()),
            InfoRow("Тип турнира", tournament.get_tournament_type_display()),
            InfoRow("Статус", tournament.get_status_display()),
        ),
    )


def _schedule_section(tournament: Tournament) -> Section:
    """Сроки, город и площадка."""
    duration = (
        "Однодневный" if tournament.is_one_day else tournament.get_duration_display()
    )
    days = tournament.match_days_per_round or 7
    rows = [
        InfoRow("Сроки", _dates_label(tournament)),
        InfoRow("Формат по длительности", duration),
        InfoRow("Дней на раунд", str(days)),
        InfoRow("Место", _place_label(tournament)),
    ]
    court = tournament.court
    if court is not None and not tournament.venue_is_pending:
        if court.address:
            rows.append(InfoRow("Адрес площадки", court.address))
        surface = (court.surface or "").strip()
        if surface:
            rows.append(InfoRow("Покрытие", surface))
    return Section(title=SECTION_SCHEDULE, rows=tuple(rows))


def _participants_section(tournament: Tournament) -> Section:
    """Допуск: категории, пол, численность."""
    categories = [
        skill_with_ntrp(item.category) for item in tournament.allowed_categories.all()
    ]
    if tournament.is_doubles():
        limit_rows = (
            InfoRow("Минимум команд", _optional_count(tournament.min_teams)),
            InfoRow("Максимум команд", _optional_count(tournament.max_teams)),
            InfoRow("Зарегистрировано команд", str(tournament.full_teams_count())),
        )
    else:
        registered = tournament.participants.filter(is_bye=False).count()
        limit_rows = (
            InfoRow("Минимум участников", _optional_count(tournament.min_participants)),
            InfoRow(
                "Максимум участников", _optional_count(tournament.max_participants)
            ),
            InfoRow("Зарегистрировано", str(registered)),
        )
    return Section(
        title=SECTION_PARTICIPANTS,
        rows=(
            InfoRow("Категории", ", ".join(categories) if categories else "Не указаны"),
            InfoRow("Пол", tournament.get_gender_display()),
            InfoRow("Разряд", tournament.get_variant_display()),
            *limit_rows,
        ),
    )


def _fee_section(tournament: Tournament) -> Section:
    """Взнос, постоплата и что не входит в стоимость."""
    rows = [InfoRow("Вступительный взнос", _money(tournament.entry_fee))]
    paragraphs: list[str] = []
    if tournament.allow_postpayment:
        hours = tournament.get_postpayment_deadline_hours()
        rows.append(
            InfoRow(
                "Постоплата",
                f"Разрешена. На оплату даётся {hours} ч. после открытия окна.",
            )
        )
    else:
        rows.append(InfoRow("Постоплата", "Не предусмотрена."))
    if tournament.start_after_fill:
        deadline = "Не задан: турнир запускается по набору состава."
    elif tournament.registration_deadline:
        deadline = timezone.localtime(tournament.registration_deadline).strftime(
            "%d.%m.%Y %H:%M"
        )
    else:
        deadline = "Не указан"
    rows.append(InfoRow("Дедлайн регистрации", deadline))
    if not tournament.is_one_day:
        paragraphs.append(
            "Вступительный взнос не включает оплату корта: "
            "корт на матчи участники оплачивают отдельно."
        )
    paragraphs.append(
        "Регистрация открыта на публичной странице турнира до дедлайна "
        "или до заполнения максимального состава."
    )
    return Section(title=SECTION_FEE, rows=tuple(rows), paragraphs=tuple(paragraphs))


def _system_section(tournament: Tournament) -> Section:
    """Формат сетки и формат матча."""
    rules = _FORMAT_RULES.get(
        str(tournament.format),
        _lines("Порядок розыгрыша определяется организатором."),
    )
    rows = [
        InfoRow("Система", tournament.get_format_display()),
        InfoRow("Вариант", tournament.get_variant_display()),
    ]
    match_format = (tournament.match_format or "").strip()
    if match_format:
        rows.append(InfoRow("Формат матча", tournament.get_match_format_display()))
        extra = _MATCH_FORMAT_TEXT.get(match_format)
        if extra:
            rules = (*rules, extra)
    return Section(title=SECTION_SYSTEM, rows=tuple(rows), paragraphs=rules)


def _walkover_section(tournament: Tournament) -> Section:
    """Дедлайны раундов и техническое поражение."""
    days = tournament.match_days_per_round or 7
    return Section(
        title=SECTION_WALKOVER,
        paragraphs=(
            f"На проведение матчей раунда (тура) отводится {days} дн. "
            "с даты старта раунда. Матч нужно сыграть до дедлайна.",
            "Если матч не сыгран в срок, организатор вправе зафиксировать "
            "техническое поражение неявившейся стороне. При неявке обеих сторон "
            "результат фиксируется как обоюдная неявка.",
            "Перенос дедлайна возможен только решением организатора.",
        ),
    )


def _points_section(tournament: Tournament) -> Section | None:
    """Таблица очков. Для круговой системы раздел не нужен."""
    if tournament.format == TournamentFormat.ROUND_ROBIN:
        return None
    if tournament.format == TournamentFormat.SINGLE_ELIMINATION:
        rows = (
            InfoRow("Вылет в 1 круге", str(tournament.fan_points_r1)),
            InfoRow("Вылет во 2 круге", str(tournament.fan_points_r2)),
            InfoRow("Вылет в полуфинале", str(tournament.fan_points_sf)),
            InfoRow("Финалист", str(tournament.fan_points_final)),
            InfoRow("Победитель", str(tournament.fan_points_winner)),
        )
        note = "Очки начисляются за этап, на котором участник завершил турнир."
    else:
        rows = (
            InfoRow("1 место", str(tournament.fan_points_winner)),
            InfoRow("2 место", str(tournament.fan_points_final)),
            InfoRow("3–4 места", str(tournament.fan_points_sf)),
            InfoRow("5–8 места", str(tournament.fan_points_r2)),
            InfoRow("9 место и ниже", str(tournament.fan_points_r1)),
        )
        note = "Очки начисляются по итоговому месту."
    return Section(title=SECTION_POINTS, rows=rows, paragraphs=(note,))


def _extra_section(tournament: Tournament) -> Section | None:
    """Текст организатора, если он заполнен."""
    text = (tournament.regulation_extra or "").strip()
    if not text:
        return None
    paragraphs = tuple(part.strip() for part in text.splitlines() if part.strip())
    return Section(title=SECTION_EXTRA, paragraphs=paragraphs)


def _description_section(tournament: Tournament) -> Section | None:
    """Публичное описание турнира."""
    text = (tournament.description or "").strip()
    if not text:
        return None
    paragraphs = tuple(part.strip() for part in text.splitlines() if part.strip())
    return Section(title=SECTION_DESCRIPTION, paragraphs=paragraphs)


def _dates_label(tournament: Tournament) -> str:
    """Сроки: явные даты или запуск по набору."""
    if tournament.start_after_fill and tournament.start_date is None:
        return "Старт после набора состава"
    start = _fmt_date(tournament.start_date)
    end = _fmt_date(tournament.end_date)
    if tournament.start_after_fill:
        if start:
            return f"Старт после набора состава (ориентир: {start})"
        return "Старт после набора состава"
    if start and end:
        return f"{start} — {end}"
    return start or "Не указаны"


def _place_label(tournament: Tournament) -> str:
    """Город, район и корт одной строкой."""
    if tournament.venue_is_pending:
        locality = _locality(tournament)
        if locality:
            return f"{locality}. Площадка будет объявлена после набора."
        return "Площадка будет объявлена после набора."
    parts = [_locality(tournament)]
    court = tournament.court
    if court is not None:
        parts.append(court.name)
    text = ", ".join(part for part in parts if part)
    return text or "Не указано"


def _locality(tournament: Tournament) -> str:
    """Район или город и регион без повторов."""
    parts: list[str] = []
    if tournament.geo_area_id and tournament.geo_area is not None:
        parts.append(tournament.geo_area.name)
    if tournament.region:
        region = tournament.get_region_display()
        if region and region not in parts:
            parts.append(region)
    city = (tournament.city or "").strip()
    if city and city not in " ".join(parts):
        parts.append(city)
    return ", ".join(parts)


def _fmt_date(value: date | datetime | None) -> str:
    """Дата в виде ДД.ММ.ГГГГ или пустая строка."""
    if value is None:
        return ""
    return value.strftime("%d.%m.%Y")


def _optional_count(value: int | None) -> str:
    """Число или пометка, что лимит не задан."""
    if value is None:
        return "Не ограничен"
    return str(value)


def _money(value: Decimal | int | float | None) -> str:
    """Сумма взноса: «бесплатно» или рубли без лишних копеек."""
    if value is None:
        return "Бесплатно"
    amount = Decimal(value)
    if amount <= 0:
        return "Бесплатно"
    text = f"{amount:.2f}".replace(".", ",")
    if text.endswith(",00"):
        text = text[:-3]
    return f"{text} ₽"
