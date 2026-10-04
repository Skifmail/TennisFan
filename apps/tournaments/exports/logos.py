"""Логотипы шапки экспортируемых документов.

Вызывается из ``tournament_export_regulation`` и ``tournament_export_bracket``.
Картинка уходит в PDF как data URI и в Word как байты.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import BinaryIO, Protocol

from django.conf import settings
from django.http import HttpRequest
from loguru import logger as log
from PIL import Image, UnidentifiedImageError

from apps.clubs.models import Club, ClubApplicationStatus, ClubTournamentApplication
from apps.core.context_processors import site_branding
from apps.tournaments.models import Tournament

_MAX_LOGOS = 8


class _StoredFile(Protocol):
    """Файл хранилища: локальный путь или чтение через storage API."""

    name: str

    @property
    def path(self) -> str:
        """Абсолютный путь. S3 его не отдаёт."""

    def open(self, mode: str = "rb") -> BinaryIO:
        """Открыть файл для чтения."""


@dataclass(frozen=True)
class ExportLogo:
    """Картинка, которую можно встроить и в PDF, и в Word."""

    alt: str
    data_uri: str
    content: bytes


def logos_for_tournament(
    tournament: Tournament, request: HttpRequest
) -> tuple[ExportLogo, ...]:
    """Логотипы документа: клубы турнира либо знак платформы.

    Турнир платформы получает логотип сайта (TennisFan или TennisTop — по домену).
    Клубный турнир — логотип организатора и одобренных клубов межклубного
    турнира. Если ни одного файла нет, в шапку ставится знак платформы.

    Args:
        tournament: Турнир, для которого собирается документ.
        request: Запрос, по нему выбирается логотип платформы.

    Returns:
        Кортеж логотипов. Пустой, только если нет и файла платформы.
    """
    brand = site_branding(request)
    platform_logo = str(brand["site_logo_path"])
    platform_alt = str(brand["site_logo_alt"])
    if tournament.club_id:
        club_logos = _club_logos(tournament)
        if club_logos:
            return tuple(club_logos[:_MAX_LOGOS])
        log.info(
            "У турнира {} нет файлов логотипов клубов, в документ ставится знак платформы",
            tournament.pk,
        )
    platform = _static_logo(platform_logo, platform_alt)
    if platform is None:
        return ()
    return (platform,)


def _club_logos(tournament: Tournament) -> list[ExportLogo]:
    """Логотипы организатора и одобренных клубов-участников."""
    logos: list[ExportLogo] = []
    seen: set[int] = set()
    for club in _document_clubs(tournament):
        if club.pk in seen or not club.logo:
            continue
        seen.add(club.pk)
        raw = _read_stored(club.logo, label=club.name)
        if raw is None:
            continue
        packed = _pack(raw, club.name)
        if packed is not None:
            logos.append(packed)
    return logos


def _document_clubs(tournament: Tournament) -> list[Club]:
    """Клубы, чьи знаки уместны в шапке этого турнира."""
    clubs: list[Club] = []
    organizer = tournament.club
    if organizer is not None:
        clubs.append(organizer)
    if not tournament.is_open_interclub:
        return clubs
    organizer_id = organizer.pk if organizer is not None else None
    applications = (
        ClubTournamentApplication.objects.filter(
            tournament_id=tournament.pk,
            status=ClubApplicationStatus.APPROVED,
        )
        .exclude(applicant_club_id=organizer_id)
        .select_related("applicant_club")
        .order_by("applicant_club__name")
    )
    clubs.extend(item.applicant_club for item in applications)
    return clubs


def _static_logo(relative: str, alt: str) -> ExportLogo | None:
    """Логотип из каталога static/."""
    path = Path(settings.BASE_DIR) / "static" / relative
    if not path.is_file():
        log.warning("Файл логотипа платформы не найден: {}", path)
        return None
    return _pack(path.read_bytes(), alt)


def _read_stored(uploaded: _StoredFile, *, label: str) -> bytes | None:
    """Байты файла. S3 не отдаёт path, поэтому есть чтение через storage."""
    local: Path | None
    try:
        local = Path(uploaded.path)
    except (NotImplementedError, ValueError, OSError):
        local = None
    try:
        if local is not None and local.is_file():
            raw = local.read_bytes()
        else:
            with uploaded.open("rb") as handle:
                raw = handle.read()
    except Exception as exc:
        log.warning("Не удалось прочитать логотип {}: {}", label, exc)
        return None
    if not raw:
        return None
    return raw


def _pack(raw: bytes, alt: str) -> ExportLogo | None:
    """Привести картинку к PNG или JPEG и собрать data URI."""
    prepared = _prepare_image(raw, alt)
    if prepared is None:
        return None
    content, mime = prepared
    encoded = base64.b64encode(content).decode("ascii")
    return ExportLogo(
        alt=alt, data_uri=f"data:{mime};base64,{encoded}", content=content
    )


def _prepare_image(raw: bytes, alt: str) -> tuple[bytes, str] | None:
    """PNG и JPEG оставляем как есть, остальное (в том числе WebP) — в PNG."""
    mime = _sniff_mime(raw)
    if mime is not None:
        return raw, mime
    try:
        image = Image.open(BytesIO(raw))
        image.load()
        buffer = BytesIO()
        image.convert("RGBA").save(buffer, format="PNG")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        log.warning("Логотип {} не удалось открыть как изображение: {}", alt, exc)
        return None
    return buffer.getvalue(), "image/png"


def _sniff_mime(raw: bytes) -> str | None:
    """MIME по сигнатуре, если формат уже подходит для PDF и Word."""
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return None
