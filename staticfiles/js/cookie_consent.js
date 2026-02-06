/**
 * Cookie consent banner — соответствие требованиям РФ и GDPR.
 * До согласия аналитика не загружается. После выбора пользователя
 * сохраняем cookie и показываем/скрываем баннер.
 *
 * Использование аналитики только после согласия:
 *   if (window.cookieConsentAccepted && window.cookieConsentAccepted()) {
 *     // загрузить Google Analytics, Яндекс.Метрику и т.п.
 *     loadAnalytics();
 *   }
 * Либо подписаться на событие:
 *   window.addEventListener('cookie_consent_accepted', function () { loadAnalytics(); });
 * При отказе (кнопка «Отказаться») cookie_consent=false, аналитика не загружается.
 */
(function () {
    'use strict';

    var COOKIE_NAME = 'cookie_consent';
    var COOKIE_MAX_AGE = 31536000; // 1 год в секундах
    var COOKIE_PATH = '/';

    function getCookie(name) {
        var match = document.cookie.match(new RegExp('(?:^|; )' + name.replace(/[.$?*|{}()[\]\\/+^]/g, '\\$1') + '=([^;]*)'));
        return match ? decodeURIComponent(match[1]) : null;
    }

    function setCookie(name, value, maxAge, path) {
        document.cookie = name + '=' + encodeURIComponent(value) + '; max-age=' + maxAge + '; path=' + (path || '/') + '; SameSite=Lax';
    }

    /**
     * Возвращает true, если пользователь явно дал согласие на cookie (аналитика и т.п.).
     * Используйте перед загрузкой Google Analytics, Яндекс.Метрики и др.
     */
    function hasAccepted() {
        return getCookie(COOKIE_NAME) === 'true';
    }

    /**
     * Возвращает true, если пользователь явно отказался.
     */
    function hasRefused() {
        return getCookie(COOKIE_NAME) === 'false';
    }

    /**
     * Показ баннера только если выбор ещё не сделан.
     */
    function shouldShowBanner() {
        var value = getCookie(COOKIE_NAME);
        return value !== 'true' && value !== 'false';
    }

    function hideBanner() {
        var el = document.getElementById('cookie-consent-banner');
        if (el) {
            el.hidden = true;
            el.classList.remove('cookie-banner--visible');
        }
    }

    function showBanner() {
        var el = document.getElementById('cookie-consent-banner');
        if (el) {
            el.hidden = false;
            el.classList.add('cookie-banner--visible');
        }
    }

    function onAccept() {
        setCookie(COOKIE_NAME, 'true', COOKIE_MAX_AGE, COOKIE_PATH);
        hideBanner();
        if (typeof window.dispatchEvent === 'function') {
            window.dispatchEvent(new CustomEvent('cookie_consent_accepted'));
        }
        if (typeof window.onCookieConsentAccept === 'function') {
            window.onCookieConsentAccept();
        }
    }

    function onRefuse() {
        setCookie(COOKIE_NAME, 'false', COOKIE_MAX_AGE, COOKIE_PATH);
        hideBanner();
        if (typeof window.dispatchEvent === 'function') {
            window.dispatchEvent(new CustomEvent('cookie_consent_refused'));
        }
    }

    function init() {
        var banner = document.getElementById('cookie-consent-banner');
        var btnAccept = document.getElementById('cookie-consent-accept');
        var btnRefuse = document.getElementById('cookie-consent-refuse');

        if (!banner) return;

        if (shouldShowBanner()) {
            showBanner();
        } else {
            hideBanner();
        }

        if (btnAccept) {
            btnAccept.addEventListener('click', onAccept);
        }
        if (btnRefuse) {
            btnRefuse.addEventListener('click', onRefuse);
        }
    }

    window.cookieConsentAccepted = hasAccepted;
    window.cookieConsentRefused = hasRefused;

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
