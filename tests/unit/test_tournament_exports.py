"""Экспорт регламента и сетки турнира."""

from __future__ import annotations

import io
import tempfile
import zipfile
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from django.conf import settings
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image
from weasyprint import HTML

from apps.clubs.models import ClubMember, ClubMemberRole, ClubMemberStatus
from apps.courts.models import Court
from apps.tournaments.exports.bracket import build_bracket_layout
from apps.tournaments.exports.docx_regulation import build_regulation_docx
from apps.tournaments.exports.logos import logos_for_tournament
from apps.tournaments.exports.pdf import render_pdf
from apps.tournaments.exports.public_qr import public_page_qr
from apps.tournaments.exports.regulation import (
    SECTION_EXTRA,
    SECTION_FEE,
    SECTION_POINTS,
    SECTION_SCHEDULE,
    RegulationContext,
    build_regulation_context,
)
from apps.tournaments.models import Match, Tournament, TournamentFormat
from tests.support.factories import make_club, make_player, make_tournament, make_user


def _titles(tournament) -> list[str]:
    """Заголовки разделов положения."""
    return [section.title for section in build_regulation_context(tournament).sections]


def _row_map(tournament, title: str) -> dict[str, str]:
    """Строки одного раздела положения."""
    for section in build_regulation_context(tournament).sections:
        if section.title == title:
            return {row.label: row.value for row in section.rows}
    return {}


