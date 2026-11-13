"""Браузерная проверка контактов автора в карточке до и после собственного отклика."""

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

from apps.sparring.models import SparringRequest, SparringResponse
from apps.users.models import Player
from tests.support.factories import make_player
from tests.support.sparring_author_contacts import make_author_contacts_scenario

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        not (environ.get("CHAT_BROWSER_EXECUTABLE") or environ.get("CHAT_E2E_URL")),
        reason="Браузер запускается на отдельном этапе CI",
    ),
]


def _serve_static(route: Route) -> None:
    """Отдать настоящие CSS, JavaScript и изображения без сети или production."""
    asset = urlsplit(route.request.url).path.removeprefix("/static/")
    found = finders.find(asset)
    if isinstance(found, str):
        route.fulfill(
            body=Path(found).read_bytes(),
            content_type=guess_type(found)[0] or "application/octet-stream",
        )
    else:
        route.fulfill(status=404)


@pytest.mark.parametrize("worst_case", [False, True], ids=["normal", "long-values"])
def test_feed_reply_reveals_author_contacts_inline_and_preserves_them_on_reload(
    client: Client, worst_case: bool
) -> None:
    """Карточка обновляется без модалки, контакты компактны, а чат остаётся отдельным.

    Args:
        client: Клиент изолированной тестовой базы.
        worst_case: Проверить длинные имена и неразрывный текст заявки.
    """
    scenario = make_author_contacts_scenario(
        suffix="author-contacts-browser", worst_case=worst_case
    )
    client.force_login(scenario.respondent.user)
    other = make_player(email_suffix="author-contacts-browser-other")
    SparringResponse.objects.create(
        sparring_request=scenario.sparring, respondent=other, contact_method="telegram"
    )
    phone_author = make_player(
        email_suffix="author-contacts-browser-phone",
        first_name="Мария",
        phone="+79990000003",
    )
    empty_author = make_player(
        email_suffix="author-contacts-browser-empty", first_name="Анна"
    )
    max_author = make_player(
        email_suffix="author-contacts-browser-max", first_name="Павел"
    )
    Player.objects.filter(pk=max_author.pk).update(max_contact="+79990000004")
    for author, status in (
        (phone_author, "pending"),
        (empty_author, "accepted"),
        (max_author, "pending"),
    ):
        request = SparringRequest.objects.create(
            player=author, city="Москва", description="Игра в выходные"
        )
        SparringResponse.objects.create(
            sparring_request=request,
            respondent=scenario.respondent,
            contact_method="telegram",
            status=status,
        )
    feed_url = reverse("sparring_list")
    reply_url = reverse("sparring_respond", args=[scenario.sparring.pk])
    initial = client.get(feed_url, secure=True)
    assert initial.status_code == 200
    csrf = client.cookies["csrftoken"].value
    submitted = client.post(
        reply_url, "{}", content_type="application/json", secure=True
    )
    assert submitted.status_code == 200
    reloaded = client.get(feed_url, secure=True)
    direct_url = reverse("chat:direct_start", args=[scenario.author.pk])
    started_chat = client.post(direct_url, secure=True)
    assert started_chat.status_code == 302
    chat_url = str(started_chat["Location"])
    replied = False
    reply_count = 0

    def serve(route: Route) -> None:
        """Подменить только транспорт: HTML и ответ на отклик уже получены из Django."""
        nonlocal replied, reply_count
        url = urlsplit(route.request.url)
        if url.netloc == "testserver" and url.path.startswith("/static/"):
            _serve_static(route)
        elif url.netloc == "testserver" and url.path == feed_url:
            route.fulfill(
                body=reloaded.content if replied else initial.content,
                content_type="text/html; charset=utf-8",
            )
        elif url.netloc == "testserver" and url.path == reply_url:
            assert route.request.method == "POST"
            assert route.request.headers.get("x-csrftoken") == csrf
            reply_count += 1
            replied = True
            route.fulfill(body=submitted.content, content_type="application/json")
        elif url.netloc == "testserver" and url.path == direct_url:
            route.fulfill(status=302, headers={"Location": chat_url})
        elif url.netloc == "testserver" and url.path == urlsplit(chat_url).path:
            route.fulfill(
                body="<!doctype html><title>Личный чат</title><h1>Личный чат</h1>",
                content_type="text/html; charset=utf-8",
            )
        elif url.netloc in {"t.me", "wa.me", "max.ru"}:
            route.fulfill(
                body="<!doctype html><title>Контакт автора</title>",
                content_type="text/html",
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
            viewport={"width": 390, "height": 1000}, reduced_motion="reduce"
        )
        context.route("**/*", serve)
        context.add_cookies(
            [
                {
                    "name": "csrftoken",
                    "value": csrf,
                    "domain": "testserver",
                    "path": "/",
                    "secure": True,
                }
            ]
        )
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto("https://testserver" + feed_url)
            page.evaluate("document.fonts.ready")
            page.get_by_role("button", name="Отказаться от cookie", exact=True).click()
            card = page.locator(f"#sparring-{scenario.sparring.pk}")
            expect(card.locator(".sparring-contact")).to_have_count(0)
            expect(card.locator(".sparring-card__contact-status")).to_have_text(
                "Чтобы увидеть контакты автора, откликнитесь на спарринг."
            )
            expect(card).to_contain_text("Есть отклики")
            card.screenshot(
                path=f"/tmp/opencode/sparring-author-contacts-locked-{'long' if worst_case else 'normal'}-390.png"
            )
            respond = card.locator(".sparring-respond-btn")
            respond.click()
            expect(card.locator(".sparring-contact")).to_have_count(4)
            expect(respond).to_be_disabled()
            expect(respond).to_have_text("Отклик отправлен")
            expect(card.locator(".sparring-card__contact-status")).to_contain_text(
                "Отклик успешно отправлен"
            )
            expect(card.locator(".sparring-contact__label")).to_have_text(
                ["Telegram", "WhatsApp", "MAX", "Позвонить"]
            )
            expect(card.locator(".sparring-contact__value")).to_have_text(
                ["8 (999) 123-45-67"]
            )
            assert "@author_player" not in card.inner_text()
            expect(page.locator("#sparring-respond-modal")).to_have_count(0)
            assert reply_count == 1

            layout_errors: list[str] = []
            for width in (1440, 1024, 900, 640, 480, 390, 375, 320):
                page.set_viewport_size({"width": width, "height": 1000})
                card.screenshot(
                    path=f"/tmp/opencode/sparring-author-contacts-{'long' if worst_case else 'normal'}-{width}.png"
                )
                layout_errors.extend(
                    cast(
                        list[str],
                        page.evaluate(
                            """() => {
                            const problems = [];
                            const note = text => problems.push(`${innerWidth}px: ${text}`);
                            if (document.documentElement.scrollWidth > innerWidth) note('страница шире экрана');
                            for (const element of document.querySelectorAll('#sparring-feed .sparring-card, .sparring-card__author-contacts, .sparring-card__contact-status, .sparring-contact, .sparring-card__headline .player-name')) {
                                if (element.scrollWidth > element.clientWidth + 1) note(`${element.className}: содержимое обрезано`);
                            }
                            for (const contacts of document.querySelectorAll('.sparring-card__author-contacts .sparring-response__contacts')) {
                                if (getComputedStyle(contacts).gridTemplateColumns.split(' ').length !== 2) note('контакты должны идти по два в ряд');
                                const buttons = Array.from(contacts.querySelectorAll('.sparring-contact'));
                                for (let index = 0; index < buttons.length; index += 2) {
                                    const first = buttons[index].getBoundingClientRect();
                                    const second = buttons[index + 1]?.getBoundingClientRect();
                                    // DOMRect при translateY может округлить 44px до 43.99994px.
                                    if (first.height < 44 - 0.01) note('контакт неудобно нажимать');
                                    if (second && Math.abs(first.top - second.top) > 1) note('контакты не помещаются в ряд');
                                    if (second && Math.abs(first.width - second.width) > 1) note('разная ширина контактов');
                                    if (second && Math.abs(first.height - second.height) > 1) note('разная высота контактов');
                                }
                            }
                            for (const actions of document.querySelectorAll('.sparring-card--single .sparring-card__actions')) {
                                const rows = new Map();
                                for (const button of actions.querySelectorAll('.btn')) {
                                    const box = button.getBoundingClientRect();
                                    if (box.height < 44 - 0.01) note(`кнопку действия неудобно нажимать (${button.textContent.trim()}: высота ${box.height}px)`);
                                    if (button.scrollWidth > button.clientWidth + 1) note('подпись действия обрезана');
                                    const key = Math.round(box.top);
                                    const row = rows.get(key) || [];
                                    row.push(box);
                                    rows.set(key, row);
                                }
                                for (const row of rows.values()) {
                                    if (Math.max(...row.map(box => box.width)) - Math.min(...row.map(box => box.width)) > 1) note('разная ширина действий в ряду');
                                    if (Math.max(...row.map(box => box.height)) - Math.min(...row.map(box => box.height)) > 1) note('разная высота действий в ряду');
                                }
                            }
                            return problems;
                        }"""
                        ),
                    )
                )
            assert not layout_errors, "\n".join(layout_errors)
            page.set_viewport_size({"width": 390, "height": 1000})
            for author, label in (
                (phone_author, "phone-only"),
                (empty_author, "no-contacts"),
                (max_author, "disabled-max"),
            ):
                page.locator(".sparring-card").filter(
                    has=page.locator(
                        f'.player-name[href="{reverse("profile", args=[author.pk])}"]'
                    )
                ).screenshot(
                    path=f"/tmp/opencode/sparring-author-contacts-{label}-390.png"
                )

            for url in (
                "https://t.me/author_player",
                "https://wa.me/79999876543",
                "https://max.ru/u/author_player",
            ):
                with page.expect_popup() as opened:
                    card.locator(f'.sparring-contact[href="{url}"]').click()
                popup = opened.value
                popup.wait_for_url(url)
                popup.close()
            page.bring_to_front()
            page.reload()
            expect(card.locator(".sparring-contact")).to_have_count(4)
            expect(respond).to_be_disabled()
            expect(card.locator(".sparring-card__contact-status")).to_have_text(
                "Вы откликнулись на спарринг."
            )
            assert reply_count == 1
            contact = card.locator(".sparring-contact").first
            contact.focus()
            expect(contact).to_be_focused()
            assert contact.evaluate(
                "element => getComputedStyle(element).outlineStyle === 'solid'"
            )
            assert not errors, errors
            with page.expect_request("https://testserver" + direct_url) as opened_chat:
                card.get_by_role(
                    "button", name="Написать в чат игроку", exact=False
                ).click()
            assert opened_chat.value.method == "POST"
            assert "csrfmiddlewaretoken=" in (opened_chat.value.post_data or "")
            page.wait_for_url("https://testserver" + chat_url)
        finally:
            browser.close()


