"""Браузер: контакты заявки доступны без Telegram, JavaScript и буфера обмена."""

from __future__ import annotations

from mimetypes import guess_type
from os import environ
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

import pytest
from django.contrib.staticfiles import finders
from django.test import Client
from django.urls import reverse
from playwright.sync_api import Route, expect, sync_playwright

from apps.courts.models import Court
from apps.training.models import Coach, Training, TrainingEnrollment
from tests.support.factories import make_user

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        not (environ.get("CHAT_BROWSER_EXECUTABLE") or environ.get("CHAT_E2E_URL")),
        reason="Браузер запускается на отдельном этапе CI",
    ),
]


@pytest.fixture
def enrollment() -> TrainingEnrollment:
    """Создаёт заявку со всеми контактами в изолированной тестовой базе."""
    user = make_user(email="training-detail-browser@test.local")
    coach = Coach.objects.create(
        user=user,
        name="Иван Тренер",
        slug="training-detail-browser-coach",
        city="Раменское",
    )
    training = Training.objects.create(
        coach=coach,
        title="Индивидуальная тренировка",
        slug="training-detail-browser-training",
        description="Тренировка по теннису",
        city="Раменское",
    )
    court = Court.objects.create(
        name="Теннисный клуб Радуга",
        slug="training-detail-browser-court",
        address="ул. Тестовая, 10",
        city="Раменское",
        surface="хард",
    )
    return TrainingEnrollment.objects.create(
        training=training,
        full_name="Юлия Соколова",
        telegram="example_player",
        whatsapp="8 (999) 123-45-67",
        email="client@example.test",
        desired_court=court,
        message="Хочу заниматься по средам.\nМожно после работы?",
    )


