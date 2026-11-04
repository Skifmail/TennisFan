"""Согласия показывают заголовки и списки без исполнения исходного HTML."""

from apps.legal.templatetags.legal_tags import legal_markdown


def test_consent_markdown_formats_sections_and_escapes_html() -> None:
    text = "# Согласие\n\n## 1. Условия\n\n**Важно**\n\n- Пункт\n\n<script>alert(1)</script>"
    html = legal_markdown(text)
    assert "<h1>Согласие</h1>" in html
    assert "<h2>1. Условия</h2>" in html
    assert "<strong>Важно</strong>" in html
    assert "<li>Пункт</li>" in html
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