@pytest.mark.parametrize("failure", ["denied", "network", "not-json"])
def test_failed_reply_keeps_contacts_private_and_restores_the_reply_button(
    client: Client, failure: str
) -> None:
    """Ошибка сервера или сети не открывает контакты и позволяет повторить отклик."""
    scenario = make_author_contacts_scenario(suffix="author-contacts-error")
    client.force_login(scenario.respondent.user)
    feed_url = reverse("sparring_list")
    reply_url = reverse("sparring_respond", args=[scenario.sparring.pk])
    document = client.get(feed_url, secure=True)
    scenario.respondent.user.subscription.delete()
    rejected = client.post(
        reply_url, "{}", content_type="application/json", secure=True
    )
    assert rejected.status_code == 403

    def serve(route: Route) -> None:
        """Отдать реальную страницу и воспроизвести один вид неуспешного транспорта."""
        url = urlsplit(route.request.url)
        if url.netloc == "testserver" and url.path.startswith("/static/"):
            _serve_static(route)
        elif url.netloc == "testserver" and url.path == feed_url:
            route.fulfill(
                body=document.content, content_type="text/html; charset=utf-8"
            )
        elif url.netloc == "testserver" and url.path == reply_url:
            if failure == "network":
                route.abort()
            elif failure == "not-json":
                route.fulfill(
                    status=500, body="Ошибка сервера", content_type="text/html"
                )
            else:
                route.fulfill(
                    status=403, body=rejected.content, content_type="application/json"
                )
        elif url.netloc == "testserver":
            route.fulfill(status=204)
        else:
            route.abort()

    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ.get("CHAT_BROWSER_EXECUTABLE"), headless=True
        )
        page = browser.new_page(
            viewport={"width": 390, "height": 1000}, reduced_motion="reduce"
        )
        page.route("**/*", serve)
        try:
            page.goto("https://testserver" + feed_url)
            page.get_by_role("button", name="Отказаться от cookie", exact=True).click()
            card = page.locator(f"#sparring-{scenario.sparring.pk}")
            respond = card.locator(".sparring-respond-btn")
            respond.click()
            expect(card.locator(".sparring-card__contact-status")).not_to_have_text(
                "Чтобы увидеть контакты автора, откликнитесь на спарринг."
            )
            expect(respond).to_be_enabled()
            expect(respond).to_have_text("Откликнуться")
            expect(card.locator(".sparring-contact")).to_have_count(0)
            assert "author_player" not in page.content()
        finally:
            browser.close()
