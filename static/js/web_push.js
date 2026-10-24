/** Подписка устройства и проверенная идентичность без автоматического запроса разрешения. */
(function () {
    'use strict';

    const configElement = document.getElementById('web-push-config');
    const button = document.getElementById('web-push-toggle');
    const status = document.getElementById('web-push-status');
    const badge = document.getElementById('web-push-badge');
    const iosHelp = document.getElementById('web-push-ios-help');
    const androidHelp = document.getElementById('web-push-android-help');
    const sdkScript = document.getElementById('onesignal-sdk');
    const testButton = document.getElementById('web-push-test');
    const testStatus = document.getElementById('web-push-test-status');
    const SDK_TIMEOUT_MS = 15000;
    const DEFAULT_RETRY_SECONDS = 60;
    const MAX_RETRY_SECONDS = 300;
    const SECOND_MS = 1000;

    if (!configElement || !button || !status || window.tennisFanWebPushStarted) return;
    window.tennisFanWebPushStarted = true;

    const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) ||
        (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    const isStandalone = window.matchMedia('(display-mode: standalone)').matches ||
        navigator.standalone === true;
    let fallbackMode = null;

    function setFallback(mode, message, label) {
        fallbackMode = mode;
        button.hidden = false;
        button.classList.remove('hidden');
        button.disabled = false;
        button.textContent = label;
        status.textContent = message;
        status.dataset.state = 'error';
    }

    function hideControls(message) {
        fallbackMode = null;
        button.hidden = true;
        button.classList.add('hidden');
        status.textContent = message;
        status.dataset.state = 'error';
    }

    function unavailable() {
        setFallback(
            'reload',
            'Сервис уведомлений временно недоступен. Проверьте соединение и попробуйте ещё раз.',
            'Повторить'
        );
    }

    function deniedInstructions() {
        return isIOS
            ? 'Уведомления заблокированы. Разрешите их в настройках iPhone и нажмите «Проверить снова».'
            : 'Уведомления заблокированы. Разрешите их по инструкции ниже и нажмите «Проверить снова».';
    }

    function openAndroidHelp() {
        if (androidHelp && !isIOS) androidHelp.open = true;
    }

    button.addEventListener('click', function () {
        if (fallbackMode === 'help') {
            if (iosHelp) {
                iosHelp.hidden = false;
                iosHelp.open = true;
                iosHelp.scrollIntoView({block: 'nearest'});
            }
            return;
        }
        if (fallbackMode === 'reload') window.location.reload();
    });

    if (isIOS && androidHelp) {
        androidHelp.hidden = true;
        androidHelp.classList.add('hidden');
    }

    if (isIOS && !isStandalone) {
        if (iosHelp) {
            iosHelp.hidden = false;
            iosHelp.open = true;
        }
        hideControls('На iPhone и iPad push работают только из приложения, добавленного на экран «Домой».');
        return;
    }

    const loadingTimeout = window.setTimeout(unavailable, SDK_TIMEOUT_MS);
    if (sdkScript) sdkScript.addEventListener('error', unavailable);

    window.OneSignalDeferred = window.OneSignalDeferred || [];
    window.OneSignalDeferred.push(async function (OneSignal) {
        try {
            if (!window.isSecureContext ||
                !('Notification' in window) ||
                !('serviceWorker' in navigator) ||
                !('PushManager' in window) ||
                !OneSignal.Notifications.isPushSupported()) {
                hideControls('Этот браузер не поддерживает push-уведомления. Откройте сайт в Chrome или Safari.');
                return;
            }

            const config = JSON.parse(configElement.textContent);
            await OneSignal.init({
                appId: config.appId,
                serviceWorkerPath: 'OneSignalSDKWorker.js',
                serviceWorkerParam: {scope: '/'},
                notifyButton: { enable: false },
                welcomeNotification: { disable: true },
                // На iOS используем уже открытое окно PWA с переходом по ссылке.
                notificationClickHandlerMatch: 'origin',
                notificationClickHandlerAction: 'navigate',
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
            let failureMessage = '';
            let retryUntil = 0;
            let retryTimer = null;
            const manualCode = Boolean(config.codeRequestUrl && config.codeVerifyUrl);
            const codeForm = document.getElementById('web-push-code-form');
            const codeInput = document.getElementById('web-push-code');
            const codeSubmit = document.getElementById('web-push-code-submit');
            const codeStatus = document.getElementById('web-push-code-status');
            let codeRetryUntil = 0;
            let testBusy = false;
            let testRetryUntil = 0;
            let testTimer = null;

            class BindingError extends Error {
                constructor(code, httpStatus, retryAfter) {
                    super('notification binding failed');
                    this.code = code;
                    this.httpStatus = httpStatus;
                    const seconds = Number(retryAfter);
                    this.retrySeconds = Number.isFinite(seconds) && seconds > 0
                        ? Math.min(Math.ceil(seconds), MAX_RETRY_SECONDS) : DEFAULT_RETRY_SECONDS;
                }
            }

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
                let payload;
                try { payload = await response.json(); }
                catch (_error) { throw new BindingError('invalid_response', response.status, null); }
                if (!response.ok || response.redirected) {
                    throw new BindingError(payload.error, response.status, response.headers.get('Retry-After'));
                }
                return payload;
            }

            async function syncDevice(enableAccount) {
                if (config.notificationCenter && config.authenticated) {
                    const denied = Boolean(window.Notification && Notification.permission === 'denied');
                    const active = Boolean(subscription.optedIn && subscription.id && !denied);
                    // Неизвестное/ещё загружающееся состояние SDK — не команда отзыва.
                    // Явный запрет в ОС отзывает серверную привязку, чтобы не копить мёртвые адреса.
                    if (!active) {
                        if (denied && verifiedSubscriptionId && boundExternalId) {
                            try {
                                await api(config.deviceUrl, {externalId: boundExternalId, active: false});
                                verifiedSubscriptionId = null;
                                confirmationPending = false;
                            } catch (_error) {
                                // Следующая авторизованная синхронизация повторит отзыв.
                            }
                        }
                        return;
                    }
                    // Push может прийти раньше HTTP-ответа (или при его потере).
                    if (enableAccount === true && !manualCode) window.tennisFanPushHandoff?.beginWaiting();
                    const state = await api(manualCode && enableAccount === true ? config.codeRequestUrl : config.deviceUrl, {
                        externalId: boundExternalId,
                        subscriptionId: subscription.id,
                        active: active,
                        enableAccount: enableAccount === true,
                    });
                    accountPushEnabled = state.accountPushEnabled !== false;
                    verifiedSubscriptionId = state.active ? subscription.id : null;
                    if (state.active) window.tennisFanPushHandoff?.stopWaiting();
                    if (state.pending) {
                        confirmationPending = true;
                        if (manualCode && codeInput) codeInput.value = '';
                        if (manualCode && codeStatus) codeStatus.textContent = 'Новый код отправлен. Используйте последнее уведомление.';
                        if (!manualCode) window.tennisFanPushHandoff?.beginWaiting();
                    }
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
                confirmationPending = manualCode && identity.pendingCode === true;
                accountPushEnabled = identity.pushEnabled !== false;
                verifiedSubscriptionId = identity.verifiedSubscriptionId;
                await syncDevice(false);
            }

            function update() {
                let state = 'off';
                fallbackMode = null;
                button.hidden = false;
                button.classList.remove('hidden');
                button.disabled = busy;
                if (codeForm) codeForm.hidden = !manualCode || !confirmationPending;
                if (codeSubmit) codeSubmit.disabled = busy || Date.now() < codeRetryUntil;
                button.textContent = 'Включить уведомления';
                if (window.Notification && Notification.permission === 'denied') {
                    setFallback('reload', deniedInstructions(), 'Проверить снова');
                    openAndroidHelp();
                    state = 'error';
                } else if (config.notificationCenter && confirmationPending) {
                    status.textContent = manualCode
                        ? 'Введите шестизначный код из полученного push. Нажимать уведомление не нужно. Код действует 5 минут.'
                        : 'Разрешение на устройстве включено. Откройте полученный push и подтвердите подключение TennisFan. Если он не пришёл, повторите через минуту.';
                    button.textContent = manualCode ? 'Отправить новый код' : 'Отправить подтверждение повторно';
                    state = 'pending';
                } else if (verifiedSubscriptionId && !subscription.id) {
                    status.textContent = 'Загружаем состояние подключённого устройства…';
                    button.textContent = 'Проверить снова';
                    fallbackMode = 'reload';
                    state = 'loading';
                } else if (!accountPushEnabled) {
                    status.textContent = 'Push отключены в настройках аккаунта. Можно включить их этой кнопкой.';
                    state = 'off';
                } else if (config.notificationCenter && subscription.optedIn && verifiedSubscriptionId !== subscription.id) {
                    status.textContent = 'Push на устройстве разрешены. Подтвердите подключение к TennisFan для персональных уведомлений.';
                    button.textContent = manualCode ? 'Получить код подключения' : 'Подтвердить подключение';
                    state = 'pending';
                } else if (subscription.optedIn && subscription.id) {
                    status.textContent = 'Уведомления включены.';
                    button.textContent = 'Отключить уведомления';
                    state = 'connected';
                } else if (subscription.optedIn) {
                    status.textContent = 'Подписка подключается. Если это длится долго, проверьте разрешение и повторите.';
                    button.textContent = 'Проверить снова';
                    fallbackMode = 'reload';
                    state = 'pending';
                } else {
                    status.textContent = 'Уведомления выключены.';
                }
                if (failureMessage) {
                    status.textContent = failureMessage;
                    state = 'error';
                }
                status.dataset.state = state;
                if (badge) badge.hidden = state !== 'connected';
                if (testButton) {
                    const canTest = state === 'connected' && Boolean(config.testUrl);
                    testButton.hidden = !canTest;
                    testButton.classList.toggle('hidden', !canTest);
                    testButton.disabled = busy || testBusy || Date.now() < testRetryUntil;
                    if (testStatus && !canTest && testStatus.dataset.state !== 'error') {
                        testStatus.textContent = '';
                        delete testStatus.dataset.state;
                    }
                }
                const remaining = Math.ceil((retryUntil - Date.now()) / SECOND_MS);
                if (remaining > 0) {
                    button.disabled = true;
                    button.textContent = 'Повторить через ' + remaining + ' с';
                    status.textContent += ' Повторите через ' + remaining + ' с.';
                } else if (retryTimer !== null) {
                    window.clearInterval(retryTimer);
                    retryTimer = null;
                }
            }

            function reportError(error) {
                if (error instanceof BindingError && error.httpStatus === 429) {
                    failureMessage = manualCode ? 'Подождите перед повторной отправкой кода.' : 'Подождите перед повторной отправкой подтверждения.';
                } else if (error instanceof BindingError && error.code === 'confirmation_unavailable') {
                    failureMessage = 'Не удалось отправить подтверждение. Разрешение устройства сохранено, но подключение TennisFan ещё не подтверждено.';
                } else if (error instanceof BindingError && error.httpStatus === 409) {
                    failureMessage = 'Состояние подключения изменилось. Обновите страницу и повторите подключение.';
                } else {
                    failureMessage = 'Не удалось проверить подключение. Проверьте интернет. Попробуйте ещё раз.';
                }
                if (error instanceof BindingError && (error.httpStatus === 429 || error.code === 'confirmation_unavailable')) {
                    retryUntil = Date.now() + error.retrySeconds * SECOND_MS;
                    if (retryTimer === null) retryTimer = window.setInterval(update, SECOND_MS);
                }
                update();
            }

            if (manualCode && codeForm && codeInput && codeSubmit && codeStatus) {
                codeForm.addEventListener('submit', async event => {
                    event.preventDefault();
                    if (busy || Date.now() < codeRetryUntil) return;
                    busy = true;
                    failureMessage = '';
                    codeStatus.textContent = 'Проверяем код…';
                    update();
                    try {
                        const result = await api(config.codeVerifyUrl, {
                            externalId: boundExternalId, code: codeInput.value.trim(),
                        });
                        if (result.active !== true || typeof result.verifiedSubscriptionId !== 'string' || !result.verifiedSubscriptionId) {
                            throw new BindingError('invalid_response', 400, null);
                        }
                        accountPushEnabled = true;
                        verifiedSubscriptionId = result.verifiedSubscriptionId;
                        confirmationPending = false;
                        codeInput.value = '';
                        codeStatus.textContent = '';
                        window.tennisFanPushHandoff?.stopWaiting();
                        const preference = document.getElementById('id_push_enabled');
                        if (preference) preference.checked = true;
                        const preferences = document.querySelector('[data-notification-preferences]');
                        if (preferences) preferences.dataset.pushEnabled = 'true';
                    } catch (error) {
                        if (error instanceof BindingError && error.code === 'confirmation_attempts_exhausted') {
                            codeRetryUntil = Date.now() + error.retrySeconds * SECOND_MS;
                            codeStatus.textContent = 'Слишком много неверных попыток. Повторите через ' + error.retrySeconds + ' с.';
                            window.setTimeout(update, error.retrySeconds * SECOND_MS);
                        } else if (error instanceof BindingError && error.code === 'confirmation_invalid') {
                            codeStatus.textContent = 'Неверный код. Проверьте шесть цифр в последнем push.';
                        } else if (error instanceof BindingError && error.code === 'confirmation_expired') {
                            codeStatus.textContent = 'Код истёк или уже использован. Запросите новый код кнопкой выше.';
                        } else {
                            codeStatus.textContent = 'Не удалось проверить код. Попробуйте ещё раз.';
                        }
                    } finally {
                        busy = false;
                        update();
                    }
                });
            }

            if (testButton && testStatus) {
                testButton.addEventListener('click', async function () {
                    if (!config.testUrl || !verifiedSubscriptionId || testBusy) return;
                    testBusy = true;
                    testStatus.dataset.state = 'loading';
                    testStatus.textContent = 'Отправляем тестовое уведомление…';
                    update();
                    try {
                        await api(config.testUrl, {subscriptionId: verifiedSubscriptionId});
                        testStatus.dataset.state = 'success';
                        testStatus.textContent = 'Уведомление отправлено. Если оно не появилось в течение минуты, проверьте разрешения и ограничения батареи по инструкции выше.';
                    } catch (error) {
                        const retry = error instanceof BindingError ? error.retrySeconds : DEFAULT_RETRY_SECONDS;
                        if (error instanceof BindingError && error.httpStatus === 429) {
                            testRetryUntil = Date.now() + retry * SECOND_MS;
                            testStatus.textContent = 'Не отправляйте тесты слишком часто. Повторите через ' + retry + ' с.';
                            if (testTimer === null) {
                                testTimer = window.setInterval(function () {
                                    if (Date.now() >= testRetryUntil) {
                                        window.clearInterval(testTimer);
                                        testTimer = null;
                                    }
                                    update();
                                }, SECOND_MS);
                            }
                        } else if (error instanceof BindingError && error.code === 'device_not_found') {
                            testStatus.textContent = 'Это устройство не найдено среди подтверждённых. Подключите push заново.';
                        } else if (error instanceof BindingError && error.code === 'device_unreachable') {
                            testStatus.textContent = 'Устройство больше не принимает push. Подключите его заново.';
                            verifiedSubscriptionId = null;
                        } else {
                            testStatus.textContent = 'Не удалось отправить тестовое уведомление. Проверьте интернет и попробуйте ещё раз.';
                        }
                        testStatus.dataset.state = 'error';
                    } finally {
                        testBusy = false;
                        update();
                    }
                });
            }

            OneSignal.Notifications.addEventListener('permissionChange', update);
            subscription.addEventListener('change', function () {
                update();
                if (!busy) syncDevice(false).then(update).catch(error => {
                    if (!busy) reportError(error);
                });
            });
            button.addEventListener('click', async function () {
                if (busy || fallbackMode) return;
                const disabling = subscription.optedIn && subscription.id && accountPushEnabled &&
                    (!config.notificationCenter || verifiedSubscriptionId === subscription.id);
                if (disabling && document.getElementById('notification-disable-dialog')) {
                    if (!window.tennisFanConfirmPushDisable ||
                        !await window.tennisFanConfirmPushDisable('device')) return;
                }
                failureMessage = '';
                busy = true;
                button.disabled = true;
                try {
                    if (subscription.optedIn && subscription.id && accountPushEnabled &&
                        (!config.notificationCenter || verifiedSubscriptionId === subscription.id)) {
                        if (config.notificationCenter) {
                            await api(config.deviceUrl, {externalId: boundExternalId, active: false});
                            verifiedSubscriptionId = null;
                            confirmationPending = false;
                            window.tennisFanPushHandoff?.stopWaiting();
                        }
                        await subscription.optOut();
                    } else {
                        // Вызов до первого await сохраняет прямое действие пользователя для iOS.
                        if (!OneSignal.Notifications.permission) {
                            await OneSignal.Notifications.requestPermission();
                        }
                        if (OneSignal.Notifications.permission && !(subscription.optedIn && subscription.id)) {
                            await subscription.optIn();
                        }
                    }
                    await syncDevice(true);
                    busy = false;
                    update();
                } catch (error) {
                    busy = false;
                    reportError(error);
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
