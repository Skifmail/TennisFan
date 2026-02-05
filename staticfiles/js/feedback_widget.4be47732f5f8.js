/**
 * Виджет обратной связи: плавающая кнопка и модальное окно с формой.
 * Отправка в Telegram админу; ответы подгружаются из API.
 */
(function () {
    "use strict";

    var widget = document.getElementById("feedback-widget");
    if (!widget) return;

    var btn = document.getElementById("feedback-widget-btn");
    var modal = document.getElementById("feedback-modal");
    var backdrop = document.getElementById("feedback-modal-backdrop");
    var closeBtn = document.getElementById("feedback-modal-close");
    var form = document.getElementById("feedback-form");
    var formWrap = document.getElementById("feedback-form-wrap");
    var successBlock = document.getElementById("feedback-success");
    var formError = document.getElementById("feedback-form-error");
    var threadsList = document.getElementById("feedback-threads-list");
    var threadsWrap = document.getElementById("feedback-threads-wrap");

    var submitUrl = widget.getAttribute("data-submit-url");
    var threadsUrl = widget.getAttribute("data-threads-url");
    var csrfToken = widget.getAttribute("data-csrf");
    var isAuth = widget.getAttribute("data-is-authenticated") === "1";

    function getCookie(name) {
        var match = document.cookie.match(new RegExp("(?:^|; )" + name.replace(/([.$?*|{}()[\]\\/+^])/g, "\\$1") + "=([^;]*)"));
        return match ? decodeURIComponent(match[1]) : null;
    }

    function openModal() {
        if (!modal) return;
        modal.setAttribute("aria-hidden", "false");
        modal.classList.add("feedback-modal--open");
        document.body.style.overflow = "hidden";
        if (isAuth && threadsUrl) loadThreads();
    }

    function closeModal() {
        if (!modal) return;
        modal.setAttribute("aria-hidden", "true");
        modal.classList.remove("feedback-modal--open");
        document.body.style.overflow = "";
    }

    function loadThreads() {
        if (!threadsList || !threadsUrl) return;
        threadsList.innerHTML = "<p class=\"text-muted\">Загрузка…</p>";
        var xhr = new XMLHttpRequest();
        xhr.open("GET", threadsUrl);
        xhr.onload = function () {
            try {
                var data = JSON.parse(xhr.responseText);
                renderThreads(data.threads || []);
            } catch (e) {
                threadsList.innerHTML = "<p class=\"text-muted\">Не удалось загрузить обращения.</p>";
            }
        };
        xhr.onerror = function () {
            threadsList.innerHTML = "<p class=\"text-muted\">Ошибка загрузки.</p>";
        };
        xhr.send();
    }

    function formatDate(iso) {
        if (!iso) return "";
        var d = new Date(iso);
        return d.toLocaleDateString("ru-RU", {
            day: "2-digit",
            month: "2-digit",
            year: "numeric",
            hour: "2-digit",
            minute: "2-digit"
        });
    }

    function renderThreads(threads) {
        if (!threadsList) return;
        if (!threads || threads.length === 0) {
            threadsList.innerHTML = "<p class=\"text-muted\">Пока нет обращений. Ответы приходят в Telegram.</p>";
            return;
        }
        var html = "";
        threads.forEach(function (t) {
            html += "<div class=\"feedback-thread\">";
            if (t.messages && t.messages.length) {
                t.messages.forEach(function (m) {
                    var label = m.is_from_admin ? "Поддержка" : "Вы";
                    html += "<div class=\"feedback-thread__meta\">" + label + " · " + formatDate(m.created_at) + "</div>";
                    html += "<div class=\"feedback-thread__message\">" + escapeHtml(m.text) + "</div>";
                });
            } else {
                html += "<div class=\"feedback-thread__meta\">#" + (t.id || "") + " · " + formatDate(t.created_at) + "</div>";
                html += "<div class=\"feedback-thread__message\">" + escapeHtml(t.message || "") + "</div>";
                if (t.replies && t.replies.length) {
                    t.replies.forEach(function (r) {
                        html += "<div class=\"feedback-thread__reply\"><strong>Ответ:</strong> " + escapeHtml(r.text) + " <span class=\"feedback-thread__reply-date\">" + formatDate(r.created_at) + "</span></div>";
                    });
                }
            }
            html += "</div>";
        });
        threadsList.innerHTML = html;
    }

    function escapeHtml(s) {
        if (!s) return "";
        var div = document.createElement("div");
        div.textContent = s;
        return div.innerHTML;
    }

    if (btn) {
        btn.addEventListener("click", openModal);
    }
    if (backdrop) backdrop.addEventListener("click", closeModal);
    if (closeBtn) closeBtn.addEventListener("click", closeModal);

    if (form && submitUrl) {
        form.addEventListener("submit", function (e) {
            e.preventDefault();
            var messageEl = form.querySelector("[name=message]");
            var subjectEl = form.querySelector("[name=subject]");
            var message = (messageEl && messageEl.value || "").trim();
            if (!message) {
                if (formError) {
                    formError.textContent = "Введите сообщение.";
                    formError.style.display = "block";
                }
                return;
            }
            if (formError) formError.style.display = "none";

            var token = csrfToken || getCookie("csrftoken");
            var xhr = new XMLHttpRequest();
            xhr.open("POST", submitUrl);
            xhr.setRequestHeader("Content-Type", "application/json");
            xhr.setRequestHeader("X-CSRFToken", token || "");
            xhr.onload = function () {
                try {
                    var data = JSON.parse(xhr.responseText);
                    if (data.success) {
                        if (formWrap) formWrap.style.display = "none";
                        if (successBlock) {
                            successBlock.style.display = "block";
                            var msg = data.message || "Сообщение отправлено. Ответ придёт в Telegram.";
                            var link = data.telegram_binding_url;
                            successBlock.innerHTML = "<p>" + escapeHtml(msg) + "</p>" +
                                (link ? "<p><a href=\"" + escapeHtml(link) + "\" class=\"btn btn-primary\" target=\"_blank\" rel=\"noopener noreferrer\">Привязать аккаунт в Telegram</a></p>" : "");
                        }
                        if (messageEl) messageEl.value = "";
                        if (subjectEl) subjectEl.value = "";
                        if (threadsList && threadsUrl) loadThreads();
                    } else {
                        if (formError) {
                            formError.textContent = data.error || "Ошибка отправки.";
                            formError.style.display = "block";
                        }
                    }
                } catch (err) {
                    if (formError) {
                        formError.textContent = "Ошибка отправки.";
                        formError.style.display = "block";
                    }
                }
            };
            xhr.onerror = function () {
                if (formError) {
                    formError.textContent = "Ошибка сети.";
                    formError.style.display = "block";
                }
            };
            xhr.send(JSON.stringify({
                message: message,
                subject: (subjectEl && subjectEl.value || "").trim()
            }));
        });
    }
})();
