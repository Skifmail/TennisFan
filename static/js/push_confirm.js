/** Секрет из push не отправляется в GET, реферер или сторонние ресурсы. */
(function () {
    'use strict';
    const input = document.querySelector('input[name="code"]');
    const button = document.querySelector('button[name="confirm"]');
    if (!input || !button) return;

    function receiveCode() {
        const code = new URLSearchParams(window.location.hash.slice(1)).get('code');
        window.history.replaceState(null, '', window.location.pathname);
        input.value = code || '';
        button.disabled = !code;
    }

    window.addEventListener('hashchange', receiveCode);
    receiveCode();
})();
