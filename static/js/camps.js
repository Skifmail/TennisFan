/* Показывает видео после загрузки, оставляя фото-заглушку до первого кадра. */
(() => {
    'use strict';
    const video = document.querySelector('[data-camp-hero-video]');
    if (!video) return;
    const hero = video.closest('[data-camp-hero]');
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        video.removeAttribute('autoplay');
        video.pause();
        return;
    }
    const reveal = () => hero?.classList.add('is-ready');
    if (video.readyState >= 2) reveal();
    video.addEventListener('loadeddata', reveal, { once: true });
    video.addEventListener('canplay', reveal, { once: true });
    video.addEventListener('error', () => hero?.classList.remove('is-ready'), { once: true });
    const playback = video.play();
    if (playback) playback.catch(() => {});
})();

/* Динамический состав поездки и обратная связь формы без внешних зависимостей. */
(() => {
    'use strict';
    const form = document.querySelector('[data-camp-application]');
    if (!form) return;
    const button = form.querySelector('[data-add-participant]');
    const total = form.querySelector('[name="people-TOTAL_FORMS"]');
    const list = form.querySelector('[data-participants]');
    const template = document.getElementById('camp-person-template');
    button?.addEventListener('click', () => {
        const index = Number(total.value);
        if (index >= 12) return;
        const fragment = template.content.cloneNode(true);
        fragment.querySelectorAll('input, select, textarea, label').forEach((element) => {
            ['name', 'id', 'for'].forEach((attribute) => {
                const value = element.getAttribute(attribute);
                if (value) element.setAttribute(attribute, value.replaceAll('__prefix__', String(index)));
            });
        });
        list.appendChild(fragment);
        total.value = String(index + 1);
        if (index + 1 >= 12) button.hidden = true;
        list.lastElementChild.querySelector('input:not([type="hidden"])')?.focus();
    });
    form.addEventListener('submit', () => {
        const submit = form.querySelector('[data-submit-label]');
        if (submit) {
            submit.disabled = true;
            submit.textContent = submit.dataset.submitLabel;
        }
    });
    window.addEventListener('pageshow', () => {
        const submit = form.querySelector('[data-submit-label]');
        if (submit) {
            submit.disabled = false;
            submit.textContent = 'Отправить заявку ↗';
        }
    });
})();
