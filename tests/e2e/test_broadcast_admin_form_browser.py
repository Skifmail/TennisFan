"""Проверка реальной формы Django Admin и сохранения выбранного действия."""

import uuid
from os import environ
from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.staticfiles import finders
from django.test import Client, override_settings
from django.urls import reverse
from playwright.sync_api import Route, expect, sync_playwright

from apps.notifications.models import Broadcast
from tests.integration.test_broadcast_admin_flow import _admin
from tests.integration.test_notification_center import (
    notification_settings as notification_settings,
)

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        environ.get("WEB_PUSH_BROWSER_TESTS") != "1", reason="Нужен Chromium"
    ),
]


def test_broadcast_form_when_direct_test_clicked_should_preserve_action(
    client: Client,
) -> None:
    """Реальная форма сохраняет выбранную кнопку при защите от двойного клика.

    Args:
        client: Изолированная авторизованная сессия администратора.
    """
    _admin(client)
    with override_settings(
        STORAGES={
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {
                "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
            },
        }
    ):
        response = client.get(reverse("admin:notifications_broadcast_add"))
        campaign = Broadcast.objects.create(
            title="Личный тест",
            body="Проверка интерфейса без отправки",
            is_test=True,
            status="expanded",
            submission_id=uuid.uuid4(),
            processed_users=1,
            inbox_created=1,
        )
        progress_response = client.get(
            reverse("admin:notifications_broadcast_change", args=[campaign.pk])
        )

    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ.get("CHAT_BROWSER_EXECUTABLE"), headless=True
        )
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            submitted: list[dict[str, list[str]]] = []

            def serve(route: Route) -> None:
                """Отдаёт локальные файлы, не обращаясь к рабочему сайту.

                Args:
                    route: Изолированный запрос браузерного стенда.
                """
                path = urlsplit(route.request.url).path
                if route.request.method == "POST":
                    submitted.append(parse_qs(route.request.post_data or ""))
                    route.fulfill(content_type="text/html", body="<p>Saved</p>")
                elif path.startswith("/static/"):
                    found = finders.find(path.removeprefix("/static/"))
                    if isinstance(found, str):
                        route.fulfill(path=found)
                    else:
                        route.fulfill(status=404)
                else:
                    route.fulfill(
                        content_type="text/html; charset=utf-8", body=response.content
                    )

            page.route("**/*", serve)
            page.goto("https://tennisfan.ru/admin/notifications/broadcast/add/")
            expect(
                page.get_by_role("button", name="Отправить тест себе", exact=True)
            ).to_be_visible()
            page.locator("#id_title").fill("Новость TennisFan")
            page.locator("#id_body").fill("Тест формы без настоящей отправки.")
            page.screenshot(path="/tmp/broadcast-admin-add-preview.png", full_page=True)
            page.get_by_role("button", name="Отправить тест себе", exact=True).click()
            expect(page.get_by_text("Saved", exact=True)).to_be_visible()
            assert len(submitted) == 1
            assert submitted[0]["_test_now"] == ["1"]
            assert submitted[0]["_broadcast_submission"]
            response = progress_response
            page.goto(
                "https://tennisfan.ru/admin/notifications/broadcast/"
                + str(campaign.pk)
                + "/change/"
            )
            expect(page.locator("[data-progress-stage]")).to_have_text("Завершена")
            expect(page.locator("[data-progress-inbox]")).to_have_text("1")
            page.screenshot(
                path="/tmp/broadcast-admin-progress-preview.png", full_page=True
            )

        finally:
            browser.close()
