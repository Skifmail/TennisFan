/* Отклик раскрывает контакты в своей карточке; чужие карточки не меняются. */
(function() {
    'use strict';

    const feed = document.getElementById('sparring-feed');
    if (!feed) return;

    function csrfToken() {
        const cookie = document.cookie.split('; ').find(value => value.startsWith('csrftoken='));
        return cookie ? decodeURIComponent(cookie.slice('csrftoken='.length)) : '';
    }

    async function respond(button) {
        const card = button.closest('.sparring-card');
        const contacts = card && card.querySelector('[data-author-contacts]');
        const status = card && card.querySelector('.sparring-card__contact-status');
        const url = button.dataset.respondUrl;
        if (!contacts || !status || !url || button.disabled) return;

        const caption = button.textContent;
        let saved = false;
        button.disabled = true;
        button.textContent = 'Отправляем…';
        button.setAttribute('aria-busy', 'true');

        try {
            const response = await fetch(url, {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken()
                },
                body: '{}'
            });
            const contentType = response.headers.get('content-type') || '';
            if (!contentType.includes('application/json')) {
                status.textContent = 'Не удалось отправить отклик. Обновите страницу и попробуйте снова.';
                return;
            }
            const data = await response.json();
            if (!response.ok || data.success !== true) {
                status.textContent = typeof data.error === 'string' ? data.error : 'Не удалось отправить отклик. Попробуйте снова.';
                if (typeof data.redirect === 'string') {
                    const destination = new URL(data.redirect, window.location.href);
                    if (destination.origin === window.location.origin) {
                        setTimeout(() => window.location.assign(destination.href), 1000);
                    }
                }
                return;
            }

            saved = true;
            button.textContent = 'Отклик отправлен';
            // Сервер рендерит общий шаблон с автоэкранированием только после сохранения отклика.
            if (typeof data.contacts_html === 'string' && data.contacts_html.trim()) {
                contacts.innerHTML = data.contacts_html;
            } else {
                const hint = document.createElement('p');
                hint.className = 'sparring-card__contacts-hint';
                hint.textContent = 'Отклик сохранён, но контакты не загрузились. Обновите страницу.';
                contacts.replaceChildren(hint);
            }
            status.textContent = typeof data.message === 'string' ? data.message : 'Отклик отправлен. Автор заявки получит уведомление.';
        } catch (_) {
            status.textContent = 'Ошибка сети. Проверьте подключение и попробуйте ещё раз.';
        } finally {
            button.removeAttribute('aria-busy');
            if (!saved) {
                button.disabled = false;
                button.textContent = caption;
            }
        }
    }

    feed.addEventListener('click', function(event) {
        if (event.defaultPrevented || !(event.target instanceof Element)) return;
        const button = event.target.closest('.sparring-respond-btn');
        if (button && feed.contains(button)) respond(button);
    });
})();
