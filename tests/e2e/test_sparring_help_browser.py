"""Браузерная регрессия справки спарринга на настоящих шаблонах и CSS."""

from __future__ import annotations

from collections.abc import Iterator
from mimetypes import guess_type
from os import environ
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

import pytest
from django.contrib.staticfiles import finders
from django.test import Client
from django.urls import reverse
from playwright.sync_api import Page, Route, expect, sync_playwright

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        not (environ.get("CHAT_BROWSER_EXECUTABLE") or environ.get("CHAT_E2E_URL")),
        reason="Браузер запускается на отдельном этапе CI",
    ),
]

VIEWPORTS = [(1440, 900), (1024, 768), (375, 667), (320, 568), (667, 375)]


@pytest.fixture
def help_page(client: Client) -> Iterator[Page]:
    """Отдать браузеру страницу из Django и реальную статику проекта."""
    feed_url = reverse("sparring_list")
    response = client.get(feed_url, secure=True)
    assert response.status_code == 200

    def serve(route: Route) -> None:
        """Подменить транспорт, не подменяя HTML, JavaScript или CSS справки."""
        url = urlsplit(route.request.url)
        if url.netloc != "testserver":
            route.abort()
        elif url.path.startswith("/static/"):
            found = finders.find(url.path.removeprefix("/static/"))
            if isinstance(found, str):
                route.fulfill(
                    body=Path(found).read_bytes(),
                    content_type=guess_type(found)[0] or "application/octet-stream",
                )
            else:
                route.fulfill(status=404)
        elif url.path == feed_url:
            route.fulfill(
                body=response.content, content_type="text/html; charset=utf-8"
            )
        else:
            route.fulfill(status=204)

    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ.get("CHAT_BROWSER_EXECUTABLE"), headless=True
        )
        context = browser.new_context(
            viewport={"width": 1440, "height": 900}, reduced_motion="reduce"
        )
        context.route("**/*", serve)
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto("https://testserver" + feed_url)
            page.evaluate("document.fonts.ready")
            page.get_by_role("button", name="Отказаться от cookie", exact=True).click()
            yield page
            assert not errors, "\n".join(errors)
        finally:
            context.close()
            browser.close()


@pytest.mark.parametrize(("width", "height"), VIEWPORTS)
def test_help_trigger_stays_next_to_centered_heading(
    help_page: Page, width: int, height: int
) -> None:
    """Название и кнопка находятся рядом внутри контейнера на любом экране."""
    help_page.set_viewport_size({"width": width, "height": height})
    help_page.screenshot(
        path=f"/tmp/opencode/sparring-help-header-{width}x{height}.png"
    )
    problems = cast(
        list[str],
        help_page.evaluate(
            """() => {
                const problems = [];
                const header = document.querySelector('.sparring-page-header');
                const container = header.closest('.container');
                if (!container) return ['заголовок находится вне контейнера'];
                const bounds = container.getBoundingClientRect();
                const styles = getComputedStyle(container);
                const left = bounds.left + parseFloat(styles.paddingLeft);
                const right = bounds.right - parseFloat(styles.paddingRight);
                for (const element of header.querySelectorAll('h1, p, button')) {
                    const box = element.getBoundingClientRect();
                    if (box.left < left - 1 || box.right > right + 1) {
                        problems.push('элемент заголовка выходит за контейнер');
                    }
                    if (element.scrollWidth > element.clientWidth + 1) {
                        problems.push('текст заголовка обрезан');
                    }
                }
                const title = header.querySelector('h1').getBoundingClientRect();
                const trigger = header.querySelector('button').getBoundingClientRect();
                const gap = trigger.left - title.right;
                if (gap < 0 || gap > 16) problems.push('кнопка далеко от названия');
                if (Math.abs((title.top + title.bottom - trigger.top - trigger.bottom) / 2) > 1) {
                    problems.push('кнопка не выровнена с названием');
                }
                if (Math.abs((title.left + trigger.right - left - right) / 2) > 1) {
                    problems.push('название с кнопкой не отцентрированы');
                }
                if (trigger.width < 44 || trigger.height < 44) {
                    problems.push('слишком маленькая область нажатия');
                }
                if (document.documentElement.scrollWidth > innerWidth) {
                    problems.push('страница шире экрана');
                }
                return problems;
            }"""
        ),
    )
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize(("width", "height"), VIEWPORTS)
def test_help_dialog_is_inset_centered_and_scrolls_only_its_body(
    help_page: Page, width: int, height: int
) -> None:
    """Окно не полноэкранное, все шаги доступны, закрытие не уезжает при прокрутке."""
    help_page.set_viewport_size({"width": width, "height": height})
    help_page.get_by_role("button", name="Справка по спаррингам", exact=True).click()
    dialog = help_page.get_by_role("dialog")
    expect(dialog).to_be_visible()
    assert dialog.evaluate("element => element.matches(':modal')")
    help_page.screenshot(
        path=f"/tmp/opencode/sparring-help-dialog-{width}x{height}.png"
    )

    problems = cast(
        list[str],
        dialog.evaluate(
            """dialog => {
                const problems = [];
                const box = dialog.getBoundingClientRect();
                if (box.left < 8 || box.right > innerWidth - 8 ||
                    box.top < 8 || box.bottom > innerHeight - 8) {
                    problems.push('окно не оставляет отступов от краёв экрана');
                }
                if (Math.abs((box.left + box.right) / 2 - innerWidth / 2) > 1 ||
                    Math.abs((box.top + box.bottom) / 2 - innerHeight / 2) > 1) {
                    problems.push('окно не отцентрировано');
                }
                const close = dialog.querySelector('.sparring-help__close');
                if (!close) {
                    problems.push('нет закрытия в шапке окна');
                } else {
                    const closeBox = close.getBoundingClientRect();
                    if (closeBox.width < 44 || closeBox.height < 44) {
                        problems.push('закрытие неудобно нажимать');
                    }
                }
                const footerButton = dialog.querySelector('.sparring-help__footer .btn');
                if (parseFloat(getComputedStyle(footerButton).fontSize) < 14) {
                    problems.push('подпись закрытия слишком мелкая');
                }
                for (const element of dialog.querySelectorAll('h2, summary, p, .sparring-help__body')) {
                    if (element.scrollWidth > element.clientWidth + 1) {
                        problems.push('содержимое справки обрезано по ширине');
                    }
                }
                return problems;
            }"""
        ),
    )
    assert not problems, "\n".join(problems)

    close = dialog.get_by_role("button", name="Закрыть справку", exact=True)
    initial_close_box = close.bounding_box()
    initial_dialog_box = dialog.bounding_box()
    assert initial_close_box is not None
    assert initial_dialog_box is not None
    body = dialog.locator(".sparring-help__body")
    steps = dialog.locator("details")
    expect(steps).to_have_count(7)
    expect(steps.first.locator("summary")).to_contain_text(
        "Найдите или создайте заявку"
    )
    for index, step in enumerate(steps.all()):
        summary = step.locator("summary")
        summary_box = summary.bounding_box()
        assert summary_box is not None
        assert summary_box["height"] >= 44
        summary.click()
        expect(step).to_have_attribute("open", "")
        if index == 0:
            help_page.screenshot(
                path=f"/tmp/opencode/sparring-help-first-step-{width}x{height}.png"
            )

    assert body.evaluate("element => element.scrollHeight > element.clientHeight")
    body.evaluate("element => { element.scrollTop = element.scrollHeight; }")
    expect(close).to_be_in_viewport(ratio=1)
    expect(dialog.get_by_role("button", name="Закрыть", exact=True)).to_be_in_viewport(
        ratio=1
    )
    expanded_close_box = close.bounding_box()
    expanded_dialog_box = dialog.bounding_box()
    assert expanded_close_box is not None
    assert expanded_dialog_box is not None
    # Высота окна может вырасти: проверяется положение закрытия именно в шапке.
    for axis in ("x", "y"):
        initial_offset = initial_close_box[axis] - initial_dialog_box[axis]
        expanded_offset = expanded_close_box[axis] - expanded_dialog_box[axis]
        assert abs(expanded_offset - initial_offset) <= 1
    assert body.evaluate(
        """element => {
            const body = element.getBoundingClientRect();
            const note = element.querySelector('.sparring-help__note').getBoundingClientRect();
            return note.top >= body.top - 1 && note.bottom <= body.bottom + 1;
        }"""
    )
    help_page.screenshot(
        path=f"/tmp/opencode/sparring-help-expanded-{width}x{height}.png"
    )


