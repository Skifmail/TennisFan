/**
 * Виджет чата обратной связи: плавающая кнопка и виджет чата в правом нижнем углу.
 * Отправка в Telegram админу; ответы подгружаются из API.
 * Стиль JivoSite/Intercom - неинтрузивный виджет без backdrop.
 */
(function () {
    "use strict";

    var widget = document.getElementById("feedback-widget");
    if (!widget) return;

    var btn = document.getElementById("feedback-widget-btn");
    var chatWidget = document.getElementById("feedback-chat-widget");
    var closeBtn = document.getElementById("feedback-chat-close");
    var messagesList = document.getElementById("feedback-messages-list");
    var messagesContainer = document.getElementById("feedback-messages-container");
    var messageInput = document.getElementById("feedback-message-input");
    var sendBtn = document.getElementById("feedback-send-btn");
    var formError = document.getElementById("feedback-form-error");
    var guestForm = document.getElementById("feedback-guest-form");
    var badge = document.getElementById("feedback-widget-badge");
    var welcomeMessage = document.getElementById("feedback-welcome-message");
    var buttonLabel = btn ? btn.querySelector(".feedback-widget__label") : null;

    // Поля для гостей
    var guestNameInput = document.getElementById("feedback-guest-name");
    var guestTelegramInput = document.getElementById("feedback-guest-telegram");
    var guestContactInput = document.getElementById("feedback-guest-contact");

    var submitUrl = widget.getAttribute("data-submit-url");
    var threadsUrl = widget.getAttribute("data-threads-url");
    var updateMessageUrl = widget.getAttribute("data-update-message-url");
    var unreadCountUrl = widget.getAttribute("data-unread-count-url");
    var adminUnreadCountUrl = widget.getAttribute("data-admin-unread-count-url");
    var adminSupportUrl = widget.getAttribute("data-admin-support-url");
    var csrfToken = widget.getAttribute("data-csrf");
    var isAuth = widget.getAttribute("data-is-authenticated") === "1";
    var isPlatformAdmin = widget.getAttribute("data-is-platform-admin") === "1";

    // Состояние чата
    var chatState = {
        isOpen: false,
        messages: [],
        guestInfoCollected: false,
        pollingInterval: null,
        unreadInterval: null,
        lastMessageId: null,
    };

    function getCookie(name) {
        var match = document.cookie.match(new RegExp("(?:^|; )" + name.replace(/([.$?*|{}()[\]\\/+^])/g, "\\$1") + "=([^;]*)"));
        return match ? decodeURIComponent(match[1]) : null;
    }

    function escapeHtml(s) {
        if (!s) return "";
        var div = document.createElement("div");
        div.textContent = s;
        return div.innerHTML;
    }

    function formatTime(dateStr) {
        if (!dateStr) return "";
        try {
            var d = new Date(dateStr);
            var now = new Date();
            var diffMs = now - d;
            var diffMins = Math.floor(diffMs / 60000);

            // Если меньше минуты - "только что"
            if (diffMins < 1) return "только что";
            // Если меньше часа - "N мин назад"
            if (diffMins < 60) return diffMins + " мин назад";
            // Если сегодня - время
            if (d.toDateString() === now.toDateString()) {
                return d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
            }
            // Иначе дата и время
            return d.toLocaleDateString("ru-RU", {
                day: "2-digit",
                month: "2-digit",
                hour: "2-digit",
                minute: "2-digit",
            });
        } catch (e) {
            return "";
        }
    }

    function openChat() {
        if (!chatWidget) return;
        chatState.isOpen = true;
        chatWidget.setAttribute("aria-hidden", "false");
        document.body.style.overflow = ""; // Не блокируем скролл страницы

        // Загружаем историю сообщений (для всех пользователей)
        if (threadsUrl) {
            loadMessages();
            startPolling();
        }

        // Показываем форму для гостя только если еще не собирали информацию
        if (!isAuth && !chatState.guestInfoCollected) {
            if (guestForm) {
                guestForm.classList.add("feedback-chat-widget__guest-form--visible");
            }
        }

        // Фокус на поле ввода
        if (messageInput) {
            setTimeout(function () {
                messageInput.focus();
            }, 100);
        }
    }

    function closeChat() {
        if (!chatWidget) return;
        chatState.isOpen = false;
        chatWidget.setAttribute("aria-hidden", "true");
        stopPolling();
    }

    function toggleChat() {
        if (chatState.isOpen) {
            closeChat();
        } else {
            openChat();
        }
    }

    function loadMessages() {
        if (!threadsUrl) return;
        if (!messagesList) return;

        var xhr = new XMLHttpRequest();
        xhr.open("GET", threadsUrl);
        xhr.onload = function () {
            try {
                var data = JSON.parse(xhr.responseText);
                var threads = data.threads || [];
                chatState.messages = [];

                // Собираем все сообщения из всех threads в один массив
                threads.forEach(function (thread) {
                    if (thread.messages && thread.messages.length) {
                        thread.messages.forEach(function (msg) {
                            chatState.messages.push({
                                id: msg.id,
                                text: msg.text,
                                isFromAdmin: msg.is_from_admin,
                                createdAt: msg.created_at,
                                isEdited: !!msg.is_edited,
                                editedAt: msg.edited_at || null,
                                canEdit: !!msg.can_edit,
                            });
                            if (!chatState.lastMessageId || msg.id > chatState.lastMessageId) {
                                chatState.lastMessageId = msg.id;
                            }
                        });
                    }
                });

                // Сортируем по времени
                chatState.messages.sort(function (a, b) {
                    return new Date(a.createdAt) - new Date(b.createdAt);
                });

                renderMessages();
                updateUnreadBadge(0);
            } catch (e) {
                console.error("Failed to load messages:", e);
            }
        };
        xhr.onerror = function () {
            console.error("Error loading messages");
        };
        xhr.send();
    }

    function renderMessages() {
        if (!messagesList) return;

        // Скрываем welcome message если есть сообщения
        if (chatState.messages.length > 0 && welcomeMessage) {
            welcomeMessage.style.display = "none";
        } else if (chatState.messages.length === 0 && welcomeMessage) {
            welcomeMessage.style.display = "block";
        }

        var html = "";
        chatState.messages.forEach(function (msg) {
            var isUser = !msg.isFromAdmin;
            var messageClass = isUser ? "feedback-chat-widget__message--user" : "feedback-chat-widget__message--admin";
            var timeStr = formatTime(msg.createdAt);
            var authorLabel = isUser ? "Вы" : "Администратор";
            var bubbleStyle = isUser
                ? "background: rgba(0, 131, 116, 0.30); border: 1px solid rgba(0, 210, 180, 0.45); color: #ffffff;"
                : "background: rgba(166, 130, 74, 0.28); border: 1px solid rgba(166, 130, 74, 0.55);";
            var canEdit = isUser && !!msg.canEdit;

            html += '<div class="feedback-chat-widget__message ' + messageClass + '" data-message-id="' + String(msg.id) + '" style="margin-bottom:12px;border-radius:12px;padding:10px 12px;border:1px solid rgba(255,255,255,0.12);background:rgba(4, 48, 43, 0.45);">';
            html += '<div class="feedback-chat-widget__message-time" style="margin-bottom:4px;font-weight:700;">' + authorLabel + "</div>";
            html += '<div class="feedback-chat-widget__message-bubble" style="' + bubbleStyle + '">' + escapeHtml(msg.text) + "</div>";
            if (canEdit) {
                html += '<button type="button" class="btn btn-ghost feedback-chat-widget__edit-btn" data-edit-message-id="' + String(msg.id) + '" style="margin-top:6px;padding:4px 8px;font-size:12px;">Редактировать</button>';
            }
            if (timeStr) {
                html += '<div class="feedback-chat-widget__message-time" style="margin-top:8px;">' + escapeHtml(authorLabel) + " · " + escapeHtml(timeStr);
                if (msg.isEdited) {
                    html += ' · изменено';
                }
                html += "</div>";
            }
            html += "</div>";
        });

        messagesList.innerHTML = (welcomeMessage && chatState.messages.length === 0 ? welcomeMessage.outerHTML : "") + html;

        // Прокрутка вниз
        scrollToBottom();
    }

    function scrollToBottom() {
        if (messagesContainer) {
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
        }
    }

    function addMessage(text, isFromAdmin, messageId, createdAt) {
        var newMsg = {
            id: messageId || Date.now(),
            text: text,
            isFromAdmin: isFromAdmin || false,
            createdAt: createdAt || new Date().toISOString(),
        };

        chatState.messages.push(newMsg);
        if (messageId && (!chatState.lastMessageId || messageId > chatState.lastMessageId)) {
            chatState.lastMessageId = messageId;
        }

        renderMessages();
    }

    function sendMessage() {
        if (!messageInput || !submitUrl) return;

        var message = (messageInput.value || "").trim();
        if (!message) return;

        // Для гостей проверяем имя
        if (!isAuth && !chatState.guestInfoCollected) {
            if (!guestNameInput || !(guestNameInput.value || "").trim()) {
                if (formError) {
                    formError.textContent = "Введите ваше имя.";
                    formError.style.display = "block";
                }
                return;
            }
        }

        if (formError) formError.style.display = "none";

        // Optimistic update - добавляем сообщение сразу
        addMessage(message, false);

        // Очищаем поле ввода
        messageInput.value = "";
        if (sendBtn) sendBtn.disabled = true;

        // Собираем данные для отправки
        var payload = {
            message: message,
        };

        // Данные гостя (только при первом сообщении)
        if (!isAuth && !chatState.guestInfoCollected) {
            if (guestNameInput) {
                payload.guest_name = (guestNameInput.value || "").trim();
            }
            if (guestContactInput) {
                payload.guest_contact = (guestContactInput.value || "").trim();
            }
            if (guestTelegramInput) {
                var tgUsername = (guestTelegramInput.value || "").trim().replace(/^@/, "");
                if (tgUsername) {
                    payload.guest_telegram_username = tgUsername;
                }
            }
            chatState.guestInfoCollected = true;
            // Скрываем форму гостя
            if (guestForm) {
                guestForm.classList.remove("feedback-chat-widget__guest-form--visible");
            }
        }

        // Отправка на сервер
        var token = csrfToken || getCookie("csrftoken");
        var xhr = new XMLHttpRequest();
        xhr.open("POST", submitUrl);
        xhr.setRequestHeader("Content-Type", "application/json");
        xhr.setRequestHeader("X-CSRFToken", token || "");
        xhr.onload = function () {
            if (sendBtn) sendBtn.disabled = false;
            try {
                var data = JSON.parse(xhr.responseText);
                if (data.success) {
                    // Обновляем последнее сообщение с реальным ID если есть
                    if (data.message_id && chatState.messages.length > 0) {
                        var lastMsg = chatState.messages[chatState.messages.length - 1];
                        if (lastMsg && (!lastMsg.id || lastMsg.id >= Date.now() - 1000)) {
                            lastMsg.id = data.message_id;
                            if (!chatState.lastMessageId || data.message_id > chatState.lastMessageId) {
                                chatState.lastMessageId = data.message_id;
                            }
                        }
                    }

                    // Начинаем polling для получения новых сообщений
                    if (threadsUrl) {
                        startPolling();
                    }
                } else {
                    // Удаляем последнее сообщение при ошибке
                    chatState.messages.pop();
                    renderMessages();
                    if (formError) {
                        formError.textContent = data.error || "Ошибка отправки.";
                        formError.style.display = "block";
                    }
                }
            } catch (err) {
                // Удаляем последнее сообщение при ошибке
                chatState.messages.pop();
                renderMessages();
                if (formError) {
                    formError.textContent = "Ошибка отправки.";
                    formError.style.display = "block";
                }
            }
        };
        xhr.onerror = function () {
            if (sendBtn) sendBtn.disabled = false;
            // Удаляем последнее сообщение при ошибке
            chatState.messages.pop();
            renderMessages();
            if (formError) {
                formError.textContent = "Ошибка сети.";
                formError.style.display = "block";
            }
        };
        xhr.send(JSON.stringify(payload));
    }

    function startPolling() {
        // Останавливаем предыдущий polling если есть
        stopPolling();

        // Polling каждые 30 секунд для новых сообщений
        if (!threadsUrl) return;

        chatState.pollingInterval = setInterval(function () {
            if (!chatState.isOpen) return;

            var xhr = new XMLHttpRequest();
            xhr.open("GET", threadsUrl);
            xhr.onload = function () {
                try {
                    var data = JSON.parse(xhr.responseText);
                    var threads = data.threads || [];
                    var newMessages = [];
            var hasUpdates = false;

                    threads.forEach(function (thread) {
                        if (thread.messages && thread.messages.length) {
                            thread.messages.forEach(function (msg) {
                                // Проверяем, есть ли это сообщение уже в нашем списке
                        var exists = chatState.messages.some(function (m) {
                                    return m.id === msg.id;
                                });
                                if (!exists) {
                                    newMessages.push({
                                        id: msg.id,
                                        text: msg.text,
                                        isFromAdmin: msg.is_from_admin,
                                        createdAt: msg.created_at,
                                isEdited: !!msg.is_edited,
                                editedAt: msg.edited_at || null,
                                canEdit: !!msg.can_edit,
                                    });
                        } else {
                            var existingMessage = chatState.messages.find(function (m) {
                                return m.id === msg.id;
                            });
                            if (
                                existingMessage
                                && (
                                    existingMessage.text !== msg.text
                                    || existingMessage.isEdited !== !!msg.is_edited
                                    || existingMessage.canEdit !== !!msg.can_edit
                                )
                            ) {
                                existingMessage.text = msg.text;
                                existingMessage.isEdited = !!msg.is_edited;
                                existingMessage.editedAt = msg.edited_at || null;
                                existingMessage.canEdit = !!msg.can_edit;
                                hasUpdates = true;
                            }
                                }
                            });
                        }
                    });

                    // Добавляем новые сообщения
                    if (newMessages.length > 0 || hasUpdates) {
                        newMessages.forEach(function (msg) {
                            chatState.messages.push(msg);
                            if (!chatState.lastMessageId || msg.id > chatState.lastMessageId) {
                                chatState.lastMessageId = msg.id;
                            }
                        });
                        // Сортируем по времени
                        chatState.messages.sort(function (a, b) {
                            return new Date(a.createdAt) - new Date(b.createdAt);
                        });
                        renderMessages();
                    }
                } catch (e) {
                    console.error("Polling error:", e);
                }
            };
            xhr.send();
        }, 30000); // 30 секунд
    }

    function stopPolling() {
        if (chatState.pollingInterval) {
            clearInterval(chatState.pollingInterval);
            chatState.pollingInterval = null;
        }
    }

    function updateUnreadBadge(count) {
        if (!badge) return;
        var safeCount = Math.max(0, Number(count) || 0);
        if (safeCount > 0) {
            badge.hidden = false;
            badge.textContent = safeCount > 99 ? "99+" : String(safeCount);
        } else {
            badge.hidden = true;
            badge.textContent = "0";
        }

        if (isPlatformAdmin && buttonLabel) {
            var adminLabel = safeCount > 0 ? "Есть обращения" : "Сообщений нет";
            buttonLabel.textContent = adminLabel;
            if (btn) {
                btn.setAttribute("aria-label", adminLabel);
            }
        }
    }

    function fetchUnreadCount() {
        var targetUnreadCountUrl = isPlatformAdmin ? adminUnreadCountUrl : unreadCountUrl;
        if (!targetUnreadCountUrl) return;
        var xhr = new XMLHttpRequest();
        xhr.open("GET", targetUnreadCountUrl);
        xhr.onload = function () {
            try {
                var data = JSON.parse(xhr.responseText || "{}");
                if (isPlatformAdmin || !chatState.isOpen) {
                    updateUnreadBadge(data.count || 0);
                }
            } catch (e) {
                console.error("Unread count parse error:", e);
                if (isPlatformAdmin) {
                    updateUnreadBadge(0);
                }
            }
        };
        xhr.onerror = function () {
            if (isPlatformAdmin) {
                updateUnreadBadge(0);
            }
        };
        xhr.send();
    }

    function startUnreadPolling() {
        if (!unreadCountUrl) return;
        stopUnreadPolling();
        fetchUnreadCount();
        chatState.unreadInterval = setInterval(fetchUnreadCount, 30000);
    }

    function stopUnreadPolling() {
        if (chatState.unreadInterval) {
            clearInterval(chatState.unreadInterval);
            chatState.unreadInterval = null;
        }
    }

    // Обработчики событий
    if (btn) {
        if (isPlatformAdmin) {
            btn.addEventListener("click", function () {
                if (!adminSupportUrl) return;
                window.location.assign(adminSupportUrl);
            });
        } else {
            btn.addEventListener("click", toggleChat);
        }
    }
    if (closeBtn) {
        closeBtn.addEventListener("click", closeChat);
    }

    // Отправка по Enter
    if (messageInput) {
        messageInput.addEventListener("keydown", function (e) {
            if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });

        // Включаем кнопку отправки когда есть текст
        messageInput.addEventListener("input", function () {
            if (sendBtn) {
                sendBtn.disabled = !(messageInput.value || "").trim();
            }
        });
    }

    if (sendBtn) {
        sendBtn.addEventListener("click", sendMessage);
        sendBtn.disabled = true; // Начинаем с отключенной кнопки
    }

    function openEditModal(initialText, onSubmit) {
        var overlay = document.createElement("div");
        overlay.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center;z-index:12000;padding:16px;";

        var modal = document.createElement("div");
        modal.style.cssText = "width:min(560px,100%);background:#062d29;border:1px solid rgba(255,255,255,.14);border-radius:14px;padding:16px;box-shadow:0 20px 40px rgba(0,0,0,.45);";

        var title = document.createElement("div");
        title.textContent = "Редактировать сообщение";
        title.style.cssText = "font-weight:700;color:#fff;margin-bottom:10px;";

        var textarea = document.createElement("textarea");
        textarea.value = initialText || "";
        textarea.maxLength = 2000;
        textarea.style.cssText = "width:100%;min-height:110px;resize:vertical;background:rgba(0,0,0,.2);color:#fff;border:1px solid rgba(255,255,255,.24);border-radius:10px;padding:10px;";

        var actions = document.createElement("div");
        actions.style.cssText = "display:flex;gap:10px;justify-content:flex-end;margin-top:12px;";

        var cancelBtn = document.createElement("button");
        cancelBtn.type = "button";
        cancelBtn.className = "btn btn-ghost";
        cancelBtn.textContent = "Отмена";

        var saveBtn = document.createElement("button");
        saveBtn.type = "button";
        saveBtn.className = "btn btn-primary";
        saveBtn.textContent = "Сохранить";

        actions.appendChild(cancelBtn);
        actions.appendChild(saveBtn);
        modal.appendChild(title);
        modal.appendChild(textarea);
        modal.appendChild(actions);
        overlay.appendChild(modal);
        document.body.appendChild(overlay);
        textarea.focus();
        textarea.setSelectionRange(textarea.value.length, textarea.value.length);

        function closeModal() {
            if (overlay && overlay.parentNode) {
                overlay.parentNode.removeChild(overlay);
            }
        }

        cancelBtn.addEventListener("click", closeModal);
        overlay.addEventListener("click", function (event) {
            if (event.target === overlay) closeModal();
        });
        textarea.addEventListener("keydown", function (event) {
            if (event.key === "Escape") {
                event.preventDefault();
                closeModal();
            }
            if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
                event.preventDefault();
                saveBtn.click();
            }
        });

        saveBtn.addEventListener("click", function () {
            var nextText = (textarea.value || "").trim();
            if (!nextText || nextText === (initialText || "")) {
                closeModal();
                return;
            }
            closeModal();
            onSubmit(nextText);
        });
    }

    if (messagesList) {
        messagesList.addEventListener("click", function (event) {
            var editButton = event.target.closest("[data-edit-message-id]");
            if (!editButton || !updateMessageUrl) return;
            var messageId = Number(editButton.getAttribute("data-edit-message-id") || 0);
            if (!messageId) return;
            var message = chatState.messages.find(function (item) {
                return item.id === messageId;
            });
            if (!message || !message.canEdit || message.isFromAdmin) return;
            openEditModal(message.text || "", function (newText) {
                var token = csrfToken || getCookie("csrftoken");
                var xhr = new XMLHttpRequest();
                xhr.open("POST", updateMessageUrl);
                xhr.setRequestHeader("Content-Type", "application/json");
                xhr.setRequestHeader("X-CSRFToken", token || "");
                xhr.onload = function () {
                    try {
                        var data = JSON.parse(xhr.responseText || "{}");
                        if (xhr.status >= 200 && xhr.status < 300 && data.success) {
                            message.text = newText;
                            message.isEdited = true;
                            message.editedAt = data.edited_at || null;
                            renderMessages();
                            return;
                        }
                        if (formError) {
                            formError.textContent = data.error || "Не удалось отредактировать сообщение.";
                            formError.style.display = "block";
                        }
                    } catch (e) {
                        if (formError) {
                            formError.textContent = "Не удалось отредактировать сообщение.";
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
                xhr.send(JSON.stringify({ message_id: messageId, text: newText }));
            });
        });
    }

    if (!isPlatformAdmin && window.location.search.indexOf("open_support=1") !== -1) {
        openChat();
    }

    startUnreadPolling();

    window.addEventListener("beforeunload", function () {
        stopPolling();
        stopUnreadPolling();
    });
})();
