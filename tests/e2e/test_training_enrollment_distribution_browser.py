"""Браузер: все три кнопки админки отправляют отдельные формы распределения."""

from __future__ import annotations

from mimetypes import guess_type
from os import environ
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.staticfiles import finders
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import Route, expect, sync_playwright

from apps.training.models import Coach, Training, TrainingEnrollment
from tests.support.factories import make_user

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        not environ.get("CHAT_BROWSER_EXECUTABLE"),
        reason="Браузер запускается на отдельном этапе CI",
    ),
]


@pytest.mark.parametrize("width", [1280, 390])
@pytest.mark.parametrize("surface", ["list", "change"])
@pytest.mark.parametrize("action", ["original", "self", "other"])
def test_admin_distribution_buttons_submit_the_correct_post(
    client: Client, action: str, width: int, surface: str
) -> None:
    """Проверить настоящие HTML-формы без вложения в форму списка Django."""
    admin = make_user(
        email="browser-distributor@test.local", is_staff=True, is_superuser=True
    )
    first_user = make_user(email="browser-original@test.local")
    second_user = make_user(email="browser-other@test.local")
    first = Coach.objects.create(
        user=first_user, name="Иван", slug="browser-original", city="Москва"
    )
    second = Coach.objects.create(
        user=second_user, name="Мария", slug="browser-other", city="Москва"
    )
    training = Training.objects.create(
        coach=first,
        title="Тренировка Ивана",
        slug="browser-training",
        city="Москва",
        description="Описание",
    )
    enrollment = TrainingEnrollment.objects.create(
        training=training, full_name="Пётр Игрок"
    )
    client.force_login(admin)
    list_url = reverse("admin:training_trainingenrollment_changelist")
    change_url = reverse(
        "admin:training_trainingenrollment_change", args=[enrollment.pk]
    )
    distribute_url = reverse(
        "admin:training_trainingenrollment_distribute", args=[enrollment.pk]
    )
    documents = {
        list_url: client.get(list_url, secure=True).content,
        change_url: client.get(change_url, secure=True).content,
        distribute_url: client.get(distribute_url, secure=True).content,
    }
    posts: list[dict[str, list[str]]] = []

    def serve(route: Route) -> None:
        """Отдать HTML Django и перехватить POST без ORM внутри цикла Playwright."""
        url = urlsplit(route.request.url)
        if url.netloc != "testserver":
            route.abort()
            return
        if url.path.startswith("/static/"):
            found = finders.find(url.path.removeprefix("/static/"))
            if isinstance(found, str):
                route.fulfill(
                    body=Path(found).read_bytes(),
                    content_type=guess_type(found)[0] or "application/octet-stream",
                )
            else:
                route.fulfill(status=404)
            return
        if route.request.method == "POST":
            body = parse_qs(route.request.post_data or "")
            posts.append(body)
            route.fulfill(
                body="<html lang='ru'><body>Запрос принят</body></html>",
                content_type="text/html; charset=utf-8",
            )
        elif url.path in documents:
            route.fulfill(
                body=documents[url.path], content_type="text/html; charset=utf-8"
            )
        else:
            route.fulfill(status=204)

    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ["CHAT_BROWSER_EXECUTABLE"], headless=True
        )
        page = browser.new_page(
            viewport={"width": width, "height": 900}, java_script_enabled=False
        )
        page.route("**/*", serve)
        try:
            page.goto(
                "https://testserver" + (list_url if surface == "list" else change_url)
            )
            expect(
                page.get_by_role("button", name="Отдать Иван", exact=True)
            ).to_be_visible()
            if action == "other":
                page.get_by_role(
                    "link", name="Отдать другому тренеру", exact=True
                ).click()
                page.get_by_role("button", name="Отдать Мария", exact=True).click()
            else:
                label = "Отдать Иван" if action == "original" else "Забрать себе"
                page.get_by_role("button", name=label, exact=True).click()
            expect(page.get_by_text("Запрос принят", exact=True)).to_be_visible()
            assert len(posts) == 1
            assert posts[0]["action"] == [action]
            assert "csrfmiddlewaretoken" in posts[0]
        finally:
            browser.close()

    # ORM вызывается после завершения async-цикла браузера.
    response = client.post(
        distribute_url,
        {key: values[0] for key, values in posts[0].items()},
        secure=True,
    )
    assert response.status_code == 302
    enrollment.refresh_from_db()
    expected = (
        first
        if action == "original"
        else second if action == "other" else Coach.objects.get(user=admin)
    )
    assert enrollment.assigned_coach_id == expected.pk
    assert enrollment.requested_coach_id == first.pk


