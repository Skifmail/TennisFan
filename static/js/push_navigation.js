/** Принимает ссылку подтверждения исключительно от worker текущего сайта. */
(function () {
    'use strict';
    if (!('serviceWorker' in navigator)) return;
    const MESSAGE_TYPE = 'tennisfan:push-confirmation';
    const CONFIRM_PATH = '/notification-center/confirm/';
    const WORKER_PATH = '/OneSignalSDKWorker.js';
    const CODE_PATTERN = /^[A-Za-z0-9_-]{43}$/;
    navigator.serviceWorker.addEventListener('message', event => {
        // Источник проверяем по объекту worker и его scriptURL, не по origin обёртки сообщения.
        if (!(event.source instanceof ServiceWorker) ||
            event.data?.type !== MESSAGE_TYPE) return;
        let source, target;
        try {
            source = new URL(event.source.scriptURL);
            target = new URL(event.data.url);
        } catch (_error) { return; }
        const code = new URLSearchParams(target.hash.slice(1));
        if (source.origin !== location.origin || source.pathname !== WORKER_PATH ||
            target.origin !== location.origin || target.pathname !== CONFIRM_PATH ||
            target.search || target.username || target.password ||
            Array.from(code).length !== 1 || !CODE_PATTERN.test(code.get('code') || '')) return;
        if (event.ports?.[0]) event.ports[0].postMessage({accepted: true});
        if (location.href !== target.href) location.assign(target.href);
    });
    if (/iPad|iPhone|iPod/.test(navigator.userAgent)) {
        // Сохраняем подписку и явно проверяем обновление существующего worker.
        navigator.serviceWorker.getRegistration().then(registration => {
            if (registration) return registration.update();
        }).catch(() => {});
    }
})();
