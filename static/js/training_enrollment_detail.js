// @ts-check
/** Копирование контактов с выделением текста при недоступном буфере обмена. */
(function () {
    'use strict';

    const page = document.querySelector('.enrollment-detail');
    if (!page) return;
    const status = page.querySelector('[data-copy-status]');

    /** @param {HTMLElement} contact Элемент с отображаемым контактом клиента. */
    function selectContact(contact) {
        const selection = window.getSelection();
        if (!selection) return;
        const range = document.createRange();
        range.selectNodeContents(contact);
        selection.removeAllRanges();
        selection.addRange(range);
    }

    page.querySelectorAll('[data-copy-contact]').forEach(button => {
        if (!(button instanceof HTMLButtonElement)) return;
        const contactId = button.dataset.copyContact;
        if (!contactId) return;
        const contact = document.getElementById(contactId);
        if (!contact) return;
        button.hidden = false;
        button.addEventListener('click', async () => {
            if (status) status.textContent = '';
            try {
                if (!navigator.clipboard || !navigator.clipboard.writeText) {
                    throw new Error('Clipboard unavailable');
                }
                await navigator.clipboard.writeText((contact.textContent || '').trim());
                if (status) status.textContent = 'Контакт скопирован.';
            } catch (_error) {
                selectContact(contact);
                if (status) {
                    status.textContent = 'Не удалось скопировать автоматически. Контакт выделен — скопируйте его вручную.';
                }
            }
        });
    });
})();
