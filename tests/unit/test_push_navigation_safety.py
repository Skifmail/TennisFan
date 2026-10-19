"""Ограничения iOS-обхода: остальные push и посторонние адреса не перехватываются."""

import json
import subprocess
from pathlib import Path
from typing import Final

import pytest

ROOT: Final = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("agent", "target"),
    [
        (
            "Chrome",
            "https://tennisfan.ru/notification-center/confirm/#code=" + "a" * 43,
        ),
        (
            "iPhone",
            "https://untrusted.example/notification-center/confirm/#code=" + "a" * 43,
        ),
        ("iPhone", "https://tennisfan.ru/notification-center/confirm/#code=invalid"),
        ("iPhone", "https://tennisfan.ru/notification-center/confirm/?code=secret"),
        ("iPhone", "https://tennisfan.ru/tournaments/"),
        ("iPhone", "javascript:alert(1)"),
    ],
)
def test_worker_click_when_not_ios_confirmation_should_delegate_to_sdk(
    agent: str, target: str
) -> None:
    """Обход не меняет Chrome, обычные уведомления и небезопасные ссылки.

    Args:
        agent: User-Agent тестового worker.
        target: Поддельная ссылка без настоящего секрета.
    """
    harness = r"""
const vm = require('node:vm');
const fs = require('node:fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const listeners = {};
let intercepted = false;
vm.runInNewContext(fs.readFileSync(input.file, 'utf8'), {
    navigator: {userAgent: input.agent}, URL, URLSearchParams,
    self: {location: {origin: 'https://tennisfan.ru'},
        addEventListener: (name, callback) => {listeners[name] = callback;}}
});
listeners.notificationclick?.({notification: {data: {launchURL: input.target}, close() {intercepted = true;}},
    stopImmediatePropagation() {intercepted = true;}, waitUntil() {intercepted = true;}});
process.stdout.write(JSON.stringify({intercepted}));
"""
    result = subprocess.run(
        ["node", "-e", harness],
        input=json.dumps(
            {
                "file": str(ROOT / "static/js/push_worker_navigation.js"),
                "agent": agent,
                "target": target,
            }
        ),
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == {"intercepted": False}


@pytest.mark.parametrize("untrusted_source", [True, False])
def test_page_message_when_source_or_target_untrusted_should_ignore(
    untrusted_source: bool,
) -> None:
    """Страница не принимает поддельный отправитель или внешнюю ссылку.

    Args:
        untrusted_source: Подменить отправитель, иначе подменить адрес перехода.
    """
    harness = r"""
const vm = require('node:vm');
const fs = require('node:fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
let listener, navigated = false;
class ServiceWorker {constructor(){this.scriptURL = 'https://tennisfan.ru/OneSignalSDKWorker.js';}}
vm.runInNewContext(fs.readFileSync(input.file, 'utf8'), {
    ServiceWorker, URL, URLSearchParams,
    navigator: {userAgent: 'Chrome', serviceWorker: {addEventListener: (_name, callback) => {listener = callback;}}},
    location: {origin: 'https://tennisfan.ru', href: 'https://tennisfan.ru/', assign() {navigated = true;}}
});
listener({origin:'https://tennisfan.ru', source: input.untrusted ? {} : new ServiceWorker(),
    data:{type:'tennisfan:push-confirmation', url:(input.untrusted ? 'https://tennisfan.ru' : 'https://untrusted.example') + '/notification-center/confirm/#code=' + 'a'.repeat(43)}});
process.stdout.write(JSON.stringify({navigated}));
"""
    result = subprocess.run(
        ["node", "-e", harness],
        input=json.dumps(
            {
                "file": str(ROOT / "static/js/push_navigation.js"),
                "untrusted": untrusted_source,
            }
        ),
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == {"navigated": False}
