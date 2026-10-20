/** Обход навигации открытой iOS PWA только для одноразового подтверждения. */
(function () {
    'use strict';
    if (!/iPad|iPhone|iPod/.test(navigator.userAgent)) return;
    const MESSAGE_TYPE = 'tennisfan:push-confirmation';
    const CONFIRM_PATH = '/notification-center/confirm/';
    const ACK_TIMEOUT_MS = 2000;
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
                const channel = new MessageChannel();
                const accepted = new Promise(resolve => {
                    const timer = setTimeout(() => resolve(false), ACK_TIMEOUT_MS);
                    channel.port1.onmessage = reply => {
                        if (reply.data?.accepted === true) {
                            clearTimeout(timer);
                            resolve(true);
                        }
                    };
                });
                let delivered = true;
                try { client.postMessage({type: MESSAGE_TYPE, url: target.href}, [channel.port2]); }
                catch (_error) { delivered = false; }
                try { await client.focus(); } catch (_error) { /* Продолжаем другими способами. */ }
                const acknowledged = delivered && await accepted;
                channel.port1.close();
                if (acknowledged) return;
                try {
                    const moved = await client.navigate(target.href);
                    if (moved && moved.url === target.href) return;
                } catch (_error) { /* Холодное открытие остаётся запасным путём. */ }
                break;
            }
            // При закрытой PWA остаётся стандартное открытие окна по точному URL.
            await self.clients.openWindow(target.href);
        })());
    });
})();
