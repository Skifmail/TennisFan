"""Браузерная проверка карточек откликов и прямых ссылок без внешней сети."""

from datetime import date
from mimetypes import guess_type
from os import environ
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

import pytest
from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse
from playwright.sync_api import Dialog, Route, expect, sync_playwright

from apps.sparring.models import SparringContact, SparringRequest, SparringResponse
from apps.tournaments.models import Match
from apps.users.models import Player
from tests.support.factories import make_player, make_subscription

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        not (environ.get("CHAT_BROWSER_EXECUTABLE") or environ.get("CHAT_E2E_URL")),
        reason="Браузер запускается на отдельном этапе CI",
    ),
]


def _serve_static(route: Route) -> None:
    """Отдать настоящую статику проекта, не обращаясь к серверу или CDN."""
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
def test_response_cards_when_viewport_changes_should_align_actions_and_keep_contacts_visible(
    client: Client,
    worst_case: bool,
) -> None:
    """Статусы, контакты и кнопки читаемы на компьютере и узком экране.

    Args:
        client: Клиент изолированной тестовой базы.
        worst_case: Проверить длинные имена, адреса и неразрывные строки.
    """
    author = make_player(
        email_suffix="cards-author", first_name="Иван", last_name="Автор"
    )
    Player.objects.filter(pk=author.pk).update(
        city="Москва", birth_date=date(1990, 1, 1), is_verified=True
    )
    make_subscription(
        author.user,
        tier_name="cards-author",
        tier_kwargs={"has_sparring": True},
    )
    respondent = make_player(
        email_suffix="cards-respondent",
        first_name="Александр" * 8 if worst_case else "Леонид",
        last_name="ОченьДлиннаяФамилия" * 6 if worst_case else "Ермолаев",
        phone="8 (999) 123-45-67",
    )
    respondent.telegram = "card_player"
    respondent.whatsapp = "8 (999) 123-45-67"
    respondent.max_contact = (
        "https://max.ru/u/" + "long-contact-" * 12
        if worst_case
        else "https://max.ru/u/card_player"
    )
    Player.objects.filter(pk=respondent.pk).update(
        telegram=respondent.telegram,
        whatsapp=respondent.whatsapp,
        max_contact=respondent.max_contact,
    )
    request = SparringRequest.objects.create(
        player=author,
        city="Москва",
        description=(
            "Спарринг" * 40
            if worst_case
            else "Ищу партнёра на вечерний спарринг, чтобы отработать подачу."
        ),
        preferred_days="Понедельник и четверг",
        preferred_time="19:00–21:00",
        preferred_location=(
            "НазваниеКортонаБезПробелов" * 6 if worst_case else "Корт Орион"
        ),
        desired_category="amateur",
        desired_partner_age_min=20,
        desired_partner_age_max=40,
    )
    SparringResponse.objects.create(
        sparring_request=request, respondent=respondent, contact_method="telegram"
    )
    phone_player = make_player(
        email_suffix="cards-phone", first_name="Мария", phone="+79990000002"
    )
    max_player = make_player(email_suffix="cards-max", first_name="Павел")
    max_player.max_contact = "+79990000003"
    Player.objects.filter(pk=max_player.pk).update(max_contact=max_player.max_contact)
    accepted_player = make_player(email_suffix="cards-accepted", first_name="Анна")
    for player, method, status in (
        (phone_player, "telegram", "pending"),
        (max_player, "max", "pending"),
        (accepted_player, "telegram", "accepted"),
    ):
        other_request = SparringRequest.objects.create(
            player=author,
            city="Москва",
            description="Дружеская игра в выходные",
            status="closed" if status == "accepted" else "active",
        )
        response = SparringResponse.objects.create(
            sparring_request=other_request,
            respondent=player,
            contact_method=method,
            status=status,
        )
        if status == "accepted":
            match = Match.objects.create(
                tournament=None,
                match_type=Match.MatchType.SPARRING,
                sparring_response=response,
                player1=author,
                player2=player,
            )
            rejected = make_player(email_suffix="cards-rejected", first_name="Денис")
            rejected.telegram = "hidden_contact"
            Player.objects.filter(pk=rejected.pk).update(telegram=rejected.telegram)
            SparringResponse.objects.create(
                sparring_request=other_request,
                respondent=rejected,
                contact_method="telegram",
                status="rejected",
            )
    client.force_login(author.user)
    document = client.get(reverse("sparring_my_requests"), secure=True)
    assert document.status_code == 200
    direct_url = reverse("chat:direct_start", args=[respondent.pk])
    started_chat = client.post(direct_url, secure=True)
    assert started_chat.status_code == 302
    chat_url = str(started_chat["Location"])

    def serve(route: Route) -> None:
        """Подменить только транспорт страницы, сохранив её реальные HTML, CSS и JS."""
        url = urlsplit(route.request.url)
        if url.netloc == "testserver" and url.path.startswith("/static/"):
            _serve_static(route)
        elif url.netloc == "testserver" and url.path == reverse("sparring_my_requests"):
            route.fulfill(
                body=document.content, content_type="text/html; charset=utf-8"
            )
        elif url.netloc == "testserver" and url.path == direct_url:
            route.fulfill(status=302, headers={"Location": chat_url})
        elif url.netloc == "testserver" and url.path == urlsplit(chat_url).path:
            route.fulfill(
                body="<!doctype html><title>Личный чат</title><h1>Личный чат</h1>",
                content_type="text/html; charset=utf-8",
            )
        elif url.netloc in {"t.me", "wa.me", "max.ru"}:
            route.fulfill(
                body="<!doctype html><title>Контакт</title>", content_type="text/html"
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
            viewport={"width": 1440, "height": 1000}, reduced_motion="reduce"
        )
        context.route("**/*", serve)
        context.add_init_script(
            f"localStorage.setItem('profile-completion:{author.user_id}', String(Date.now()));"
        )
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto("https://testserver" + reverse("sparring_my_requests"))
            page.evaluate("document.fonts.ready")
            page.get_by_role("button", name="Отказаться от cookie", exact=True).click()
            main_response = page.locator(".sparring-response").filter(
                has=page.locator('.sparring-contact[href="https://t.me/card_player"]')
            )
            main_card = page.locator(".sparring-card--mine").filter(has=main_response)
            expect(main_response.locator(".sparring-response__status")).to_have_text(
                "Ожидает вашего подтверждения"
            )
            expect(
                main_response.locator(".sparring-response__actions .btn")
            ).to_have_text(["Подтвердить", "Написать в чат", "Профиль"])
            expect(main_response.locator(".sparring-contact__label")).to_have_text(
                ["Telegram", "WhatsApp", "MAX", "Позвонить"]
            )
            expect(main_response.locator(".sparring-contact__value")).to_have_text(
                ["8 (999) 123-45-67"]
            )
            expect(
                main_response.locator(".sparring-response__actions a")
            ).to_have_count(1)
            expect(page.locator("#sparring-contact-modal")).to_have_count(0)
            expect(
                page.locator('.sparring-contact[href="https://t.me/hidden_contact"]')
            ).to_have_count(0)
            expect(page.locator(".sparring-contact--text")).to_have_text(
                "MAX Нет ссылки"
            )
            expect(page.locator(".sparring-contact--text")).to_be_disabled()
            expect(page.get_by_role("link", name="Матч", exact=True)).to_have_attribute(
                "href", reverse("match_detail", args=[match.pk])
            )
            expect(
                page.locator(
                    f'form[action="{reverse("chat:direct_start", args=[phone_player.pk])}"]'
                )
            ).to_have_count(1)
            expect(
                main_response.locator(f'form[action="{direct_url}"]')
            ).to_have_attribute("method", "post")

            accepted_response = page.locator(".sparring-response").filter(
                has=page.locator(
                    f'.sparring-response__name[href="{reverse("profile", args=[accepted_player.pk])}"]'
                )
            )
            phone_response = page.locator(".sparring-response").filter(
                has=page.locator(
                    f'.sparring-response__name[href="{reverse("profile", args=[phone_player.pk])}"]'
                )
            )
            expect(
                accepted_response.locator(".sparring-response__status")
            ).to_have_text("Отклик принят")
            expect(
                accepted_response.locator(".sparring-response__no-contacts")
            ).to_contain_text("Кнопка «Написать в чат» откроет личный чат")
            expect(
                accepted_response.locator(".sparring-response__actions .btn")
            ).to_have_text(["Написать в чат", "Профиль", "Матч"])
            rejected_response = page.locator(".sparring-response--rejected")
            expect(
                rejected_response.locator(".sparring-response__status")
            ).to_have_text("Отклик отклонён")
            expect(
                rejected_response.locator(".sparring-response__actions .btn")
            ).to_have_text(["Профиль"])

            layout_errors: list[str] = []
            for width in (1440, 1024, 900, 640, 480, 390, 375, 320):
                page.set_viewport_size({"width": width, "height": 1000})
                if width in (1440, 390, 320):
                    main_card.screenshot(
                        path=f"/tmp/opencode/sparring-response-{'long' if worst_case else 'normal'}-{width}.png"
                    )
                layout_errors.extend(
                    cast(
                        list[str],
                        page.evaluate(
                            """() => {
                                const problems = [];
                                const note = text => problems.push(`${innerWidth}px: ${text}`);
                                if (document.documentElement.scrollWidth > innerWidth) {
                                    const tabs = document.querySelector('.sparring-tabs');
                                    note(`страница шире экрана: ${document.documentElement.scrollWidth}px, вкладки ${tabs.clientWidth}/${tabs.scrollWidth}px`);
                                }
                                for (const element of document.querySelectorAll('.sparring-card--mine, .sparring-response, .sparring-contact, .sparring-card__desc, .sparring-card__facts dd')) {
                                    if (element.scrollWidth > element.clientWidth + 1) note(`${element.className}: содержимое обрезано`);
                                }
                                for (const contacts of document.querySelectorAll('.sparring-response__contacts')) {
                                    if (getComputedStyle(contacts).gridTemplateColumns.split(' ').length !== 2) note('контакты должны идти по два в ряд');
                                    const buttons = Array.from(contacts.querySelectorAll('.sparring-contact'));
                                    for (let index = 0; index < buttons.length; index += 2) {
                                        const first = buttons[index].getBoundingClientRect();
                                        const second = buttons[index + 1]?.getBoundingClientRect();
                                        if (second && Math.abs(first.top - second.top) > 1) note('два контакта не помещаются в один ряд');
                                        if (second && Math.abs(first.width - second.width) > 1) note('разная ширина кнопок контактов');
                                        if (second && Math.abs(first.height - second.height) > 1) note('разная высота кнопок контактов');
                                    }
                                }
                                for (const actions of document.querySelectorAll('.sparring-response__actions, .sparring-card--mine .sparring-card__actions, .sparring-toolbar__panel--my-requests .sparring-toolbar__actions')) {
                                    const rows = new Map();
                                    for (const button of actions.querySelectorAll('.btn')) {
                                        const box = button.getBoundingClientRect();
                                        const minimum = innerWidth <= 640 ? 44 : 36;
                                        if (box.height < minimum) {
                                            const style = getComputedStyle(button);
                                            note(`${actions.className}: «${button.textContent.trim()}», высота ${box.height}px, min/max ${style.minHeight}/${style.maxHeight}`);
                                        }
                                        if (button.scrollWidth > button.clientWidth + 1) note('подпись кнопки обрезана');
                                        const key = Math.round(box.top);
                                        const row = rows.get(key) || [];
                                        row.push(box);
                                        rows.set(key, row);
                                    }
                                    for (const row of rows.values()) {
                                        if (Math.max(...row.map(box => box.height)) - Math.min(...row.map(box => box.height)) > 1) note('разная высота кнопок в ряду');
                                        if (Math.max(...row.map(box => box.width)) - Math.min(...row.map(box => box.width)) > 1) note('разная ширина кнопок в ряду');
                                    }
                                }
                                if (innerWidth <= 640) {
                                    for (const contact of document.querySelectorAll('a.sparring-contact')) {
                                        if (contact.getBoundingClientRect().height < 44) note('контакт неудобно нажимать');
                                    }
                                }
                                return problems;
                            }"""
                        ),
                    )
                )
            assert not layout_errors, "\n".join(layout_errors)

            page.set_viewport_size({"width": 390, "height": 1000})
            if not worst_case:
                accepted_response.screenshot(
                    path="/tmp/opencode/sparring-response-accepted-no-contacts-390.png"
                )
                phone_response.screenshot(
                    path="/tmp/opencode/sparring-response-phone-only-390.png"
                )
                page.locator(".sparring-response").filter(
                    has=page.locator(".sparring-contact--max:disabled")
                ).screenshot(
                    path="/tmp/opencode/sparring-response-disabled-max-390.png"
                )

            for url in (
                "https://t.me/card_player",
                "https://wa.me/79991234567",
                respondent.max_contact,
            ):
                with page.expect_popup() as opened:
                    main_response.locator(f'.sparring-contact[href="{url}"]').click()
                popup = opened.value
                popup.wait_for_url(url)
                popup.close()
            page.bring_to_front()
            assert page.evaluate("document.hasFocus()")
            tabs = page.locator(".sparring-tabs .sparring-tab")
            for width in (640, 480, 390, 375, 320):
                page.set_viewport_size({"width": width, "height": 1000})
                tabs.first.focus()
                expect(tabs.first).to_be_in_viewport(ratio=1)
                for key, tab in (
                    ("Tab", tabs.nth(1)),
                    ("Tab", tabs.last),
                    ("Shift+Tab", tabs.nth(1)),
                    ("Shift+Tab", tabs.first),
                ):
                    page.keyboard.press(key)
                    expect(
                        tab,
                        f"{width}px после {key}: "
                        + page.evaluate(
                            "document.activeElement.outerHTML.slice(0, 300)"
                        ),
                    ).to_be_focused()
                    expect(tab, f"{width}px после {key}").to_be_in_viewport(ratio=1)
                    assert tab.evaluate(
                        "element => element.getBoundingClientRect().right <= innerWidth"
                    )
            for control in (
                main_response.locator(".sparring-response__confirm .btn"),
                main_response.locator(".direct-message-action .btn"),
                main_response.locator(".sparring-contact").first,
            ):
                control.focus()
                expect(control).to_be_focused()
                assert control.evaluate(
                    "element => getComputedStyle(element).outlineStyle === 'solid'"
                )
            assert not errors, errors
            with page.expect_request("https://testserver" + direct_url) as opened_chat:
                main_response.locator(".direct-message-action .btn").click()
            assert opened_chat.value.method == "POST"
            assert "csrfmiddlewaretoken=" in (opened_chat.value.post_data or "")
            page.wait_for_url("https://testserver" + chat_url)
            expect(
                page.get_by_role("heading", name="Личный чат", exact=True)
            ).to_be_visible()
        finally:
            browser.close()


