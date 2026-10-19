/** Счётчики символов: ограничения серверной формы остаются обязательными. */
document.addEventListener('DOMContentLoaded', function () {
    'use strict';
    document.querySelectorAll('[data-counter]').forEach(function (input) {
        const counter = document.createElement('small');
        counter.setAttribute('aria-live', 'polite');
        input.insertAdjacentElement('afterend', counter);
        function update() {
            counter.textContent = ' ' + Array.from(input.value).length + ' / ' + input.dataset.counter;
        }
        input.addEventListener('input', update);
        update();
    });
});
