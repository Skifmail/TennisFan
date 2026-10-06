/**
 * Скачивание документов турнира (регламент, сетка) с окном ожидания.
 *
 * Ссылка с атрибутом data-export-download="Подготовка сетки" скачивается
 * через fetch: пока сервер собирает PDF, показывается окно с анимацией,
 * при получении файла — полоса прогресса, затем файл сохраняется.
 * Если fetch/Blob недоступны, ссылка работает как обычная.
 */
(function () {
    'use strict';

    if (!window.fetch || !window.Blob || !window.URL || !URL.createObjectURL) return;

    var CLOSE_DELAY_MS = 900;
    var NETWORK_ERROR = 'Нет связи с сервером. Проверьте интернет и попробуйте ещё раз.';
    var modal = null;
    var busy = false;
    var lastLink = null;
    var lastFocus = null;

    function ExportError(message) {
        this.userMessage = message;
    }

    function buildModal() {
        var root = document.createElement('div');
        root.className = 'export-loader';
        root.setAttribute('hidden', '');
        root.innerHTML =
            '<div class="export-loader__backdrop"></div>' +
            '<div class="export-loader__dialog" role="dialog" aria-modal="true" aria-labelledby="export-loader-title" aria-describedby="export-loader-status">' +
            '  <div class="export-loader__visual" aria-hidden="true">' +
            '    <div class="export-loader__doc">' +
            '      <span class="export-loader__line"></span>' +
            '      <span class="export-loader__line"></span>' +
            '      <span class="export-loader__line"></span>' +
            '      <span class="export-loader__line export-loader__line--short"></span>' +
            '    </div>' +
            '    <span class="export-loader__ball"></span>' +
            '    <svg class="export-loader__check" viewBox="0 0 24 24"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>' +
            '  </div>' +
            '  <p class="export-loader__title" id="export-loader-title"></p>' +
            '  <p class="export-loader__status" id="export-loader-status" aria-live="polite"></p>' +
            '  <div class="export-loader__bar" role="progressbar" aria-valuemin="0" aria-valuemax="100">' +
            '    <span class="export-loader__fill"></span>' +
            '  </div>' +
            '  <div class="export-loader__actions">' +
            '    <button type="button" class="export-loader__btn export-loader__btn--primary" data-export-retry>Попробовать ещё раз</button>' +
            '    <button type="button" class="export-loader__btn" data-export-close>Закрыть</button>' +
            '  </div>' +
            '</div>';
        document.body.appendChild(root);
        root.querySelector('[data-export-close]').addEventListener('click', hide);
        root.querySelector('[data-export-retry]').addEventListener('click', function () {
            if (lastLink) start(lastLink);
        });
        root.addEventListener('keydown', function (event) {
            if (event.key === 'Escape' && !busy) hide();
        });
        return root;
    }

    function part(name) {
        return modal.querySelector('.export-loader__' + name);
    }

    function setState(state, status) {
        modal.setAttribute('data-state', state);
        part('status').textContent = status;
    }

    function setProgress(percent) {
        var bar = part('bar');
        var fill = part('fill');
        if (percent === null) {
            bar.classList.add('is-indeterminate');
            bar.removeAttribute('aria-valuenow');
            fill.style.width = '';
            return;
        }
        var value = Math.max(0, Math.min(100, Math.round(percent)));
        bar.classList.remove('is-indeterminate');
        bar.setAttribute('aria-valuenow', String(value));
        fill.style.width = value + '%';
    }

    function show(title) {
        if (!modal) modal = buildModal();
        if (modal.hasAttribute('hidden')) lastFocus = document.activeElement;
        part('title').textContent = title;
        modal.removeAttribute('hidden');
        document.documentElement.classList.add('export-loader-open');
        requestAnimationFrame(function () {
            modal.classList.add('is-visible');
            var dialog = part('dialog');
            dialog.setAttribute('tabindex', '-1');
            dialog.focus();
        });
    }

    function hide() {
        if (!modal) return;
        modal.classList.remove('is-visible');
        document.documentElement.classList.remove('export-loader-open');
        setTimeout(function () {
            if (!modal.classList.contains('is-visible')) modal.setAttribute('hidden', '');
        }, 200);
        if (lastFocus && lastFocus.focus) lastFocus.focus();
    }

    function filenameFrom(response, fallback) {
        var header = response.headers.get('Content-Disposition') || '';
        var encoded = /filename\*=UTF-8''([^;]+)/i.exec(header);
        if (encoded) {
            try {
                return decodeURIComponent(encoded[1].trim());
            } catch (error) {
                return fallback;
            }
        }
        var plain = /filename="?([^";]+)"?/i.exec(header);
        return plain ? plain[1] : fallback;
    }

    function readBody(response) {
        var total = Number(response.headers.get('Content-Length')) || 0;
        if (!response.body || !response.body.getReader || !total) {
            setProgress(null);
            return response.blob();
        }
        var type = response.headers.get('Content-Type') || 'application/octet-stream';
        var reader = response.body.getReader();
        var chunks = [];
        var received = 0;
        function pump() {
            return reader.read().then(function (result) {
                if (result.done) return new Blob(chunks, { type: type });
                chunks.push(result.value);
                received += result.value.length;
                var percent = Math.min(100, Math.round((received / total) * 100));
                setProgress(percent);
                part('status').textContent = 'Скачиваем файл… ' + percent + '%';
                return pump();
            });
        }
        return pump();
    }

    function saveBlob(blob, filename) {
        var url = URL.createObjectURL(blob);
        var anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = filename;
        anchor.rel = 'noopener';
        anchor.style.display = 'none';
        document.body.appendChild(anchor);
        anchor.click();
        document.body.removeChild(anchor);
        setTimeout(function () {
            URL.revokeObjectURL(url);
        }, 60000);
    }

    function errorText(response) {
        if (response.status === 403) return 'Нет доступа к этому турниру.';
        if (response.status === 404) return 'Турнир не найден.';
        return 'Не удалось подготовить файл. Попробуйте ещё раз.';
    }

    function start(link) {
        if (busy) return;
        busy = true;
        lastLink = link;
        show(link.getAttribute('data-export-download') || 'Подготовка файла');
        setState('loading', 'Собираем документ, это может занять до минуты…');
        setProgress(null);

        var fallbackName = link.getAttribute('data-export-filename') || 'document';
        fetch(link.href, { credentials: 'same-origin' })
            .then(function (response) {
                var type = response.headers.get('Content-Type') || '';
                if (!response.ok || type.indexOf('text/html') === 0) {
                    throw new ExportError(errorText(response));
                }
                setState('downloading', 'Скачиваем файл…');
                setProgress(0);
                return readBody(response).then(function (blob) {
                    return { blob: blob, name: filenameFrom(response, fallbackName) };
                });
            })
            .then(function (file) {
                setProgress(100);
                saveBlob(file.blob, file.name);
                setState('done', 'Готово! Файл сохранён в загрузки.');
                busy = false;
                setTimeout(hide, CLOSE_DELAY_MS);
            })
            .catch(function (error) {
                busy = false;
                part('title').textContent = 'Не удалось скачать файл';
                setState('error', error instanceof ExportError ? error.userMessage : NETWORK_ERROR);
                setProgress(0);
                var retry = modal.querySelector('[data-export-retry]');
                if (retry) retry.focus();
            });
    }

    document.addEventListener('click', function (event) {
        if (event.defaultPrevented || event.button !== 0) return;
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        var link = event.target.closest ? event.target.closest('a[data-export-download]') : null;
        if (!link) return;
        event.preventDefault();
        var menu = link.closest('[data-export-menu]');
        if (menu) menu.removeAttribute('open');
        start(link);
    });
})();
