(() => {
    'use strict';
    const form = document.getElementById('stringer-application-form');
    if (!form) return;
    const summary = document.getElementById('application-errors');
    const fields = [...form.querySelectorAll('input:not([type="hidden"]), textarea, select')];
    form.noValidate = true;

    const label = field => field.dataset.label || field.labels?.[0]?.textContent.trim() || 'Поле';
    function message(field) {
        const validity = field.validity;
        if (validity.valueMissing) return `Заполните поле «${label(field)}».`;
        if (validity.typeMismatch) return 'Введите email в формате name@example.ru.';
        if (validity.patternMismatch) return field.dataset.formatError || 'Проверьте формат значения.';
        if (validity.tooLong) return `Максимум ${field.maxLength} символов.`;
        if (field.type === 'file' && field.files.length) {
            if (field.files[0].size > 10 * 1024 * 1024) return 'Размер фотографии не должен превышать 10 МБ.';
        }
        return validity.valid ? '' : field.validationMessage;
    }
    function showError(field, text) {
        const group = field.closest('.form-group');
        let errors = group.querySelector('.errorlist');
        if (errors) errors.remove();
        group.classList.toggle('has-error', Boolean(text));
        field.setAttribute('aria-invalid', String(Boolean(text)));
        const hint = document.getElementById(`${field.id}_helptext`);
        const describedBy = hint ? [hint.id] : [];
        if (text) {
            errors = document.createElement('ul');
            errors.className = 'errorlist';
            errors.id = `${field.id}_error`;
            const item = document.createElement('li');
            item.textContent = text;
            errors.append(item);
            group.append(errors);
            describedBy.push(errors.id);
        }
        field.setAttribute('aria-describedby', describedBy.join(' '));
    }
    function firstError() {
        const field = fields.find(field => field.getAttribute('aria-invalid') === 'true');
        if (field) {
            field.focus({preventScroll: true});
            field.scrollIntoView({block: 'center'});
        } else summary.focus();
    }
    fields.forEach(field => {
        const errors = field.closest('.form-group')?.querySelector('.errorlist');
        if (errors) showError(field, errors.textContent.trim());
        field.addEventListener('blur', () => showError(field, message(field)));
        field.addEventListener('input', () => {
            if (field.getAttribute('aria-invalid') === 'true') showError(field, message(field));
        });
        field.addEventListener('change', () => showError(field, message(field)));
    });
    summary.addEventListener('click', event => {
        const link = event.target.closest('a[href^="#"]');
        if (!link) return;
        const target = document.getElementById(link.hash.slice(1));
        if (target) {
            event.preventDefault();
            target.focus({preventScroll: true});
            target.scrollIntoView({block: 'center'});
        }
    });
    form.addEventListener('submit', event => {
        const invalid = fields.map(field => ({field, text: message(field)})).filter(item => item.text);
        fields.forEach(field => showError(field, message(field)));
        if (!invalid.length) return;
        event.preventDefault();
        const list = summary.querySelector('ul');
        list.replaceChildren();
        invalid.forEach(({field, text}) => {
            const item = document.createElement('li');
            const link = document.createElement('a');
            link.href = `#${field.id}`;
            link.textContent = `${label(field)}: ${text}`;
            item.append(link);
            list.append(item);
        });
        summary.hidden = false;
        firstError();
    });
    if (!summary.hidden) firstError();
})();
