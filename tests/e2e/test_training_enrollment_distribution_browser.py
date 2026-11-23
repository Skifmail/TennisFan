"""Браузер: все три кнопки админки отправляют отдельные формы распределения."""

from __future__ import annotations

from mimetypes import guess_type
from os import environ
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.staticfiles import finders
from django.test import Client
from django.urls import reverse
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


@pytest.mark.parametrize("action", ["original", "self", "other"])
def test_admin_distribution_buttons_submit_the_correct_post(
    client: Client, action: str
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
    distribute_url = reverse(
        "admin:training_trainingenrollment_distribute", args=[enrollment.pk]
    )
    documents = {
        list_url: client.get(list_url, secure=True).content,
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
            viewport={"width": 1280, "height": 900}, java_script_enabled=False
        )
        page.route("**/*", serve)
        try:
            page.goto("https://testserver" + list_url)
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
