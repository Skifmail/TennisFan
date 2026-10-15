"""Ссылки внутри правовых текстов ведут на опубликованные маршруты."""

from apps.legal.utils import LEGAL_DOCUMENT_PATHS


def test_privacy_when_self_links_rendered_should_use_actual_legal_routes() -> None:
    """Политика не отправляет игрока с формы согласия на старый несуществующий URL."""
    content = LEGAL_DOCUMENT_PATHS["privacy"].read_text()
    for domain in ("tennisfan.ru", "tennistop.ru"):
        assert f"https://{domain}/legal/privacy/" in content