def test_help_closes_with_escape_both_buttons_and_backdrop(help_page: Page) -> None:
    """Любое закрытие возвращает фокус и снимает блокировку прокрутки страницы."""
    trigger = help_page.get_by_role("button", name="Справка по спаррингам", exact=True)
    dialog = help_page.get_by_role("dialog")
    initial_overflow = cast(
        str, help_page.evaluate("getComputedStyle(document.documentElement).overflowY")
    )

    for method in ("escape", "header", "footer", "backdrop", "header", "footer"):
        trigger.click()
        expect(dialog).to_be_visible()
        expect(dialog.get_by_role("button", name="Закрыть справку")).to_be_focused()
        assert help_page.evaluate(
            "getComputedStyle(document.documentElement).overflowY === 'hidden'"
        )
        if method == "escape":
            help_page.keyboard.press("Escape")
        elif method == "header":
            dialog.get_by_role("button", name="Закрыть справку").click()
        elif method == "footer":
            dialog.get_by_role("button", name="Закрыть", exact=True).click()
        else:
            help_page.mouse.click(1, 1)
        expect(dialog).not_to_be_visible()
        expect(trigger).to_be_focused()
        expect(help_page.locator("html")).to_have_css("overflow-y", initial_overflow)


def test_help_keeps_inside_clicks_and_keyboard_off_background(help_page: Page) -> None:
    """Край окна не закрывает его, клавиатура раскрывает шаги без перехода на фон."""
    help_page.get_by_role("button", name="Справка по спаррингам", exact=True).click()
    dialog = help_page.get_by_role("dialog")
    box = dialog.bounding_box()
    assert box is not None
    help_page.mouse.click(box["x"] + 1, box["y"] + 1)
    expect(dialog).to_be_visible()

    step = dialog.locator("details").first
    summary = step.locator("summary")
    summary.focus()
    help_page.keyboard.press("Enter")
    expect(step).to_have_attribute("open", "")
    help_page.keyboard.press("Enter")
    expect(step).not_to_have_attribute("open", "")
    for _ in range(12):
        help_page.keyboard.press("Tab")
        # Нативный dialog допускает переход в панель браузера, но не на фоновые поля.
        assert dialog.evaluate(
            "element => document.activeElement === document.body || element.contains(document.activeElement)"
        )
    trigger = help_page.locator(".sparring-help__trigger")
    trigger.evaluate("element => element.focus()")
    expect(trigger).not_to_be_focused()
    help_page.keyboard.press("Escape")
    expect(dialog).not_to_be_visible()
