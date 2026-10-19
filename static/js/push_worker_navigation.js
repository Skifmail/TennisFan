/** Обход навигации открытой iOS PWA только для одноразового подтверждения. */
(function () {
    'use strict';
    if (!/iPad|iPhone|iPod/.test(navigator.userAgent)) return;
    const MESSAGE_TYPE = 'tennisfan:push-confirmation';
    const CONFIRM_PATH = '/notification-center/confirm/';
    const CODE_PATTERN = /^[A-Za-z0-9_-]{43}$/;

    self.addEventListener('install', event => event.waitUntil(self.skipWaiting()));
    self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
    self.addEventListener('notificationclick', event => {
        let target;
        try {
            target = new URL(event.notification.data?.launchURL);
        } catch (_error) { return; }
        const code = new URLSearchParams(target.hash.slice(1));
        if (target.origin !== self.location.origin || target.pathname !== CONFIRM_PATH ||
            target.search || target.username || target.password ||
            Array.from(code).length !== 1 || !CODE_PATTERN.test(code.get('code') || '')) return;

        // SDK оставляем владельцем остальных кликов; здесь не допускаем двойной переход.
        event.stopImmediatePropagation();
        event.notification.close();
        event.waitUntil((async () => {
            const windows = await self.clients.matchAll({type: 'window', includeUncontrolled: true});
            const local = windows.filter(client =>
                typeof client.url === 'string' && client.url.startsWith(target.origin + '/'));
            local.sort((left, right) => Number(right.focused) - Number(left.focused));
            for (const client of local) {
                // Сообщение позволяет странице выполнить location.assign даже при сбое navigate.
                client.postMessage({type: MESSAGE_TYPE, url: target.href});
                try { await client.focus(); } catch (_error) { /* PWA может быть ещё скрыта. */ }
                return;
            }
            // При закрытой PWA остаётся стандартное открытие окна по точному URL.
            await self.clients.openWindow(target.href);
        })());
    });
})();
