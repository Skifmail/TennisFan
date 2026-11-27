(function () {
    'use strict';

    const dialog = document.getElementById('sparring-invite-dialog');
    const triggers = document.querySelectorAll('[data-invite-trigger]');
    if (!dialog || !triggers.length || typeof dialog.showModal !== 'function') return;

    const form = dialog.querySelector('form');
    const inviteeInput = document.getElementById('sparring-invite-dialog-invitee');
    const nameNode = dialog.querySelector('[data-invite-dialog-name]');
    const cityNode = dialog.querySelector('[data-invite-dialog-city]');
    const avatarNode = document.getElementById('sparring-invite-dialog-avatar');
    const placeholderAvatar = avatarNode ? avatarNode.getAttribute('src') : '';
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    let lastTrigger = null;
    let pointerStartedOnBackdrop = false;
    let closing = false;

    function isBackdrop(event) {
        if (event.target !== dialog) return false;
        const bounds = dialog.getBoundingClientRect();
        return event.clientX < bounds.left || event.clientX > bounds.right ||
            event.clientY < bounds.top || event.clientY > bounds.bottom;
    }

    function closeDialog() {
        if (!dialog.open || closing) return;
        if (reduceMotion.matches) {
            dialog.close();
            return;
        }
        closing = true;
        dialog.classList.add('is-closing');
        window.setTimeout(function () {
            dialog.close();
        }, 170);
    }

    function openDialog(trigger) {
        lastTrigger = trigger;
        if (form) form.reset();
        if (inviteeInput) inviteeInput.value = trigger.getAttribute('data-invite-id') || '';
        if (nameNode) nameNode.textContent = trigger.getAttribute('data-invite-name') || '';
        if (cityNode) {
            const city = trigger.getAttribute('data-invite-city') || '';
            cityNode.textContent = city;
            cityNode.hidden = !city;
        }
        if (avatarNode) {
            avatarNode.src = trigger.getAttribute('data-invite-avatar') || placeholderAvatar;
        }
        dialog.classList.remove('is-closing');
        dialog.showModal();
    }

    triggers.forEach(function (trigger) {
        trigger.addEventListener('click', function (event) {
            if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
            event.preventDefault();
            if (dialog.open) return;
            openDialog(trigger);
        });
    });

    dialog.querySelectorAll('[data-invite-dialog-close]').forEach(function (button) {
        button.addEventListener('click', closeDialog);
    });

    dialog.addEventListener('cancel', function (event) {
        event.preventDefault();
        closeDialog();
    });

    dialog.addEventListener('pointerdown', function (event) {
        pointerStartedOnBackdrop = isBackdrop(event);
    });

    dialog.addEventListener('click', function (event) {
        const shouldClose = pointerStartedOnBackdrop && isBackdrop(event);
        pointerStartedOnBackdrop = false;
        if (shouldClose) closeDialog();
    });

    dialog.addEventListener('close', function () {
        closing = false;
        dialog.classList.remove('is-closing');
        if (lastTrigger) {
            lastTrigger.focus({preventScroll: true});
            lastTrigger = null;
        }
    });
})();