@pytest.mark.parametrize("confirmed", [False, True], ids=["cancel", "confirm"])
@pytest.mark.parametrize("keyboard", [False, True], ids=["mouse", "keyboard"])
@pytest.mark.parametrize("dynamic", [False, True], ids=["initial", "inserted"])
def test_phone_contact_when_activated_should_confirm_before_allowing_a_call(
    confirmed: bool, keyboard: bool, dynamic: bool
) -> None:
    """Клик и Enter требуют подтверждения, отмена блокирует переход на звонок.

    Args:
        confirmed: Принять или отклонить системное подтверждение.
        keyboard: Активировать ссылку клавишей Enter вместо клика.
        dynamic: Добавить контакт после загрузки страницы, как при AJAX-отклике.
    """
    contact: SparringContact = {
        "key": "phone",
        "label": "Телефон",
        "value": "8 (999) 123-45-67",
        "url": "tel:+79991234567",
        "external": False,
    }
    markup = render_to_string("sparring/_response_contact.html", {"link": contact})
    document = (
        '<!doctype html><div id="contacts">'
        + ("" if dynamic else markup)
        + '</div><script defer src="/static/js/main.js"></script>'
    )

    def serve(route: Route) -> None:
        """Отдать настоящий контакт и JavaScript, не обращаясь к внешней сети."""
        url = urlsplit(route.request.url)
        if url.netloc == "testserver" and url.path.startswith("/static/"):
            _serve_static(route)
        elif url.netloc == "testserver":
            route.fulfill(body=document, content_type="text/html; charset=utf-8")
        else:
            route.abort()

    dialogs: list[tuple[str, str]] = []

    def answer_call(dialog: Dialog) -> None:
        """Зафиксировать текст настоящего системного диалога и выбрать ответ."""
        dialogs.append((dialog.type, dialog.message))
        if confirmed:
            dialog.accept()
        else:
            dialog.dismiss()

    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ.get("CHAT_BROWSER_EXECUTABLE"), headless=True
        )
        context = browser.new_context(viewport={"width": 390, "height": 844})
        context.route("**/*", serve)
        page = context.new_page()
        page.on("dialog", answer_call)
        try:
            page.goto("https://testserver/phone-contact/")
            if dynamic:
                page.locator("#contacts").evaluate(
                    "(element, html) => { element.innerHTML = html; }", markup
                )
            phone = page.locator('.sparring-contact[href="tel:+79991234567"]')
            # Сначала проверяем результат обработчика сайта, затем блокируем сам
            # системный вызов tel:, чтобы тест никогда не запускал реальный звонок.
            phone.evaluate(
                """element => {
                window.__sparringCallAllowed = null;
                window.addEventListener('click', event => {
                    window.__sparringCallAllowed = !event.defaultPrevented;
                    event.preventDefault();
                }, {once: true});
            }"""
            )
            if keyboard:
                page.bring_to_front()
                assert page.evaluate("document.hasFocus()")
                phone.focus()
                page.keyboard.press("Enter")
            else:
                phone.click()

            assert dialogs == [("confirm", "Позвонить?")]
            assert page.evaluate("window.__sparringCallAllowed") is confirmed
            expect(phone).to_contain_text("Позвонить")
            expect(phone).to_contain_text("8 (999) 123-45-67")
            expect(page).to_have_url("https://testserver/phone-contact/")
        finally:
            browser.close()


