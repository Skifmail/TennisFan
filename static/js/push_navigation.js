/** Принимает ссылку подтверждения исключительно от worker текущего сайта. */
(function () {
    'use strict';
    if (!('serviceWorker' in navigator)) return;
    const MESSAGE_TYPE = 'tennisfan:push-confirmation';
    const CONFIRM_PATH = '/notification-center/confirm/';
    const WORKER_PATH = '/OneSignalSDKWorker.js';
    const CODE_PATTERN = /^[A-Za-z0-9_-]{43}$/;
    const POLL_MS = 1000;
    const handoff = window.tennisFanPushHandoff;
    const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent);
    const loadedTarget = handoff?.parse(location.href)?.href || null;
    let recovering = false;
    let timer = null;

    function navigate(target) {
        if (location.href === target.href) {
            if (loadedTarget !== target.href) location.reload();
            return;
        }
        const sameDocument = location.pathname === target.pathname;
        location.assign(target.href);
        // Новый fragment не загружает новую форму: после прошлого подтверждения
        // текущий документ может вообще не содержать полей подключения.
        if (sameDocument) location.reload();
    }

    async function recover() {
        if (!isIOS || !handoff || recovering || document.visibilityState === 'hidden') return;
        recovering = true;
        try {
            const saved = await handoff.read();
            const target = handoff.parse(saved);
            if (target) navigate(target);
        } finally { recovering = false; }
    }

    function watch() {
        void recover();
        if (timer !== null || !handoff?.waiting()) return;
        timer = window.setInterval(() => {
            if (!handoff.waiting()) {window.clearInterval(timer); timer = null; return;}
            void recover();
        }, POLL_MS);
    }

    if (isIOS && handoff) {
        const arrived = handoff.parse(location.href);
        if (arrived) {
            // Только реально загруженная страница подтверждения снимает передачу.
            void handoff.forget(arrived.href);
            handoff.stopWaiting();
        } else {
            let authenticated = false;
            try {
                const config = document.getElementById('web-push-config');
                authenticated = location.pathname === CONFIRM_PATH ||
                    JSON.parse(config?.textContent || '{}').authenticated === true;
            } catch (_error) { /* Не перенаправляем гостя или истёкшую сессию. */ }
            if (authenticated) {
                window.addEventListener('focus', watch);
                window.addEventListener('pageshow', watch);
                window.addEventListener('tennisfan:push-confirmation-wait', watch);
                document.addEventListener('visibilitychange', watch);
                watch();
            } else handoff.stopWaiting();
        }
    }
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
        navigate(target);
        if (event.ports?.[0]) event.ports[0].postMessage({accepted: true});
    });
    if (isIOS) {
        // Сохраняем подписку и явно проверяем обновление существующего worker.
        navigator.serviceWorker.getRegistration().then(registration => {
            if (registration) return registration.update();
        }).catch(() => {});
    }
})();
