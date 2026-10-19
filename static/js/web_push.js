/** Управление анонимной подпиской OneSignal без автоматического запроса разрешения. */
(function () {
    'use strict';

    const configElement = document.getElementById('web-push-config');
    const button = document.getElementById('web-push-toggle');
    const status = document.getElementById('web-push-status');
    const iosHelp = document.getElementById('web-push-ios-help');
    const sdkScript = document.getElementById('onesignal-sdk');
    const SDK_TIMEOUT_MS = 15000;

    if (!configElement || !button || !status || window.tennisFanWebPushStarted) return;
    window.tennisFanWebPushStarted = true;

    const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) ||
        (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    const isStandalone = window.matchMedia('(display-mode: standalone)').matches ||
        navigator.standalone === true;

    if (isIOS && !isStandalone) {
        if (iosHelp) iosHelp.hidden = false;
        status.textContent = 'Для уведомлений сначала добавьте сайт на экран «Домой».';
        return;
    }

    function unavailable() {
        button.disabled = true;
        status.textContent = 'Сервис уведомлений временно недоступен. Обновите страницу позже.';
    }

    const loadingTimeout = window.setTimeout(unavailable, SDK_TIMEOUT_MS);
    if (sdkScript) sdkScript.addEventListener('error', unavailable);

    window.OneSignalDeferred = window.OneSignalDeferred || [];
    window.OneSignalDeferred.push(async function (OneSignal) {
        try {
            if (!window.isSecureContext || !OneSignal.Notifications.isPushSupported()) {
                status.textContent = 'Этот браузер не поддерживает push-уведомления. Попробуйте другой браузер.';
                button.disabled = true;
                return;
            }

            const config = JSON.parse(configElement.textContent);
            await OneSignal.init({
                appId: config.appId,
                notifyButton: { enable: false },
                welcomeNotification: { disable: true },
                promptOptions: {
                    slidedown: { prompts: [{ type: 'push', autoPrompt: false }] },
                },
            });

            const subscription = OneSignal.User.PushSubscription;
            let busy = false;

            function update() {
                button.disabled = busy;
                button.textContent = 'Включить уведомления';
                if (window.Notification && Notification.permission === 'denied') {
                    status.textContent = 'Уведомления запрещены. Разрешите их в настройках браузера или устройства.';
                    button.disabled = true;
                } else if (subscription.optedIn && subscription.id) {
                    status.textContent = 'Уведомления включены.';
                    button.textContent = 'Отключить уведомления';
                } else if (subscription.optedIn) {
                    status.textContent = 'Подписка подключается. Дождитесь подтверждения.';
                    button.disabled = true;
                } else {
                    status.textContent = 'Уведомления выключены.';
                }
            }

            OneSignal.Notifications.addEventListener('permissionChange', update);
            subscription.addEventListener('change', update);
            button.addEventListener('click', async function () {
                if (busy) return;
                busy = true;
                button.disabled = true;
                try {
                    if (subscription.optedIn && subscription.id) {
                        await subscription.optOut();
                    } else {
                        // Вызов до первого await сохраняет прямое действие пользователя для iOS.
                        if (!OneSignal.Notifications.permission) {
                            await OneSignal.Notifications.requestPermission();
                        }
                        if (OneSignal.Notifications.permission) await subscription.optIn();
                    }
                    busy = false;
                    update();
                } catch (_error) {
                    busy = false;
                    update();
                    status.textContent = 'Не удалось изменить подписку. Попробуйте ещё раз.';
                }
            });
            update();
        } catch (_error) {
            unavailable();
        } finally {
            window.clearTimeout(loadingTimeout);
        }
    });
})();
