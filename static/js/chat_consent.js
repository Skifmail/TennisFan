/* Независимые согласия: доступный dialog и обычная страница без JavaScript. */
(function () {
    "use strict";
    const dialog = document.getElementById("chat-join-dialog");
    let origin = null;
    let pending = null;
    function initialize(form) {
        const submit = form.querySelector('button[type="submit"]');
        const required = Array.from(form.querySelectorAll('input[type="checkbox"][required]'));
        const disclosure = form.querySelector('input[name="disclosure"]');
        const options = form.querySelector("[data-disclosure-fields]");
        const categories = Array.from(options.querySelectorAll('input[type="checkbox"]'));
        const optionalFields = options.querySelectorAll("input, textarea");
        function update() {
            const enabled = disclosure.checked;
            options.hidden = !enabled;
            disclosure.setAttribute("aria-expanded", String(enabled));
            optionalFields.forEach((input) => {
                input.disabled = !enabled;
                if (!enabled) {
                    if (input.type === "checkbox") input.checked = false;
                    else input.value = "";
                }
            });
            submit.disabled = !required.every((input) => input.checked)
                || (enabled && !categories.some((input) => input.checked));
        }
        form.addEventListener("change", update);
        form.addEventListener("submit", (event) => {
            update();
            if (submit.disabled) event.preventDefault();
            else submit.disabled = true;
        });
        update();
    }
    document.querySelectorAll("[data-consent-form]").forEach(initialize);
    if (!dialog || typeof dialog.showModal !== "function") return;
    dialog.addEventListener("close", () => {
        dialog.replaceChildren();
        if (origin && origin.isConnected) origin.focus({preventScroll: true});
    });
    dialog.addEventListener("click", (event) => {
        if (event.target.closest("[data-consent-close], [data-consent-cancel]")) {
            event.preventDefault();
            dialog.close();
        }
    });
    document.addEventListener("click", async (event) => {
        const trigger = event.target.closest("[data-chat-join]");
        if (!trigger || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button !== 0) return;
        event.preventDefault();
        if (pending) pending.abort();
        const controller = new AbortController();
        pending = controller;
        origin = trigger;
        trigger.setAttribute("aria-busy", "true");
        const timeout = window.setTimeout(() => controller.abort(), 10000);
        try {
            const response = await fetch(trigger.href, {
                headers: {"X-Requested-With": "XMLHttpRequest"}, credentials: "same-origin",
                cache: "no-store", signal: controller.signal,
            });
            if (!response.ok || response.redirected || !response.headers.get("Content-Type").includes("text/html")) throw new Error("Форма недоступна");
            const html = await response.text();
            if (pending !== controller) return;
            dialog.innerHTML = html;
            const form = dialog.querySelector("[data-consent-form]");
            if (!form) throw new Error("Некорректная форма");
            initialize(form);
            dialog.showModal();
        } catch (error) {
            // При ошибке сети или устаревшем браузере остаётся серверная форма.
            if (pending === controller) window.location.assign(trigger.href);
        } finally {
            window.clearTimeout(timeout);
            trigger.removeAttribute("aria-busy");
            if (pending === controller) pending = null;
        }
    });
})();
