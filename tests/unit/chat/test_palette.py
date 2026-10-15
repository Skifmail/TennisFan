"""Контракт фирменной палитры чата и читаемости её цветовых пар."""

import re
from pathlib import Path
from typing import Final

import pytest

ROOT: Final = Path(__file__).resolve().parents[3]
TEXT_CONTRAST: Final = 4.5
CONTROL_CONTRAST: Final = 3.0
RGB_MAX: Final = 255
SRGB_THRESHOLD: Final = 0.04045
SRGB_DIVISOR: Final = 12.92
SRGB_OFFSET: Final = 0.055
SRGB_SCALE: Final = 1.055
SRGB_EXPONENT: Final = 2.4
LUMINANCE_OFFSET: Final = 0.05
LUMINANCE_WEIGHTS: Final = (0.2126, 0.7152, 0.0722)
DARK_CANVAS_LUMINANCE: Final = 0.05
CSS: Final = ROOT / "static/css/pages/chat.css"


def _tokens() -> dict[str, str]:
    root = CSS.read_text().split(".chat-document {", 1)[1].split("}", 1)[0]
    return dict(re.findall(r"(--chat-[\w-]+):\s*([^;]+);", root))


def _rgb(name: str) -> tuple[float, ...]:
    tokens = _tokens()
    value = tokens[name]
    while value.startswith("var("):
        value = tokens[value[4:-1]]
    assert re.fullmatch(r"#[\da-f]{3}(?:[\da-f]{3})?", value), value
    if len(value) == 4:
        value = "#" + "".join(channel * 2 for channel in value[1:])
    return tuple(int(value[index : index + 2], 16) / RGB_MAX for index in (1, 3, 5))


def _luminance(rgb: tuple[float, ...]) -> float:
    linear = tuple(
        (
            channel / SRGB_DIVISOR
            if channel <= SRGB_THRESHOLD
            else ((channel + SRGB_OFFSET) / SRGB_SCALE) ** SRGB_EXPONENT
        )
        for channel in rgb
    )
    return sum(
        weight * value for weight, value in zip(LUMINANCE_WEIGHTS, linear, strict=True)
    )


@pytest.mark.parametrize(
    ("foreground", "background"),
    [
        ("--chat-on-accent", "--chat-accent"),
        ("--chat-on-accent", "--chat-accent-hover"),
        ("--chat-on-accent", "--chat-danger"),
        ("--chat-ink", "--chat-canvas"),
        ("--chat-ink", "--chat-bubble-incoming"),
        ("--chat-secondary", "--chat-canvas"),
        ("--chat-secondary", "--chat-surface"),
        ("--chat-secondary", "--chat-sidebar"),
        ("--chat-secondary", "--chat-selected"),
        ("--chat-secondary", "--chat-hover"),
        ("--chat-secondary", "--chat-field"),
        ("--chat-accent", "--chat-accent-soft"),
        ("--chat-accent", "--chat-accent-soft-hover"),
        ("--chat-avatar-ink", "--chat-avatar"),
        ("--chat-avatar-alt-ink", "--chat-avatar-alt"),
        ("--chat-gold-ink", "--chat-gold-soft"),
        ("--chat-danger", "--chat-danger-soft"),
        ("--chat-disabled-ink", "--chat-disabled"),
        ("--chat-secondary-strong", "--chat-muted"),
    ],
)
def test_palette_when_text_on_surface_should_meet_readable_contrast(
    foreground: str, background: str
) -> None:
    """Текст, подписи и состояния сохраняют контраст не ниже WCAG AA.

    Args:
        foreground: Семантический токен цвета текста.
        background: Семантический токен фоновой поверхности.
    """
    values = sorted((_luminance(_rgb(foreground)), _luminance(_rgb(background))))
    contrast = (values[1] + LUMINANCE_OFFSET) / (values[0] + LUMINANCE_OFFSET)
    assert contrast >= TEXT_CONTRAST, (foreground, background, contrast)


@pytest.mark.parametrize("background", ["--chat-canvas", "--chat-sidebar"])
def test_palette_when_keyboard_focus_should_have_distinct_indicator(
    background: str,
) -> None:
    """Клавиатурный фокус различим на белой и тонированной поверхности.

    Args:
        background: Фоновая поверхность вокруг элемента управления.
    """
    values = sorted((_luminance(_rgb("--chat-focus")), _luminance(_rgb(background))))
    contrast = (values[1] + LUMINANCE_OFFSET) / (values[0] + LUMINANCE_OFFSET)
    assert contrast >= CONTROL_CONTRAST


def test_palette_when_brand_applied_should_use_forest_accent_and_dark_canvas() -> None:
    """Фирменный зелёный фон объединяет чат с основной платформой."""
    red, green, blue = _rgb("--chat-accent")
    assert green > red and green > blue
    assert _luminance(_rgb("--chat-canvas")) < DARK_CANVAS_LUMINANCE
    assert _luminance(_rgb("--chat-surface")) < DARK_CANVAS_LUMINANCE
    assert "--chat-blue" not in CSS.read_text()


def test_palette_when_elements_styled_should_use_shared_color_tokens() -> None:
    """Компоненты не содержат случайных цветов в обход общей палитры."""
    without_tokens = re.sub(r"--chat-[\w-]+:\s*[^;]+;", "", CSS.read_text())
    assert not re.search(r"#[\da-fA-F]{3,8}\b", without_tokens)
    assert not re.search(r":\s*white\s*;", without_tokens)


def test_palette_when_browser_chrome_shown_should_match_page_surface() -> None:
    """Цвет браузерной панели совпадает с фоном пространства чата."""
    template = (ROOT / "templates/chat/base.html").read_text()
    red, green, blue = _rgb("--chat-surface")
    surface = "#" + "".join(
        f"{round(channel * RGB_MAX):02x}" for channel in (red, green, blue)
    )
    assert f'<meta name="theme-color" content="{surface}">' in template


def test_glass_when_transparency_unavailable_should_keep_opaque_surfaces() -> None:
    """Стеклянные панели имеют непрозрачную подложку и доступный режим без blur."""
    css = CSS.read_text()
    assert "@supports (backdrop-filter:" in css
    assert "prefers-reduced-transparency: reduce" in css
    assert "color-scheme: dark" in css
