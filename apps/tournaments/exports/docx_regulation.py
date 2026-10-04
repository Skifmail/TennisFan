"""Положение о турнире в формате Word."""

from __future__ import annotations

from io import BytesIO
from typing import Any

from docx import Document
from docx.document import Document as WordDocument
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from docx.table import _Cell
from docx.text.paragraph import Paragraph
from loguru import logger as log

from apps.tournaments.exports.logos import ExportLogo
from apps.tournaments.exports.regulation import RegulationContext, Section

_GREEN = RGBColor(0x0B, 0x3D, 0x2E)
_GREEN_HEX = "0B3D2E"
_CREAM = "F7F4EC"
_FONT = "Times New Roman"


def build_regulation_docx(
    regulation: RegulationContext,
    logos: tuple[ExportLogo, ...] = (),
) -> bytes:
    """Собрать .docx положения.

    Args:
        regulation: Готовые разделы документа.
        logos: Логотипы шапки. Клубные либо знак платформы.

    Returns:
        bytes: Файл Office Open XML.
    """
    document = Document()
    _apply_base_style(document)
    _set_margins(document)
    _add_footer(document, regulation)
    _add_logos(document, logos)
    _add_title(document, regulation)
    for index, section in enumerate(regulation.sections, start=1):
        _add_section(document, index, section)
    if regulation.public_url:
        paragraph = document.add_paragraph()
        run = paragraph.add_run(f"Публичная страница: {regulation.public_url}")
        run.italic = True
        run.font.size = Pt(10)
        run.font.color.rgb = _GREEN
        run.font.name = _FONT
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _apply_base_style(document: WordDocument) -> None:
    """Базовый шрифт с кириллицей."""
    style = document.styles["Normal"]
    style.font.name = _FONT
    style.font.size = Pt(11)
    style.font.color.rgb = RGBColor(0x1A, 0x1A, 0x1A)
    _set_style_font(style.element, _FONT)


def _add_logos(document: WordDocument, logos: tuple[ExportLogo, ...]) -> None:
    """Логотипы в правом верхнем углу шапки."""
    if not logos:
        return
    paragraph = document.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    for index, logo in enumerate(logos):
        if index:
            paragraph.add_run(" ")
        run = paragraph.add_run()
        try:
            run.add_picture(BytesIO(logo.content), width=Cm(2.8))
        except Exception as exc:
            log.warning("Не удалось вставить логотип {} в Word: {}", logo.alt, exc)


def _add_title(document: WordDocument, regulation: RegulationContext) -> None:
    """Шапка положения."""
    kicker = document.add_paragraph()
    kicker.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = kicker.add_run("ПОЛОЖЕНИЕ")
    run.bold = True
    run.font.size = Pt(18)
    run.font.color.rgb = _GREEN
    run.font.name = _FONT
    subtitle = document.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub = subtitle.add_run("о проведении турнира")
    sub.font.size = Pt(12)
    sub.font.color.rgb = _GREEN
    sub.font.name = _FONT
    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    name = title.add_run(regulation.tournament_name)
    name.bold = True
    name.font.size = Pt(16)
    name.font.name = _FONT
    _set_paragraph_border(title, "C9A35A")
    meta = document.add_paragraph()
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    meta_run = meta.add_run(
        f"{regulation.sport_label} · {regulation.format_label} · "
        f"{regulation.variant_label}"
    )
    meta_run.font.size = Pt(11)
    meta_run.font.color.rgb = RGBColor(0x5C, 0x6B, 0x64)
    meta_run.font.name = _FONT
    stamp = document.add_paragraph()
    stamp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    stamp_run = stamp.add_run(
        f"{regulation.organizer_name} · документ от {regulation.generated_on}"
    )
    stamp_run.italic = True
    stamp_run.font.size = Pt(10)
    stamp_run.font.name = _FONT


def _add_section(document: WordDocument, index: int, section: Section) -> None:
    """Заголовок раздела, таблица и абзацы."""
    heading = document.add_heading(f"{index}. {section.title}", level=1)
    for run in heading.runs:
        run.font.color.rgb = _GREEN
        run.font.name = _FONT
    if section.rows:
        table = document.add_table(rows=len(section.rows), cols=2)
        table.style = "Table Grid"
        table.autofit = False
        for row_index, info in enumerate(section.rows):
            label_cell = table.rows[row_index].cells[0]
            value_cell = table.rows[row_index].cells[1]
            label_cell.text = ""
            value_cell.text = ""
            label_run = label_cell.paragraphs[0].add_run(info.label)
            label_run.bold = True
            label_run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
            label_run.font.name = _FONT
            label_run.font.size = Pt(10)
            value_run = value_cell.paragraphs[0].add_run(info.value)
            value_run.font.name = _FONT
            value_run.font.size = Pt(10)
            _shade(label_cell, _GREEN_HEX)
            _shade(value_cell, _CREAM)
            label_cell.width = Cm(5.2)
            value_cell.width = Cm(11.5)
        document.add_paragraph()
    for text in section.paragraphs:
        paragraph = document.add_paragraph(text)
        for run in paragraph.runs:
            run.font.name = _FONT
            run.font.size = Pt(11)
    for bullet in section.bullets:
        paragraph = document.add_paragraph(bullet, style="List Bullet")
        for run in paragraph.runs:
            run.font.name = _FONT


def _add_footer(document: WordDocument, regulation: RegulationContext) -> None:
    """Колонтитул: организатор и номер страницы."""
    footer = document.sections[0].footer
    footer.is_linked_to_previous = False
    paragraph = footer.paragraphs[0]
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    left = paragraph.add_run(f"TennisFan · {regulation.organizer_name} · стр. ")
    left.font.size = Pt(9)
    left.font.name = _FONT
    left.font.color.rgb = RGBColor(0x5C, 0x6B, 0x64)
    _append_page_field(paragraph)


def _append_page_field(paragraph: Paragraph) -> None:
    """Поле PAGE в колонтитуле."""
    run = paragraph.add_run()
    run.font.size = Pt(9)
    run.font.name = _FONT
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instruction = OxmlElement("w:instrText")
    instruction.set(qn("xml:space"), "preserve")
    instruction.text = " PAGE "
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.append(begin)
    run._r.append(instruction)
    run._r.append(end)


def _set_margins(document: WordDocument) -> None:
    """Поля страницы A4."""
    section = document.sections[0]
    section.top_margin = Cm(1.6)
    section.bottom_margin = Cm(1.6)
    section.left_margin = Cm(1.7)
    section.right_margin = Cm(1.7)


def _shade(cell: _Cell, fill: str) -> None:
    """Заливка ячейки таблицы."""
    properties = cell._tc.get_or_add_tcPr()
    shading = OxmlElement("w:shd")
    shading.set(qn("w:val"), "clear")
    shading.set(qn("w:color"), "auto")
    shading.set(qn("w:fill"), fill)
    properties.append(shading)


def _set_paragraph_border(paragraph: Paragraph, color: str) -> None:
    """Золотая линия под названием турнира."""
    p_pr = paragraph._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "12")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), color)
    borders.append(bottom)
    p_pr.append(borders)


def _set_style_font(style_element: Any, font_name: str) -> None:
    """Прописать шрифт и для complex script, чтобы кириллица не подменялась."""
    r_pr = style_element.get_or_add_rPr()
    fonts = r_pr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        r_pr.append(fonts)
    fonts.set(qn("w:ascii"), font_name)
    fonts.set(qn("w:hAnsi"), font_name)
    fonts.set(qn("w:cs"), font_name)
