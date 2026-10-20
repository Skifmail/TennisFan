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

/** Прогресс обновляется без перезагрузки формы; ошибку сети не выдаём за успех. */
document.addEventListener('DOMContentLoaded', function () {
    'use strict';
    const form = document.getElementById('broadcast_form');
    if (form && !form.dataset.submissionGuard) {
        form.dataset.submissionGuard = 'true';
        let submitted = false;
        form.addEventListener('submit', function (event) {
            if (submitted) { event.preventDefault(); return; }
            submitted = true;
            const sender = event.submitter;
            if (sender?.name) {
                const action = document.createElement('input');
                action.type = 'hidden'; action.name = sender.name; action.value = sender.value;
                form.appendChild(action);
            }
            form.querySelectorAll('button[type="submit"], input[type="submit"]').forEach(button => {button.disabled = true;});
        });
    }
    const panel = document.getElementById('broadcast-progress');
    if (!panel || panel.dataset.started) return;
    panel.dataset.started = 'true';
    const button = document.getElementById('broadcast-progress-refresh');
    const message = panel.querySelector('[data-progress-refresh]');
    const POLL_MS = 5000;
    const TIMEOUT_MS = 10000;
    let terminal = panel.dataset.terminal === 'true';
    let busy = false;
    let timer = null;

    function schedule() {
        if (timer !== null) window.clearTimeout(timer);
        if (!terminal) timer = window.setTimeout(refresh, POLL_MS);
    }
    async function refresh() {
        if (busy) return;
        if (document.visibilityState === 'hidden') { schedule(); return; }
        busy = true;
        if (button) button.disabled = true;
        const controller = new AbortController();
        const timeout = window.setTimeout(() => controller.abort(), TIMEOUT_MS);
        try {
            const response = await fetch(panel.dataset.progressUrl, {
                credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
                headers: {'Accept': 'application/json'},
            });
            if (!response.ok || response.redirected) throw new Error('status unavailable');
            const data = await response.json();
            if (typeof data.stage !== 'string' || typeof data.terminal !== 'boolean' || !data.channels) throw new Error('invalid status');
            panel.querySelector('[data-progress-stage]').textContent = data.stage;
            panel.querySelector('[data-progress-users]').textContent = data.counters_complete ? data.processed_users : '—';
            panel.querySelector('[data-progress-inbox]').textContent = data.counters_complete ? data.inbox_created : '—';
            panel.querySelectorAll('[data-channel][data-state]').forEach(cell => {
                cell.textContent = data.channels[cell.dataset.channel]?.[cell.dataset.state] ?? 0;
            });
            terminal = data.terminal;
            message.textContent = (terminal ? 'Обработка завершена. ' : 'Обновляется автоматически. ') + 'Статус проверен: ' + new Date(data.as_of).toLocaleTimeString();
        } catch (_error) {
            message.textContent = 'Не удалось обновить статус. Показаны предыдущие данные. Нажмите «Обновить статус» или дождитесь автоматической проверки.';
        } finally {
            window.clearTimeout(timeout);
            busy = false;
            if (button) button.disabled = false;
            schedule();
        }
    }
    if (button) button.addEventListener('click', refresh);
    schedule();
});
