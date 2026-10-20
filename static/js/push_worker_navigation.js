/** Обход навигации открытой iOS PWA только для одноразового подтверждения. */
(function () {
    'use strict';
    if (!/iPad|iPhone|iPod/.test(navigator.userAgent)) return;
    const MESSAGE_TYPE = 'tennisfan:push-confirmation';
    const CONFIRM_PATH = '/notification-center/confirm/';
    const ACK_TIMEOUT_MS = 2000;
    const NATIVE_TIMEOUT_MS = 1000;

    function bounded(operation, fallback) {
        return new Promise(resolve => {
            const timer = setTimeout(() => resolve(fallback), NATIVE_TIMEOUT_MS);
            Promise.resolve().then(operation).then(value => {clearTimeout(timer); resolve(value);},
                () => {clearTimeout(timer); resolve(fallback);});
        });
    }
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
            // Записываем только после notificationclick, а не при получении push.
            if (self.tennisFanPushHandoff) await self.tennisFanPushHandoff.remember(target.href);
            const windows = await bounded(() => self.clients.matchAll({type: 'window', includeUncontrolled: true}), []);
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
                // Зависший focus не должен блокировать уже отправленное сообщение.
                void bounded(() => client.focus(), null);
                const acknowledged = delivered && await accepted;
                channel.port1.close();
                if (acknowledged) return;
                const moved = await bounded(() => client.navigate(target.href), null);
                if (moved && moved.url === target.href) return;
                break;
            }
            // При закрытой PWA остаётся стандартное открытие окна по точному URL.
            await bounded(() => self.clients.openWindow(target.href), null);
        })());
    });
})();