@pytest.mark.parametrize("mode", ["copy", "denied", "no-clipboard", "no-js", "long"])
def test_contacts_stay_available_when_telegram_or_copying_is_blocked(
    client: Client, enrollment: TrainingEnrollment, mode: str
) -> None:
    """Проверяет переход из кабинета, копирование, отказ API и узкие экраны.

    Args:
        client: Клиент изолированной тестовой базы.
        enrollment: Заявка с контактами и пожеланиями.
        mode: Успешный буфер обмена, его отказ, отключённый JS или длинные данные.
    """
    if mode == "long":
        enrollment.full_name = "ОченьДлинноеИмяБезПробелов" * 8
        enrollment.telegram = "long_" + "username" * 11
        enrollment.email = "long.email." * 18 + "@example.test"
        enrollment.message = "ДлинныеПожеланияБезПробелов" * 40
        enrollment.save()

    coach = enrollment.training.coach
    assert coach is not None and coach.user is not None
    client.force_login(coach.user)
    detail_url = reverse("enrollment_detail", args=[enrollment.pk])
    account_url = reverse("my_trainings")
    account = client.get(account_url, secure=True)
    detail = client.get(detail_url, secure=True)
    assert account.status_code == detail.status_code == 200
    updated_at = enrollment.updated_at

    def serve(route: Route) -> None:
        """Отдаёт настоящий HTML и статику Django, не обращаясь к внешним сайтам."""
        url = urlsplit(route.request.url)
        if url.netloc == "testserver" and url.path.startswith("/static/"):
            found = finders.find(url.path.removeprefix("/static/"))
            if isinstance(found, str):
                route.fulfill(
                    body=Path(found).read_bytes(),
                    content_type=guess_type(found)[0] or "application/octet-stream",
                )
            else:
                route.fulfill(status=404)
        elif url.netloc == "testserver" and url.path in {detail_url, account_url}:
            document = detail if url.path == detail_url else account
            route.fulfill(
                body=document.content, content_type="text/html; charset=utf-8"
            )
        elif url.netloc == "testserver":
            route.fulfill(status=204)
        else:
            route.abort()

    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ.get("CHAT_BROWSER_EXECUTABLE"), headless=True
        )
        context = browser.new_context(
            viewport={"width": 375, "height": 900},
            reduced_motion="reduce",
            java_script_enabled=mode != "no-js",
        )
        context.route("**/*", serve)
        if mode in {"copy", "long"}:
            context.add_init_script(
                """Object.defineProperty(navigator, 'clipboard', {value: {
                    writeText: async value => { window.copiedContact = value; }
                }});"""
            )
        elif mode == "denied":
            context.add_init_script(
                """Object.defineProperty(navigator, 'clipboard', {value: {
                    writeText: async () => { throw new DOMException('Denied', 'NotAllowedError'); }
                }});"""
            )
        elif mode == "no-clipboard":
            context.add_init_script(
                "Object.defineProperty(navigator, 'clipboard', {value: undefined});"
            )
        context.add_cookies(
            [
                {
                    "name": "cookie_consent",
                    "value": "false",
                    "domain": "testserver",
                    "path": "/",
                }
            ]
        )
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto("https://testserver" + account_url)
            page.locator(".my-trainings-enrollment").get_by_role(
                "link", name="Открыть заявку", exact=True
            ).click()
            page.wait_for_url("https://testserver" + detail_url)
            main = page.locator(".enrollment-detail")
            expect(main.locator("#enrollment-telegram")).to_have_text(
                enrollment.telegram
            )
            expect(main.locator("#enrollment-phone")).to_have_text(enrollment.whatsapp)
            expect(main.locator("#enrollment-email")).to_have_text(enrollment.email)
            expect(main).to_contain_text("Теннисный клуб Радуга")
            page.evaluate("document.fonts.ready")

            if mode == "no-js":
                expect(main.get_by_role("button")).to_have_count(0)
            else:
                for label, value in (
                    ("Telegram", enrollment.telegram),
                    ("телефон", enrollment.whatsapp),
                    ("email", enrollment.email),
                ):
                    copy = main.get_by_role("button", name=f"Скопировать {label}")
                    copy.focus()
                    expect(copy).to_be_focused()
                    assert copy.evaluate(
                        "element => getComputedStyle(element).outlineStyle === 'solid'"
                    )
                    copy.click()
                    if mode in {"copy", "long"}:
                        expect(main.get_by_role("status")).to_have_text(
                            "Контакт скопирован."
                        )
                        assert page.evaluate("window.copiedContact") == value
                    else:
                        expect(main.get_by_role("status")).to_contain_text(
                            "скопируйте его вручную"
                        )
                        assert (
                            page.evaluate("window.getSelection().toString()") == value
                        )

            telegram_url = enrollment.telegram_url
            assert telegram_url is not None
            with (
                page.expect_popup() as opened,
                context.expect_event(
                    "request", predicate=lambda request: request.url == telegram_url
                ),
            ):
                main.get_by_role("link", name="Открыть Telegram", exact=True).click()
            opened.value.close()
            expect(main.locator("#enrollment-telegram")).to_be_visible()
            expect(page.locator(".page-transition-spinner.is-visible")).to_have_count(0)

            layout_errors: list[str] = []
            for width, height in (
                (1440, 900),
                (900, 900),
                (375, 900),
                (320, 900),
                (844, 390),
            ):
                page.set_viewport_size({"width": width, "height": height})
                page.evaluate("document.fonts.ready")
                layout_errors.extend(
                    cast(
                        list[str],
                        page.evaluate(
                            """() => {
                                const problems = [];
                                if (document.documentElement.scrollWidth > innerWidth) {
                                    problems.push(`${innerWidth}px: страница шире экрана`);
                                }
                                for (const element of document.querySelectorAll('.enrollment-detail .card, .enrollment-detail .card-body, .enrollment-detail dl, .enrollment-detail dd, .enrollment-detail__contact, .enrollment-detail__contact-actions, .enrollment-detail__contact-value, .enrollment-detail .btn:not([hidden])')) {
                                    if (element.scrollWidth > element.clientWidth + 1) {
                                        const style = getComputedStyle(element);
                                        problems.push(`${innerWidth}px: текст обрезан в ${element.tagName}.${element.className} (${element.scrollWidth}/${element.clientWidth}px, ${style.whiteSpace}, ${style.overflowWrap})`);
                                    }
                                }
                                for (const element of document.querySelectorAll('.enrollment-detail__contact-actions .btn:not([hidden])')) {
                                    if (element.getBoundingClientRect().height < 44 - 0.01) {
                                        problems.push('Кнопка контакта ниже 44px');
                                    }
                                }
                                return problems;
                            }"""
                        ),
                    )
                )
                if mode in {"copy", "no-js", "long"} and width in {375, 1440}:
                    main.screenshot(
                        path=f"/tmp/opencode/training-enrollment-detail-{mode}-{width}.png"
                    )
            assert not layout_errors, "\n".join(layout_errors)
            assert not errors, errors
        finally:
            browser.close()

    enrollment.refresh_from_db()
    assert enrollment.status == TrainingEnrollment.Status.PENDING
    assert enrollment.updated_at == updated_at
