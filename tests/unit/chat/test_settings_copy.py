"""Проверки кратких подписей настроек городского чата."""

from pathlib import Path


def test_settings_when_profile_controls_shown_should_omit_explanatory_paragraphs() -> (
    None
):
    """Настройки сохраняют кнопки без лишних пояснений о фотографии."""
    template = (
        Path(__file__).resolve().parents[3] / "templates/chat/room.html"
    ).read_text()
    assert "Показ имени и фото можно изменить" not in template
    assert "Фотография берётся из профиля" not in template
    assert "фотографию автоматически" not in template
    assert "Добавить фото в профиль" in template
    assert "Изменить согласия" in template