class RegulationContextTestCase(TestCase):
    """Состав положения зависит от формата и заполненных полей."""

    def test_elimination_includes_points_and_skips_empty_extra(self) -> None:
        tournament = make_tournament(
            slug="reg-fan",
            format=TournamentFormat.SINGLE_ELIMINATION,
            min_participants=8,
            fan_points_r1=11,
            description="Корт грунтовый.",
        )
        titles = _titles(tournament)
        self.assertIn(SECTION_POINTS, titles)
        self.assertNotIn(SECTION_EXTRA, titles)
        self.assertNotIn("Описание турнира", titles)
        points = _row_map(tournament, SECTION_POINTS)
        self.assertEqual(points["Вылет в 1 круге"], "11")

    def test_round_robin_has_no_points_section(self) -> None:
        tournament = make_tournament(
            slug="reg-rr",
            format=TournamentFormat.ROUND_ROBIN,
        )
        self.assertNotIn(SECTION_POINTS, _titles(tournament))

    def test_postpayment_and_extra_clauses(self) -> None:
        club = make_club(
            name="Клуб Восток",
            slug="club-vostok",
            admin_name="Иванова Мария Петровна",
        )
        tournament = make_tournament(
            slug="reg-extra",
            club=club,
            entry_fee=Decimal("1000.00"),
            allow_postpayment=True,
            postpayment_deadline_hours=24,
            regulation_extra="Мячи Dunlop.\nСудья на финале.",
        )
        titles = _titles(tournament)
        self.assertIn(SECTION_EXTRA, titles)
        fee = _row_map(tournament, SECTION_FEE)
        self.assertIn("в течение 24 часа", fee["Постоплата"])
        self.assertIn("будет направлена ссылка на оплату", fee["Постоплата"])
        self.assertIn("или участие в турнире аннулируется", fee["Постоплата"])
        self.assertNotIn("в противном случае", fee["Постоплата"])
        self.assertEqual(fee["Вступительный взнос"], "1000 ₽")
        context = build_regulation_context(tournament)
        self.assertEqual(context.organizer_name, "Клуб Восток")
        organizer_text = " ".join(
            paragraph
            for section in context.sections
            for paragraph in section.paragraphs
        )
        self.assertIn(
            "Организатор — Клуб Восток, Иванова Мария Петровна.",
            organizer_text,
        )
        extra = next(
            section for section in context.sections if section.title == SECTION_EXTRA
        )
        self.assertEqual(extra.paragraphs, ("Мячи Dunlop.", "Судья на финале."))

    def test_locality_is_in_contacts_and_not_repeated_in_venue(self) -> None:
        club = make_club(
            name="Будь первым",
            slug="bud-pervym",
            city="Воскресенск",
            address="Московская область, ул. Фединская, 2",
            admin_name="Будь первым",
        )
        court = Court.objects.create(
            name="Теннисный центр Воскресенск",
            slug="tc-voskresensk",
            city="Воскресенск",
            address="Воскресенск, Фединская улица 2, село Федино",
        )
        tournament = make_tournament(
            slug="vos-open",
            name="Воскресенск Опен",
            club=club,
            city="Воскресенск",
            court=court,
        )
        context = build_regulation_context(tournament)
        organizer_text = " ".join(
            paragraph
            for section in context.sections
            for paragraph in section.paragraphs
        )
        self.assertIn(
            "Воскресенск, Московская область, ул. Фединская, 2",
            organizer_text,
        )
        place = _row_map(tournament, SECTION_SCHEDULE)
        self.assertEqual(
            place["Адрес площадки"],
            "Фединская улица 2, село Федино",
        )
        self.assertEqual(place["Адрес площадки"].count("Воскресенск"), 0)
        self.assertIn("Воскресенск", place["Место"])

    def test_platform_name_is_used_in_header_and_general_section(self) -> None:
        """Бренд домена попадает в шапку платформенного турнира и в общие положения."""
        platform = make_tournament(slug="brand-platform", club=None)
        top = build_regulation_context(platform, platform_name="TennisTop")
        self.assertEqual(top.organizer_name, "TennisTop")
        self.assertEqual(top.platform_name, "TennisTop")
        top_text = " ".join(
            paragraph for section in top.sections for paragraph in section.paragraphs
        )
        self.assertIn("Организатор — платформа TennisTop.", top_text)
        self.assertIn("на платформе TennisTop.", top_text)
        self.assertNotIn("TennisFan", top_text)

        fan = build_regulation_context(platform)
        self.assertEqual(fan.organizer_name, "TennisFan")
        fan_text = " ".join(
            paragraph for section in fan.sections for paragraph in section.paragraphs
        )
        self.assertIn("на платформе TennisFan.", fan_text)

        club = make_club(name="Клуб Восток", slug="brand-club")
        club_tournament = make_tournament(slug="brand-club-open", club=club)
        club_top = build_regulation_context(club_tournament, platform_name="TennisTop")
        self.assertEqual(club_top.organizer_name, "Клуб Восток")
        club_text = " ".join(
            paragraph
            for section in club_top.sections
            for paragraph in section.paragraphs
        )
        self.assertIn("на платформе TennisTop.", club_text)
        self.assertNotIn("TennisFan", club_text)

    def test_dense_regulation_pdf_stays_on_two_pages(self) -> None:
        """Плотный регламент занимает два листа, QR закреплён на втором."""
        club = make_club(
            name="Будь первым",
            slug="bud-pages",
            city="Воскресенск",
            address="Московская область, ул. Фединская, 2",
            admin_name="Иванова Мария Петровна",
            phone="89123451166",
        )
        tournament = make_tournament(
            slug="pages-open",
            name="Будь первым: Воскресенск Опен",
            club=club,
            city="Воскресенск",
            format=TournamentFormat.SINGLE_ELIMINATION,
            entry_fee=Decimal("1000.00"),
            allow_postpayment=True,
            postpayment_deadline_hours=24,
            regulation_extra=(
                "Мячи предоставляет организатор. Разминка не более 5 минут. "
                "Судья назначается на финал."
            ),
        )
        page = "https://tennisfan.ru/tournaments/pages-open/"
        regulation = build_regulation_context(tournament, public_url=page)
        code = public_page_qr(page)
        html = render_to_string(
            "tournaments/exports/regulation_pdf.html",
            {"regulation": regulation, "logos": (), "public_qr": code},
        )
        self.assertIn("sheet-two", html)
        self.assertIn("running(publicqr)", html)
        static_dir = Path(settings.BASE_DIR) / "static"
        document = HTML(string=html, base_url=f"{static_dir.as_uri()}/").render()
        self.assertEqual(len(document.pages), 2)


