/* Напоминание о профиле раз в шесть часов, отдельно для каждого пользователя. */
(() => {
    const dataNode = document.getElementById('profile-completion-data');
    const dialog = document.getElementById('profile-completion-dialog');
    if (!dataNode || !dialog || typeof dialog.showModal !== 'function') return;
    const data = JSON.parse(dataNode.textContent);
    const key = `profile-completion:${data.user_id}`;
    let fallback = 0;
    const due = () => {
        let last = fallback;
        try { last = Number(localStorage.getItem(key)) || 0; } catch (_) { /* storage недоступен */ }
        return Date.now() - last >= data.interval_ms;
    };
    const remind = () => {
        if (document.visibilityState !== 'visible' || dialog.open || !due() || document.querySelector('dialog[open]')) return;
        fallback = Date.now();
        try { localStorage.setItem(key, String(fallback)); } catch (_) { /* используем память вкладки */ }
        dialog.showModal();
    };
    document.getElementById('profile-completion-later').addEventListener('click', () => dialog.close());
    setTimeout(remind, 15000);
    setInterval(remind, 60000);
})();
