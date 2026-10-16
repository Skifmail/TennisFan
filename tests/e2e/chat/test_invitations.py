"""Браузерный путь приглашения: роли, подтверждение, уведомление и согласия."""

import json
import subprocess
import sys
from os import environ
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not environ.get("CHAT_E2E_URL"), reason="Нужен локальный браузерный стенд"
)


def test_invitation_when_moderator_sends_should_require_confirmation_and_recipient_consents() -> (
    None
):
    """Настройки ведут к трём способам, письмо и уведомление не вступают за игрока."""
    from playwright.sync_api import expect, sync_playwright

    sessions = json.loads(Path(environ["CHAT_E2E_SESSION_FILE"]).read_text())
    base = environ["CHAT_E2E_URL"]
    assert base == "http://127.0.0.1:18765"
    artifacts = Path(environ.get("CHAT_E2E_ARTIFACTS", "/tmp/chat-invitations-browser"))
    artifacts.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as driver:
        browser = driver.chromium.launch(
            executable_path=environ.get("CHAT_BROWSER_EXECUTABLE"), headless=True
        )
        contexts = [
            browser.new_context(viewport={"width": 390, "height": 1000})
            for _ in range(2)
        ]
        for index, context in enumerate(contexts):
            context.add_cookies(
                [
                    {"name": cookie["name"], "value": cookie["value"], "url": base}
                    for cookie in sessions["cookies"][index]
                ]
            )
        sender, recipient = (context.new_page() for context in contexts)
        try:
            sender.goto(base + f'/chats/join/?city_id={sessions["invitation_city_id"]}')
            sender.locator('input[name="personal_data"]').check()
            sender.locator('input[name="rules"]').check()
            sender.get_by_role(
                "button", name="Подтвердить и создать чат", exact=True
            ).click()
            sender.wait_for_url("**/chats/*/")
            room_url = sender.url
            sender.get_by_role("button", name="Настройки чата", exact=True).click()
            sender.get_by_role("link", name="Пригласить участников", exact=True).click()
            expect(
                sender.get_by_role("heading", name="Пригласить участников", exact=True)
            ).to_be_visible()
            expect(
                sender.get_by_role(
                    "button",
                    name="Отправить приглашение всем игрокам из города Тест приглашений",
                    exact=True,
                )
            ).to_be_visible()
            sender.get_by_role(
                "button",
                name="Пригласить всех пользователей платформы вне этого чата",
                exact=True,
            ).click()
            expect(
                sender.get_by_role("heading", name="Подтвердите отправку", exact=True)
            ).to_be_visible()
            sender.get_by_role("link", name="Отмена", exact=True).click()
            sender.locator("#invitation-search").fill("Михаил")
            sender.get_by_role("button", name="Найти", exact=True).click()
            target_id = sessions["invitation_target_id"]
            sender.locator(
                f'form:has(input[name="target_id"][value="{target_id}"])'
            ).get_by_role("button", name="Пригласить", exact=True).click()
            sender.screenshot(
                path=str(artifacts / "invitation-confirm-mobile.png"), full_page=True
            )
            assert sender.locator(".chat-invitations").evaluate(
                "(element) => element.scrollWidth <= element.clientWidth"
            )
            sender.get_by_role(
                "button", name="Подтвердить отправку", exact=True
            ).click()
            expect(
                sender.get_by_text("Приглашения поставлены в очередь.", exact=False)
            ).to_be_visible()
            sender.set_viewport_size({"width": 1440, "height": 1000})
            sender.screenshot(path=str(artifacts / "invitation-management-desktop.png"))
            subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import django;django.setup();from tests.support.chat_browser_seed import process_smoke_invitations;process_smoke_invitations()",
                ],
                check=True,
                timeout=60,
                cwd=Path(__file__).resolve().parents[3],
            )
            recipient.goto(base + "/users/notifications/")
            invitation_link = recipient.locator('a[href^="/chats/invitations/"]').first
            expect(invitation_link).to_be_visible()
            invitation_link.click()
            expect(
                recipient.get_by_role(
                    "heading", name="Вас приглашают в чат", exact=True
                )
            ).to_be_visible()
            recipient.screenshot(
                path=str(artifacts / "invitation-recipient-mobile.png")
            )
            recipient.get_by_role("link", name="Вступить в чат", exact=True).click()
            expect(recipient.locator('input[name="personal_data"]')).not_to_be_checked()
            expect(recipient.locator('input[name="rules"]')).not_to_be_checked()
            expect(
                recipient.get_by_role(
                    "button", name="Подтвердить и вступить", exact=True
                )
            ).to_be_disabled()
            recipient.locator('input[name="personal_data"]').check()
            recipient.locator('input[name="rules"]').check()
            recipient.get_by_role(
                "button", name="Подтвердить и вступить", exact=True
            ).click()
            expect(recipient).to_have_url(room_url)
            sender.goto(room_url + "management/")
            sender.locator(
                f'form:has(input[name="target_id"][value="{target_id}"]):has(input[name="operation"][value="moderator"])'
            ).get_by_role("button", name="Назначить модератором", exact=True).click()
            recipient.reload()
            recipient.get_by_role("button", name="Настройки чата", exact=True).click()
            expect(
                recipient.get_by_role("link", name="Пригласить участников", exact=True)
            ).to_be_visible()
            recipient.get_by_role(
                "link", name="Пригласить участников", exact=True
            ).click()
            expect(
                recipient.get_by_role(
                    "heading", name="Пригласить участников", exact=True
                )
            ).to_be_visible()
            from django.urls import reverse

            from apps.chat.invitations import invitation_email_token

            token = invitation_email_token(user_id=target_id)
            recipient.goto(
                base + reverse("chat:invitation_email_settings", args=[token])
            )
            recipient.get_by_role(
                "button", name="Отключить email-приглашения", exact=True
            ).click()
            expect(recipient.get_by_role("status")).to_contain_text(
                "Email-приглашения из городских чатов отключены"
            )
        finally:
            for context in contexts:
                context.close()
            browser.close()
