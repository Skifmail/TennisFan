(function () {
    'use strict';
    const trigger = document.querySelector('[data-help-trigger]');
    const dialog = trigger && document.getElementById(trigger.getAttribute('aria-controls'));
    if (!dialog || !trigger || typeof dialog.showModal !== 'function') return;

    let pointerStartedOnBackdrop = false;

    function isBackdrop(event) {
        if (event.target !== dialog) return false;
        const bounds = dialog.getBoundingClientRect();
        return event.clientX < bounds.left || event.clientX > bounds.right ||
            event.clientY < bounds.top || event.clientY > bounds.bottom;
    }

    trigger.addEventListener('click', function () {
        if (dialog.open) return;
        dialog.showModal();
        document.documentElement.classList.add('sparring-help-open');
        const body = dialog.querySelector('.sparring-help__body');
        if (body) body.scrollTop = 0;
    });

    dialog.addEventListener('pointerdown', function (event) {
        pointerStartedOnBackdrop = isBackdrop(event);
    });

    dialog.addEventListener('click', function (event) {
        const shouldClose = pointerStartedOnBackdrop && isBackdrop(event);
        pointerStartedOnBackdrop = false;
        if (shouldClose) dialog.close();
    });

    dialog.addEventListener('close', function () {
        document.documentElement.classList.remove('sparring-help-open');
        pointerStartedOnBackdrop = false;
        trigger.focus({preventScroll: true});
    });
})();
