/* Компактные меню закрываются по Escape и клику вне меню. Формы работают без JS. */
(function () {
    'use strict';
    const panel = document.querySelector('.sparring-compact');
    if (!panel) return;

    document.addEventListener('click', function (event) {
        if (!(event.target instanceof Node)) return;
        panel.querySelectorAll('details[open]').forEach(function (menu) {
            if (!menu.contains(event.target)) menu.open = false;
        });
    });

    document.addEventListener('keydown', function (event) {
        if (event.key !== 'Escape') return;
        const menu = panel.querySelector('details[open]');
        if (!menu) return;
        menu.open = false;
        const summary = menu.querySelector('summary');
        if (summary) summary.focus();
        event.preventDefault();
    });
})();
