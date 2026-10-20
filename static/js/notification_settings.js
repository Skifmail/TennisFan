/** Единая панель каналов и обязательное предупреждение перед отключением push. */
(function () {
    'use strict';
    const dialog = document.getElementById('notification-disable-dialog');
    const form = document.querySelector('[data-notification-preferences]');
    if (!dialog || !form) return;
    let pending = null;
    window.tennisFanConfirmPushDisable = function (scope = 'account') {
        if (pending) return pending;
        const description = document.getElementById('notification-disable-description');
        description.textContent = scope === 'device'
            ? 'На этом устройстве перестанут приходить push. На других подключённых устройствах они сохранятся. Уведомления в личном кабинете всё равно будут приходить, а история сохранится. Настройка email не изменится.'
            : 'Push перестанут приходить на всех подключённых устройствах. Уведомления в личном кабинете всё равно будут приходить, а история сохранится. Настройка email не изменится.';
        dialog.returnValue = 'cancel';
        pending = new Promise(resolve => {
            dialog.addEventListener('close', () => {
                const accepted = dialog.returnValue === 'disable';
                pending = null;
                resolve(accepted);
            }, {once: true});
            dialog.showModal();
        });
        return pending;
    };
    let confirmed = false;
    form.addEventListener('submit', async event => {
        const push = form.querySelector('[name="push_enabled"]');
        if (!push || confirmed || form.dataset.pushEnabled !== 'true' || push.checked) return;
        event.preventDefault();
        if (await window.tennisFanConfirmPushDisable()) {
            confirmed = true;
            form.requestSubmit();
        } else {
            push.checked = true;
        }
    });
})();
