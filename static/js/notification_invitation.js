/** Приглашение не запрашивает разрешение само и не превращает «Позже» в постоянный отказ. */
(function () {
    'use strict';
    const dialog = document.getElementById('notification-invitation');
    if (!dialog || (window.Notification && Notification.permission === 'denied')) return;
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
    dialog.showModal();
})();