@pytest.mark.parametrize(
    "contact_url",
    [
        "https://t.me/card_player",
        "https://wa.me/79991234567",
        "https://max.ru/u/card_player",
    ],
)
def test_dynamic_messenger_link_when_href_changes_should_not_be_intercepted(
    contact_url: str,
) -> None:
    """Ссылка, бывшая href="#" при загрузке, открывает подставленный мессенджер.

    Args:
        contact_url: Рабочий адрес контакта, который подставляет модальное окно.
    """
    document = (
        '<!doctype html><a id="contact" href="#" target="_blank" rel="noopener">Контакт</a>'
        '<script defer src="/static/js/main.js"></script>'
    )

    def serve(route: Route) -> None:
        """Отдать минимальную страницу с настоящим обработчиком ссылок проекта."""
        url = urlsplit(route.request.url)
        if url.netloc == "testserver" and url.path.startswith("/static/"):
            _serve_static(route)
        elif url.netloc == "testserver":
            route.fulfill(body=document, content_type="text/html; charset=utf-8")
        elif route.request.url == contact_url:
            route.fulfill(
                body="<!doctype html><title>Контакт</title>", content_type="text/html"
            )
        else:
            route.abort()

    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ.get("CHAT_BROWSER_EXECUTABLE"), headless=True
        )
        context = browser.new_context()
        context.route("**/*", serve)
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto("https://testserver/dynamic-contact/")
            page.locator("#contact").evaluate(
                "(element, url) => element.setAttribute('href', url)", contact_url
            )
            with page.expect_popup() as opened:
                page.get_by_role("link", name="Контакт", exact=True).click()
            popup = opened.value
            popup.wait_for_url(contact_url)
            assert not errors, errors
        finally:
            browser.close()
