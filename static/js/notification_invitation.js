/** Приглашение не запрашивает разрешение само и не превращает «Позже» в постоянный отказ. */
(function () {
    'use strict';
    const dialog = document.getElementById('notification-invitation');
    if (!dialog || (window.Notification && Notification.permission === 'denied')) return;
    // Отказ действует до конца текущих суток устройства, а не навсегда.
    const STORAGE_KEY = 'tennisfan:notification-invitation-snoozed';
    const today = () => {
        const now = new Date();
        const month = String(now.getMonth() + 1).padStart(2, '0');
        const day = String(now.getDate()).padStart(2, '0');
        return `${now.getFullYear()}-${month}-${day}`;
    };
    const stored = () => {
        try {
            return localStorage.getItem(STORAGE_KEY);
        } catch (error) {
            // Приватный режим может запрещать хранилище: тогда просто показываем окно.
            return null;
        }
    };
    if (stored() === today()) return;
    const initial = dialog.querySelector('[data-invitation-initial]');
    const later = dialog.querySelector('[data-invitation-later-message]');
    dialog.querySelector('[data-invitation-later]').addEventListener('click', () => {
        initial.hidden = true;
        later.hidden = false;
        const heading = later.querySelector('h2');
        heading.id = 'notification-invitation-later-title';
        dialog.setAttribute('aria-labelledby', heading.id);
        later.querySelector('button').focus();
    });
    const snooze = later.querySelector('[data-invitation-snooze]');
    later.querySelector('form').addEventListener('submit', () => {
        if (!snooze.checked) return;
        try {
            localStorage.setItem(STORAGE_KEY, today());
        } catch (error) {
            // Без хранилища отказ не сохранится: окно появится при следующем визите.
        }
    });
    dialog.showModal();
})();