@pytest.mark.parametrize("width", [1440, 1280, 1024, 768, 390, 320])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_enrollment_list_fits_viewport_and_separates_actions(
    client: Client, width: int, theme: Literal["light", "dark"]
) -> None:
    """Длинные поля не растягивают список, кнопки не пересекаются даже без JavaScript."""
    admin = make_user(email="layout-admin@test.local", is_staff=True, is_superuser=True)
    coach = Coach.objects.create(
        name="Алексей Кочедыков с очень длинным именем тренера",
        slug="layout-coach",
        city="Москва",
    )
    training = Training.objects.create(
        coach=coach,
        title="Индивидуальные тренировки по теннису для начинающих и опытных игроков",
        slug="layout-training",
        city="Москва",
        description="Описание",
    )
    enrollment = TrainingEnrollment.objects.create(
        training=training,
        full_name="Александр Шатайло с очень длинным именем игрока",
        email="very-long-unbreakable-email-address-for-the-layout-check@example.test",
        telegram="@very_long_telegram_contact",
    )
    TrainingEnrollment.objects.create(
        training=training,
        full_name="Уже распределённый игрок",
        assigned_coach=coach,
        assigned_at=timezone.now(),
    )
    client.force_login(admin)
    list_url = reverse("admin:training_trainingenrollment_changelist")
    document = client.get(list_url, secure=True).content

    def serve(route: Route) -> None:
        """Отдать сохранённый ответ Django и реальные стили админки."""
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
        elif url.path == list_url:
            route.fulfill(body=document, content_type="text/html; charset=utf-8")
        else:
            route.fulfill(status=204)

    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ["CHAT_BROWSER_EXECUTABLE"], headless=True
        )
        page = browser.new_page(
            viewport={"width": width, "height": 1000},
            java_script_enabled=theme == "dark",
            color_scheme=theme,
        )
        page.route("**/*", serve)
        try:
            page.goto("https://testserver" + list_url)
            pending = page.locator(".enrollment-badge--pending")
            assigned = page.locator(".enrollment-badge:not(.enrollment-badge--pending)")
            expect(pending).to_have_css("background-color", "rgb(185, 28, 28)")
            expect(assigned).to_have_css("background-color", "rgb(21, 128, 61)")
            for badge in (pending, assigned):
                expect(badge).to_have_css("color", "rgb(255, 255, 255)")
            if directory := environ.get("ENROLLMENT_SCREENSHOTS"):
                folder = Path(directory)
                folder.mkdir(parents=True, exist_ok=True)
                page.screenshot(
                    path=str(folder / f"enrollments-{width}-{theme}.png"),
                    full_page=True,
                )
            assert page.evaluate(
                "document.documentElement.scrollWidth <= window.innerWidth"
            )
            results = page.locator("#changelist .results")
            assert results.evaluate(
                "element => element.scrollWidth <= element.clientWidth"
            )
            actions = page.locator(".enrollment-actions")
            expect(actions).to_have_count(1)
            buttons = actions.locator("button, a")
            expect(buttons).to_have_count(3)
            boxes = []
            for button in buttons.all():
                expect(button).to_be_visible()
                box = button.bounding_box()
                assert box is not None
                assert box["x"] >= 0
                assert box["x"] + box["width"] <= width
                if width <= 768:
                    assert box["height"] >= 44
                boxes.append(box)
            for first, second in zip(boxes, boxes[1:], strict=False):
                assert first["y"] + first["height"] + 6 <= second["y"]
            original = buttons.first
            assert (
                original.evaluate("element => element.form.id")
                == f"enrollment-distribute-{enrollment.pk}"
            )
            assert (
                original.evaluate(
                    "element => element.form.parentElement.closest('form')"
                )
                is None
            )
            if width <= 768:
                assert (
                    page.locator("#result_list tbody tr").first.evaluate(
                        "element => getComputedStyle(element).display"
                    )
                    == "grid"
                )
            expect(
                page.get_by_text("Уже распределённый игрок", exact=True)
            ).to_be_visible()
            page.locator(".enrollment-filters > summary").click()
            expect(page.locator("#changelist-filter")).to_be_visible()
            assert page.evaluate(
                "document.documentElement.scrollWidth <= window.innerWidth"
            )
            assert results.evaluate(
                "element => element.scrollWidth <= element.clientWidth"
            )
        finally:
            browser.close()