class BracketLayoutTestCase(TestCase):
    """Пустая сетка до жеребьёвки и заполненная после."""

    def test_empty_elimination_uses_power_of_two(self) -> None:
        tournament = make_tournament(
            slug="draw-empty",
            format=TournamentFormat.SINGLE_ELIMINATION,
            min_participants=5,
            bracket_generated=False,
        )
        layout = build_bracket_layout(tournament)
        self.assertEqual(layout.status_note, "Предварительная сетка")
        rounds = layout.boards[0].pages[0].rounds
        self.assertEqual(len(rounds), 3)
        self.assertEqual(len(rounds[0].matches), 4)
        names = [(match.side1.name, match.side2.name) for match in rounds[0].matches]
        self.assertEqual(
            names,
            [
                ("Посев 1", "Посев 8"),
                ("Посев 4", "Посев 5"),
                ("Посев 3", "Посев 6"),
                ("Посев 2", "Посев 7"),
            ],
        )

    def test_empty_elimination_is_drawn_for_maximum(self) -> None:
        """До жеребьёвки сетка рисуется на максимальный состав, а не на минимум."""
        tournament = make_tournament(
            slug="draw-max",
            format=TournamentFormat.SINGLE_ELIMINATION,
            min_participants=4,
            max_participants=16,
            bracket_generated=False,
        )
        layout = build_bracket_layout(tournament)
        first_round = [
            match for page in layout.boards[0].pages for match in page.rounds[0].matches
        ]
        self.assertEqual(len(first_round), 8)

    def test_generated_elimination_uses_match_names(self) -> None:
        tournament = make_tournament(
            slug="draw-ready",
            format=TournamentFormat.SINGLE_ELIMINATION,
            bracket_generated=True,
            status="active",
        )
        first = make_player(email_suffix="draw-a", first_name="Иван", last_name="Серов")
        second = make_player(
            email_suffix="draw-b", first_name="Пётр", last_name="Орлов"
        )
        tournament.participants.add(first, second)
        Match.objects.create(
            tournament=tournament,
            round_name="Финал",
            round_index=1,
            round_order=1,
            player1=first,
            player2=second,
            player1_set1=6,
            player2_set1=4,
            winner=first,
            status=Match.MatchStatus.COMPLETED,
            deadline=timezone.now(),
        )
        layout = build_bracket_layout(tournament)
        self.assertTrue(layout.status_note.startswith("Сетка сформирована"))
        match = layout.boards[0].pages[0].rounds[0].matches[0]
        self.assertEqual(match.side1.name, "Иван Серов")
        self.assertTrue(match.side1.is_winner)
        self.assertIn("6:4", match.score)

    def test_empty_round_robin_chess(self) -> None:
        tournament = make_tournament(
            slug="draw-rr",
            format=TournamentFormat.ROUND_ROBIN,
            min_participants=4,
            bracket_generated=False,
        )
        layout = build_bracket_layout(tournament)
        table = layout.tables[0]
        self.assertEqual(len(table.rows), 4)
        self.assertEqual(table.rows[0].cells[0], "—")
        self.assertEqual(table.rows[0].cells[1], "")
        self.assertEqual(layout.boards, ())


