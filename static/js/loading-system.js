/**
 * Unified premium loading system.
 */
(function () {
    "use strict";

    var ACTIVE_BUTTONS = new WeakSet();
    var PAGE_LOADER_ID = "global-fullscreen-loader";
    var PAGE_SPINNER_ID = "page-transition-spinner";

    function createSpinner(sizeClass, tennisVariant) {
        var spinner = document.createElement("span");
        spinner.className = "loading-spinner " + (sizeClass || "spinner-sm");
        if (tennisVariant) {
            spinner.classList.add("loading-spinner--tennis-ball");
        }
        spinner.setAttribute("role", "status");
        spinner.setAttribute("aria-live", "polite");
        spinner.setAttribute("aria-label", "Загрузка");
        return spinner;
    }

    function getOrCreateOverlay() {
        var overlay = document.getElementById(PAGE_LOADER_ID);
        if (overlay) {
            return overlay;
        }

        overlay = document.createElement("div");
        overlay.id = PAGE_LOADER_ID;
        overlay.className = "fullscreen-loader";
        overlay.setAttribute("aria-hidden", "true");

        var panel = document.createElement("div");
        panel.className = "fullscreen-loader__panel";
        panel.setAttribute("role", "status");
        panel.setAttribute("aria-live", "polite");
        panel.setAttribute("aria-busy", "true");

        var spinner = createSpinner("spinner-lg", true);
        var text = document.createElement("span");
        text.className = "fullscreen-loader__text";
        text.textContent = "Загрузка...";

        panel.appendChild(spinner);
        panel.appendChild(text);
        overlay.appendChild(panel);
        document.body.appendChild(overlay);
        return overlay;
    }

    function getOrCreatePageSpinner() {
        var el = document.getElementById(PAGE_SPINNER_ID);
        if (el) {
            return el;
        }
        el = document.createElement("div");
        el.id = PAGE_SPINNER_ID;
        el.className = "page-transition-spinner";
        var spinner = createSpinner("spinner-md", true);
        el.appendChild(spinner);
        document.body.appendChild(el);
        return el;
    }

    function showLoader(message) {
        var overlay = getOrCreateOverlay();
        var textNode = overlay.querySelector(".fullscreen-loader__text");
        if (textNode && message) {
            textNode.textContent = message;
        }
        overlay.classList.add("is-visible");
        overlay.setAttribute("aria-hidden", "false");
        document.body.classList.add("has-fullscreen-loader");
        return overlay;
    }

    function hideLoader() {
        var overlay = document.getElementById(PAGE_LOADER_ID);
        if (!overlay) {
            return;
        }
        overlay.classList.remove("is-visible");
        overlay.setAttribute("aria-hidden", "true");
        document.body.classList.remove("has-fullscreen-loader");
    }

    function showPageSpinner() {
        var el = getOrCreatePageSpinner();
        el.classList.add("is-visible");
    }

    function hidePageSpinner() {
        var el = document.getElementById(PAGE_SPINNER_ID);
        if (!el) {
            return;
        }
        el.classList.remove("is-visible");
    }

    function resolveLoadingText(button, customText) {
        if (customText) {
            return customText;
        }
        var fromDataset = button.getAttribute("data-loading-text");
        if (fromDataset) {
            return fromDataset;
        }
        var currentText = (button.textContent || "").trim();
        if (!currentText) {
            return "Загрузка...";
        }
        return currentText + "...";
    }

    function setButtonLoading(button, customText) {
        if (!button || ACTIVE_BUTTONS.has(button)) {
            return;
        }

        var minWidth = button.offsetWidth;
        button.dataset.loadingOriginalHtml = button.innerHTML;
        button.dataset.loadingOriginalMinWidth = button.style.minWidth || "";
        button.style.minWidth = String(minWidth) + "px";

        var content = document.createElement("span");
        content.className = "btn-loading-content";
        content.appendChild(createSpinner("spinner-sm", false));

        var label = document.createElement("span");
        label.className = "btn-loading-label";
        label.textContent = resolveLoadingText(button, customText);
        content.appendChild(label);

        button.innerHTML = "";
        button.appendChild(content);
        button.classList.add("is-loading");
        button.disabled = true;
        button.setAttribute("aria-busy", "true");
        ACTIVE_BUTTONS.add(button);
    }

    function resetButtonLoading(button) {
        if (!button || !ACTIVE_BUTTONS.has(button)) {
            return;
        }
        if (button.dataset.loadingOriginalHtml !== undefined) {
            button.innerHTML = button.dataset.loadingOriginalHtml;
        }
        button.classList.remove("is-loading");
        button.disabled = false;
        button.removeAttribute("aria-busy");
        button.style.minWidth = button.dataset.loadingOriginalMinWidth || "";
        delete button.dataset.loadingOriginalHtml;
        delete button.dataset.loadingOriginalMinWidth;
        ACTIVE_BUTTONS.delete(button);
    }

    function isOverlayPreferred(form, submitter) {
        if (form.hasAttribute("data-loader-overlay")) {
            return true;
        }
        if (submitter && submitter.classList.contains("btn-pay")) {
            return true;
        }
        if (form.id === "register-form" || form.classList.contains("auth-login-form")) {
            return true;
        }
        return false;
    }

    function shouldSkipAutoLoading(form, submitter) {
        if (form.hasAttribute("data-disable-auto-loading")) {
            return true;
        }
        if (submitter && submitter.hasAttribute("data-disable-auto-loading")) {
            return true;
        }
        return false;
    }

    function wireFormSubmitLoading() {
        document.addEventListener("submit", function (event) {
            var form = event.target;
            if (!(form instanceof HTMLFormElement)) {
                return;
            }

            var submitter = event.submitter || form.querySelector('button[type="submit"],input[type="submit"]');
            if (!(submitter instanceof HTMLElement)) {
                return;
            }
            if (shouldSkipAutoLoading(form, submitter)) {
                return;
            }

            setTimeout(function () {
                if (event.defaultPrevented) {
                    return;
                }
                setButtonLoading(submitter);
                form.setAttribute("aria-busy", "true");
                if (isOverlayPreferred(form, submitter)) {
                    showLoader("Пожалуйста, подождите...");
                }
            }, 0);
        });
    }

    function isInternalLink(element) {
        if (!(element instanceof HTMLAnchorElement)) {
            return false;
        }
        if (!element.href) {
            return false;
        }
        if (element.target === "_blank") {
            return false;
        }
        if (element.hasAttribute("download")) {
            return false;
        }
        if (element.hasAttribute("data-no-page-spinner")) {
            return false;
        }

        var rawHref = element.getAttribute("href") || "";
        if (!rawHref || rawHref.charAt(0) === "#") {
            return false;
        }
        if (rawHref.toLowerCase().indexOf("javascript:") === 0) {
            return false;
        }

        var url;
        try {
            url = new URL(element.href, window.location.href);
        } catch (e) {
            return false;
        }

        if (url.origin !== window.location.origin) {
            return false;
        }
        if (url.pathname === window.location.pathname && url.search === window.location.search) {
            return false;
        }
        return true;
    }

    function wirePageUnloadLoader() {
        window.addEventListener("beforeunload", function () {
            var activeElement = document.activeElement;
            if (activeElement && isInternalLink(activeElement)) {
                showPageSpinner();
            }
        });
        window.addEventListener("pageshow", function () {
            hidePageSpinner();
        });
    }

    function init() {
        wireFormSubmitLoading();
        wirePageUnloadLoader();
    }

    window.showLoader = showLoader;
    window.hideLoader = hideLoader;
    window.setButtonLoading = setButtonLoading;
    window.resetButtonLoading = resetButtonLoading;
    window.LoadingUX = {
        showLoader: showLoader,
        hideLoader: hideLoader,
        setButtonLoading: setButtonLoading,
        resetButtonLoading: resetButtonLoading,
        showPageSpinner: showPageSpinner,
        hidePageSpinner: hidePageSpinner,
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
