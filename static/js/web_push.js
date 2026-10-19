/** Подписка устройства и проверенная идентичность без автоматического запроса разрешения. */
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
            let accountPushEnabled = true;
            let boundExternalId = null;
            let verifiedSubscriptionId = null;
            let confirmationPending = false;

            async function api(url, data) {
                const csrf = document.querySelector('[name=csrfmiddlewaretoken]');
                const cookie = document.cookie.split('; ').find(value => value.startsWith('csrftoken='));
                const response = await fetch(url, {
                    method: 'POST', credentials: 'same-origin', cache: 'no-store',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': csrf ? csrf.value : (cookie ? decodeURIComponent(cookie.split('=')[1]) : ''),
                    },
                    body: JSON.stringify(data),
                });
                if (!response.ok || response.redirected) throw new Error('notification binding failed');
                return response.json();
            }

            async function syncDevice(enableAccount) {
                if (config.notificationCenter && config.authenticated) {
                    const state = await api(config.deviceUrl, {
                        externalId: boundExternalId,
                        subscriptionId: subscription.id,
                        active: Boolean(subscription.optedIn && subscription.id && Notification.permission !== 'denied'),
                        enableAccount: enableAccount === true,
                    });
                    accountPushEnabled = state.accountPushEnabled !== false;
                    verifiedSubscriptionId = state.active ? subscription.id : null;
                    if (state.pending) confirmationPending = true;
                }
            }

            if (config.notificationCenter) {
                if (!config.authenticated) {
                    button.disabled = true;
                    status.textContent = 'Войдите в аккаунт, чтобы подключить персональные уведомления.';
                    return;
                }
                const identity = await api(config.identityUrl, {});
                boundExternalId = identity.externalId;
                accountPushEnabled = identity.pushEnabled !== false;
                verifiedSubscriptionId = identity.verifiedSubscriptionId;
                await syncDevice(false);
            }

            function update() {
                button.disabled = busy;
                button.textContent = 'Включить уведомления';
                if (window.Notification && Notification.permission === 'denied') {
                    status.textContent = 'Уведомления запрещены. Разрешите их в настройках браузера или устройства.';
                    button.disabled = true;
                } else if (config.notificationCenter && confirmationPending) {
                    status.textContent = 'Откройте полученный push и подтвердите подключение. Если он не пришёл, повторите через минуту.';
                    button.textContent = 'Отправить подтверждение повторно';
                } else if (!accountPushEnabled) {
                    status.textContent = 'Push отключены в настройках аккаунта. Можно включить их этой кнопкой.';
                } else if (config.notificationCenter && verifiedSubscriptionId !== subscription.id) {
                    status.textContent = 'Подтвердите это устройство, чтобы получать персональные уведомления.';
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
            subscription.addEventListener('change', function () {
                update();
                if (!busy) syncDevice(false).catch(unavailable);
            });
            button.addEventListener('click', async function () {
                if (busy) return;
                busy = true;
                button.disabled = true;
                try {
                    if (subscription.optedIn && subscription.id && accountPushEnabled &&
                        (!config.notificationCenter || verifiedSubscriptionId === subscription.id)) {
                        if (config.notificationCenter) {
                            await api(config.deviceUrl, {externalId: boundExternalId, active: false});
                            verifiedSubscriptionId = null;
                            confirmationPending = false;
                        }
                        await subscription.optOut();
                    } else {
                        // Вызов до первого await сохраняет прямое действие пользователя для iOS.
                        if (!OneSignal.Notifications.permission) {
                            await OneSignal.Notifications.requestPermission();
                        }
                        if (OneSignal.Notifications.permission) await subscription.optIn();
                    }
                    await syncDevice(true);
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