class TournamentExportViewTestCase(TestCase):
    """Скачивание доступно менеджеру клуба и закрыто для остальных."""

    def setUp(self) -> None:
        self.club = make_club(slug="export-club")
        self.manager = make_user(email="manager-export@test.local")
        ClubMember.objects.create(
            club=self.club,
            user=self.manager,
            role=ClubMemberRole.ADMIN,
            status=ClubMemberStatus.ACTIVE,
        )
        self.outsider = make_user(email="outsider-export@test.local")
        self.tournament = make_tournament(
            slug="export-open",
            club=self.club,
            format=TournamentFormat.SINGLE_ELIMINATION,
            min_participants=5,
        )

    def test_outsider_is_forbidden(self) -> None:
        self.client.force_login(self.outsider)
        url = reverse(
            "tournament_export_regulation", kwargs={"slug": self.tournament.slug}
        )
        response = self.client.get(url, secure=True)
        self.assertEqual(response.status_code, 403)
        bracket = reverse(
            "tournament_export_bracket", kwargs={"slug": self.tournament.slug}
        )
        self.assertEqual(self.client.get(bracket, secure=True).status_code, 403)

    def test_manager_downloads_pdf_docx_and_bracket(self) -> None:
        self.client.force_login(self.manager)
        regulation = reverse(
            "tournament_export_regulation",
            kwargs={"slug": self.tournament.slug},
        )
        pdf = self.client.get(regulation + "?fmt=pdf", secure=True)
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        self.assertIn("reglament-export-open.pdf", pdf["Content-Disposition"])

        docx = self.client.get(regulation + "?fmt=docx", secure=True)
        self.assertEqual(docx.status_code, 200)
        self.assertEqual(
            docx["Content-Type"],
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        self.assertTrue(docx.content.startswith(b"PK"))

        unknown = self.client.get(regulation + "?fmt=xls", secure=True)
        self.assertEqual(unknown.status_code, 400)

        bracket = self.client.get(
            reverse("tournament_export_bracket", kwargs={"slug": self.tournament.slug}),
            secure=True,
        )
        self.assertEqual(bracket.status_code, 200)
        self.assertTrue(bracket.content.startswith(b"%PDF"))

    @override_settings(ALLOWED_HOSTS=["testserver", "tennistop.ru"])
    def test_docx_uses_tennistop_when_downloaded_from_that_host(self) -> None:
        """Скачивание с tennistop.ru подставляет TennisTop вместо TennisFan."""
        self.client.force_login(self.manager)
        url = reverse(
            "tournament_export_regulation",
            kwargs={"slug": self.tournament.slug},
        )
        response = self.client.get(
            f"{url}?fmt=docx",
            secure=True,
            HTTP_HOST="tennistop.ru",
        )
        self.assertEqual(response.status_code, 200)
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        text = b"".join(
            archive.read(name)
            for name in archive.namelist()
            if name.startswith("word/") and name.endswith(".xml")
        )
        self.assertIn("на платформе TennisTop.".encode(), text)
        self.assertNotIn(b"TennisFan", text)

    def test_manage_page_links_exports(self) -> None:
        self.client.force_login(self.manager)
        response = self.client.get(
            reverse("tournament_manage", kwargs={"slug": self.tournament.slug}),
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Скачать регламент")
        self.assertContains(response, "Скачать сетку (PDF)")
        self.assertContains(response, "fmt=docx")
        self.assertEqual(response.content.decode().count("data-no-page-spinner"), 3)


class _StorageLogo:
    """Логотип облачного хранилища: path недоступен, байты читаются через open."""

    def __init__(self, name: str, raw: bytes, *, broken: bool = False) -> None:
        self.name = name
        self._raw = raw
        self._broken = broken

    def __bool__(self) -> bool:
        return True

    @property
    def path(self) -> str:
        raise NotImplementedError("This backend doesn't support absolute paths.")

    def open(self, mode: str = "rb") -> io.BytesIO:
        if self._broken:
            raise OSError("нет файла")
        return io.BytesIO(self._raw)


def _png(color: tuple[int, int, int]) -> bytes:
    """Крошечный PNG для подстановки вместо файла клуба."""
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _export_request() -> HttpRequest:
    """Запрос с хостом TennisFan, чтобы взялся логотип платформы."""
    return cast(HttpRequest, RequestFactory().get("/", HTTP_HOST="tennisfan.ru"))


def _club_tournament(logo: object) -> Tournament:
    """Клубный турнир без обращения к базе."""
    club = SimpleNamespace(pk=7, name="Альфа", logo=logo)
    return cast(
        Tournament,
        SimpleNamespace(pk=3, club_id=7, club=club, is_open_interclub=False),
    )


class DocumentLogoTestCase(SimpleTestCase):
    """В шапке документа — знак платформы или логотип клуба."""

    def test_platform_tournament_uses_site_logo(self) -> None:
        tournament = cast(
            Tournament,
            SimpleNamespace(pk=1, club_id=None, club=None, is_open_interclub=False),
        )
        logos = logos_for_tournament(tournament, _export_request())
        self.assertEqual([logo.alt for logo in logos], ["TennisFan"])
        self.assertTrue(logos[0].content.startswith(b"\x89PNG"))
        self.assertTrue(logos[0].data_uri.startswith("data:image/png;base64,"))

    @override_settings(ALLOWED_HOSTS=["tennisfan.ru", "tennistop.ru", "testserver"])
    def test_platform_logo_follows_download_host(self) -> None:
        """Знак платформы в шапке — TennisFan или TennisTop, по домену запроса."""
        tournament = cast(
            Tournament,
            SimpleNamespace(pk=1, club_id=None, club=None, is_open_interclub=False),
        )
        factory = RequestFactory()
        fan = logos_for_tournament(
            tournament,
            cast(HttpRequest, factory.get("/", HTTP_HOST="tennisfan.ru")),
        )
        top = logos_for_tournament(
            tournament,
            cast(HttpRequest, factory.get("/", HTTP_HOST="tennistop.ru")),
        )
        self.assertEqual(fan[0].alt, "TennisFan")
        self.assertEqual(top[0].alt, "TennisTop")
        self.assertNotEqual(fan[0].content, top[0].content)

    def test_s3_club_logo_is_embedded(self) -> None:
        raw = _png((180, 30, 30))
        logos = logos_for_tournament(
            _club_tournament(_StorageLogo("clubs/logo.png", raw)),
            _export_request(),
        )
        self.assertEqual(logos[0].alt, "Альфа")
        self.assertEqual(logos[0].content, raw)

    def test_missing_club_logo_falls_back_to_platform(self) -> None:
        logos = logos_for_tournament(
            _club_tournament(_StorageLogo("clubs/logo.png", b"", broken=True)),
            _export_request(),
        )
        self.assertEqual([logo.alt for logo in logos], ["TennisFan"])

    def test_local_club_file_is_read(self) -> None:
        raw = _png((20, 90, 40))
        with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
            tmp.write(raw)
            tmp.flush()

            class _LocalLogo:
                name = "logo.png"

                def __bool__(self) -> bool:
                    return True

                @property
                def path(self) -> str:
                    return tmp.name

                def open(self, mode: str = "rb") -> io.BytesIO:
                    raise AssertionError("локальный файл читается по path")

            logos = logos_for_tournament(
                _club_tournament(_LocalLogo()), _export_request()
            )
        self.assertEqual(logos[0].content, raw)

    def test_docx_and_pdf_templates_include_logo(self) -> None:
        tournament = cast(
            Tournament,
            SimpleNamespace(pk=1, club_id=None, club=None, is_open_interclub=False),
        )
        logos = logos_for_tournament(tournament, _export_request())
        regulation = RegulationContext(
            tournament_name="Кубок",
            sport_label="Теннис",
            format_label="Олимпийская система",
            variant_label="Мужчины",
            organizer_name="TennisFan",
            generated_on="04.10.2026",
            public_url="",
            sections=(),
        )
        docx = build_regulation_docx(regulation, logos)
        names = zipfile.ZipFile(io.BytesIO(docx)).namelist()
        self.assertTrue(any(name.startswith("word/media/") for name in names))

        html = render_to_string(
            "tournaments/exports/regulation_pdf.html",
            {"regulation": regulation, "logos": logos},
        )
        self.assertIn(logos[0].data_uri, html)
        bracket_html = render_to_string(
            "tournaments/exports/bracket_pdf.html",
            {
                "layout": SimpleNamespace(
                    headline="Кубок",
                    format_label="Сетка",
                    status_note="Предварительная сетка",
                    meta=[],
                    footnote="",
                    tables=[],
                    boards=[],
                ),
                "logos": logos,
            },
        )
        self.assertIn(logos[0].data_uri, bracket_html)

        pdf = render_pdf(
            "tournaments/exports/regulation_pdf.html",
            {"regulation": regulation, "logos": logos},
        )
        self.assertIn(b"/Image", pdf)

    def test_public_page_link_is_a_qr_code(self) -> None:
        page = "https://tennisfan.ru/tournaments/demo/"
        code = public_page_qr(page)
        self.assertIsNotNone(code)
        assert code is not None
        self.assertTrue(code.content.startswith(b"\x89PNG"))
        self.assertIsNone(public_page_qr("  "))
        regulation = RegulationContext(
            tournament_name="Кубок",
            sport_label="Теннис",
            format_label="Олимпийская система",
            variant_label="Мужчины",
            organizer_name="TennisFan",
            generated_on="04.10.2026",
            public_url=page,
            sections=(),
        )
        html = render_to_string(
            "tournaments/exports/regulation_pdf.html",
            {"regulation": regulation, "logos": (), "public_qr": code},
        )
        self.assertIn(code.data_uri, html)
        self.assertNotIn(page, html)
        docx = build_regulation_docx(regulation, page_qr=code)
        names = zipfile.ZipFile(io.BytesIO(docx)).namelist()
        self.assertTrue(any(name.startswith("word/media/") for name in names))
        self.assertNotIn(page.encode(), docx)
