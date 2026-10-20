/** Короткоживущая передача уже нажатой ссылки между экземплярами worker и PWA. */
(function () {
    'use strict';
    const CONFIRM_PATH = '/notification-center/confirm/';
    const CODE_PATTERN = /^[A-Za-z0-9_-]{43}$/;
    const DATABASE = 'tennisfan-push-clicks';
    const DATABASE_VERSION = 1;
    const STORE = 'clicked-confirmation';
    const RECORD_KEY = 'latest';
    const OPERATION_TIMEOUT_MS = 1000;
    const CLICK_LIFETIME_MS = 5 * 60 * 1000;
    const WAIT_KEY = 'tennisfan:push-confirmation-wait';

    function parse(value) {
        let target;
        try { target = new URL(value); } catch (_error) { return null; }
        const origin = globalThis.location?.origin || globalThis.self?.location?.origin;
        const code = new URLSearchParams(target.hash.slice(1));
        return target.origin === origin && target.pathname === CONFIRM_PATH &&
            !target.search && !target.username && !target.password &&
            Array.from(code).length === 1 && CODE_PATTERN.test(code.get('code') || '') ? target : null;
    }

    function transact(action) {
        if (typeof indexedDB === 'undefined') return Promise.resolve(null);
        return new Promise(resolve => {
            let database, finished = false;
            const finish = value => {
                if (finished) return;
                finished = true;
                clearTimeout(timer);
                if (database) database.close();
                resolve(value);
            };
            const timer = setTimeout(() => finish(null), OPERATION_TIMEOUT_MS);
            let opening;
            try { opening = indexedDB.open(DATABASE, DATABASE_VERSION); }
            catch (_error) { finish(null); return; }
            opening.onupgradeneeded = () => {
                if (!opening.result.objectStoreNames.contains(STORE)) opening.result.createObjectStore(STORE);
            };
            opening.onerror = opening.onblocked = () => finish(null);
            opening.onsuccess = () => {
                database = opening.result;
                if (finished) { database.close(); return; }
                try {
                    const transaction = database.transaction(STORE, 'readwrite');
                    let result = null;
                    transaction.oncomplete = () => finish(result);
                    transaction.onabort = transaction.onerror = () => finish(null);
                    action(transaction.objectStore(STORE), value => {result = value;});
                } catch (_error) { finish(null); }
            };
        });
    }

    function remember(url) {
        const target = parse(url);
        if (!target) return Promise.resolve(false);
        return transact((store, result) => {
            store.put({url: target.href, expiresAt: Date.now() + CLICK_LIFETIME_MS}, RECORD_KEY);
            result(true);
        });
    }

    function read() {
        return transact((store, result) => {
            const request = store.get(RECORD_KEY);
            request.onsuccess = () => {
                const record = request.result;
                const target = parse(record?.url);
                if (!target || !Number.isFinite(record.expiresAt) || record.expiresAt <= Date.now() ||
                    record.expiresAt > Date.now() + CLICK_LIFETIME_MS) {
                    store.delete(RECORD_KEY);
                    result(null);
                } else result(target.href);
            };
        });
    }

    function forget(url) {
        return transact((store, result) => {
            const request = store.get(RECORD_KEY);
            request.onsuccess = () => {
                if (request.result?.url === url) store.delete(RECORD_KEY);
                result(true);
            };
        });
    }

    function beginWaiting() {
        try { sessionStorage.setItem(WAIT_KEY, String(Date.now() + CLICK_LIFETIME_MS)); }
        catch (_error) { /* Возврат в приложение всё равно проверит уже нажатую ссылку. */ }
        if (typeof window !== 'undefined') window.dispatchEvent(new Event('tennisfan:push-confirmation-wait'));
    }

    function waiting() {
        try {
            const deadline = Number(sessionStorage.getItem(WAIT_KEY));
            return deadline > Date.now() && deadline <= Date.now() + CLICK_LIFETIME_MS;
        } catch (_error) { return false; }
    }

    function stopWaiting() {
        try { sessionStorage.removeItem(WAIT_KEY); } catch (_error) { /* Не мешаем форме. */ }
    }

    globalThis.tennisFanPushHandoff = {parse, remember, read, forget, beginWaiting, waiting, stopWaiting};
})();
