/* Городской чат: безопасный текст, мгновенная доставка, спокойные взаимодействия. */
(function () {
    "use strict";
    const root = document.getElementById("city-chat");
    if (!root) return;
    const config = JSON.parse(document.getElementById("chat-config").textContent);
    const byId = (id) => document.getElementById(id);
    const list = byId("chat-messages");
    const viewport = byId("chat-window");
    const input = byId("chat-text");
    const send = byId("chat-send");
    const error = byId("chat-error");
    const notice = byId("chat-notice");
    const menu = byId("chat-message-menu");
    const settingsDialog = byId("chat-settings");
    const actionDialog = byId("chat-action-dialog");
    const confirmButton = byId("chat-action-confirm");
    const reportReason = byId("chat-report-reason");
    const csrf = root.querySelector("[name=csrfmiddlewaretoken]").value;
    const messages = new Map();
    const shown = new Set();
    const nodes = new Map();
    const storageKey = `city-chat:${config.userId}:${config.roomId}`;
    const RECONNECT_MAX_MS = 30000;
    const HEARTBEAT_MS = 25000;
    const REQUEST_TIMEOUT_MS = 15000;
    const SCROLL_MARGIN = 70;
    const EDIT_WINDOW_MS = 900000;
    const NOTICE_MS = 6000;
    const MAX_INPUT_HEIGHT = 128;
    const MODERATOR_BADGE_SIZE = 18;
    const SVG_NS = "http://www.w3.org/2000/svg";
    const supportsPopover = typeof menu.showPopover === "function";
    let cursor = 0;
    let latestId = 0;
    let oldestId = 0;
    let socket;
    let reconnectTimer;
    let noticeTimer;
    let reconnectDelay = 1000;
    let stopped = false;
    let initialized = false;
    let syncing = false;
    let syncAgain = false;
    let sending = false;
    let bannedUntil = null;
    let canModerate = config.canModerate;
    let replyId = null;
    let editId = null;
    let beforeEdit = null;
    let pending = null;
    let readId = 0;
    let readInFlight = false;
    let loadingHistory = false;
    let menuTrigger = null;
    let menuOpen = false;
    let currentAction = null;
    let transientError = "";
    let bannedNotice = false;
    const dialogOrigins = new WeakMap();

    function element(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function icon(name) {
        const svg = document.createElementNS(SVG_NS, "svg");
        svg.classList.add("chat-icon");
        svg.setAttribute("aria-hidden", "true");
        const use = document.createElementNS(SVG_NS, "use");
        use.setAttribute("href", `#chat-icon-${name}`);
        svg.append(use);
        return svg;
    }

    function showError(message) {
        error.textContent = message;
        error.hidden = !message;
    }

    function announce(message, persistent = false) {
        clearTimeout(noticeTimer);
        notice.textContent = message;
        notice.hidden = !message;
        if (message && !persistent) noticeTimer = setTimeout(() => { notice.hidden = true; }, NOTICE_MS);
    }

    function updateSend() {
        const banned = Boolean(bannedUntil && new Date(bannedUntil).getTime() > Date.now());
        send.disabled = !initialized || stopped || sending || banned || !input.value.trim();
        send.setAttribute("aria-busy", String(sending));
        if (banned && !bannedNotice) {
            bannedNotice = true;
            announce("Модератор временно ограничил отправку. Читать чат по-прежнему можно.", true);
        } else if (!banned && bannedNotice) {
            bannedNotice = false;
            announce("");
        }
    }

    function fitInput() {
        const nearBottom = atBottom();
        input.style.height = "auto";
        input.style.height = `${Math.min(input.scrollHeight, MAX_INPUT_HEIGHT)}px`;
        input.style.overflowY = input.scrollHeight > MAX_INPUT_HEIGHT ? "auto" : "hidden";
        const counter = byId("chat-character-count");
        counter.hidden = input.value.length < 1800;
        counter.textContent = `${input.value.length} / 2000`;
        updateSend();
        if (nearBottom) viewport.scrollTop = viewport.scrollHeight;
    }

    async function request(url, payload) {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
        try {
            const options = {credentials: "same-origin", cache: "no-store", signal: controller.signal};
            if (payload) {
                options.method = "POST";
                options.headers = {"Content-Type": "application/json", "X-CSRFToken": csrf};
                options.body = JSON.stringify(payload);
            }
            const response = await fetch(url, options);
            const data = await response.json();
            if (!response.ok) {
                if (response.status === 403 && ["forbidden", "consent_required"].includes(data.code) && !payload) stopAccess();
                throw new Error(data.error || "Не удалось выполнить запрос.");
            }
            return data;
        } catch (failure) {
            if (failure instanceof SyntaxError) throw new Error("Сессия истекла. Обновите страницу и войдите снова.");
            if (failure.name === "AbortError" || failure instanceof TypeError) {
                const networkError = new Error("Нет ответа сервера. Черновик сохранён — попробуйте ещё раз.");
                networkError.isNetwork = true;
                throw networkError;
            }
            throw failure;
        } finally { clearTimeout(timeout); }
    }

    function atBottom() {
        return viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight < SCROLL_MARGIN;
    }

    function persist() {
        // Не подменяем черновик незавершённым редактированием старого сообщения.
        const draft = editId && beforeEdit ? beforeEdit : {text: input.value, pending, replyId};
        try { sessionStorage.setItem(storageKey, JSON.stringify(draft)); } catch { /* Хранилище может быть отключено. */ }
    }

    function clearContext(restoreDraft = false) {
        if (restoreDraft && beforeEdit) {
            input.value = beforeEdit.text;
            pending = beforeEdit.pending;
            replyId = beforeEdit.replyId;
        } else replyId = null;
        editId = null;
        beforeEdit = null;
        byId("chat-context").hidden = true;
        send.setAttribute("aria-label", "Отправить");
        send.title = "Отправить сообщение";
        byId("chat-send-icon").setAttribute("href", "#chat-icon-up");
        if (restoreDraft && replyId && messages.has(replyId)) setContext(replyId, false);
        fitInput();
    }

    function setContext(id, editing) {
        const message = messages.get(id);
        if (!message || message.is_deleted) return;
        if (editing && !editId) beforeEdit = {text: input.value, pending, replyId};
        if (!editing && editId) clearContext(true);
        editId = editing ? id : null;
        replyId = editing ? null : id;
        if (editing) input.value = message.text;
        byId("chat-context").hidden = false;
        byId("chat-context-text").textContent = `${editing ? "Редактирование" : `Ответ ${message.author}`}: ${message.text.slice(0, 100)}`;
        send.setAttribute("aria-label", editing ? "Сохранить" : "Отправить");
        send.title = editing ? "Сохранить изменения" : "Отправить сообщение";
        byId("chat-send-icon").setAttribute("href", editing ? "#chat-icon-check" : "#chat-icon-up");
        fitInput();
        persist();
        input.focus({preventScroll: true});
    }

    function storeMessage(message) {
        const current = messages.get(message.id);
        if (!current || message.revision >= current.revision) messages.set(message.id, message);
    }

    function updateModeratorRoles(ids) {
        if (!Array.isArray(ids)) return false;
        const moderators = new Set(ids);
        let changed = false;
        messages.forEach((message, id) => {
            const isModerator = moderators.has(message.author_id);
            if (message.author_is_moderator !== isModerator) {
                messages.set(id, {...message, author_is_moderator: isModerator});
                changed = true;
            }
        });
        return changed;
    }

    function closeMenu(restoreFocus = false) {
        if (supportsPopover && menu.matches(":popover-open")) menu.hidePopover();
        if (!supportsPopover) menu.hidden = true;
        menuOpen = false;
        if (menuTrigger) {
            menuTrigger.setAttribute("aria-expanded", "false");
            if (restoreFocus && menuTrigger.isConnected) menuTrigger.focus({preventScroll: true});
        }
    }

    function openDialog(dialog, origin) {
        closeMenu();
        if (settingsDialog.open && dialog !== settingsDialog) settingsDialog.close();
        dialogOrigins.set(dialog, origin || byId("chat-info"));
        dialog.showModal();
    }

    document.querySelectorAll(".chat-dialog").forEach((dialog) => {
        dialog.addEventListener("close", () => {
            const origin = dialogOrigins.get(dialog);
            if (origin && origin.isConnected) origin.focus({preventScroll: true});
            else input.focus({preventScroll: true});
        });
        dialog.querySelectorAll("[data-close-dialog]").forEach((button) => button.addEventListener("click", () => dialog.close()));
        dialog.addEventListener("click", (event) => {
            if (event.target !== dialog) return;
            const rect = dialog.getBoundingClientRect();
            if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
        });
    });

    function ask({title, description, confirm, destructive = false, report = false, execute, origin}) {
        currentAction = {execute, report, busy: false};
        byId("chat-action-title").textContent = title;
        byId("chat-action-description").textContent = description;
        confirmButton.textContent = confirm;
        confirmButton.classList.toggle("is-destructive", destructive);
        confirmButton.disabled = report;
        confirmButton.setAttribute("aria-busy", "false");
        byId("chat-report-field").hidden = !report;
        reportReason.value = "";
        reportReason.required = report;
        byId("chat-action-error").hidden = true;
        openDialog(actionDialog, origin);
        if (report) reportReason.focus({preventScroll: true});
    }

    reportReason.addEventListener("input", () => {
        if (currentAction && currentAction.report && !currentAction.busy) confirmButton.disabled = !reportReason.value.trim();
    });
    byId("chat-action-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        const operation = currentAction;
        if (!operation || operation.busy || (operation.report && !reportReason.value.trim())) return;
        operation.busy = true;
        confirmButton.disabled = true;
        confirmButton.setAttribute("aria-busy", "true");
        try {
            await operation.execute(reportReason.value.trim());
            if (currentAction === operation && actionDialog.open) actionDialog.close();
            await synchronize();
        } catch (failure) {
            if (currentAction === operation && actionDialog.open) {
                byId("chat-action-error").textContent = failure.message;
                byId("chat-action-error").hidden = false;
            } else showError(failure.message);
        } finally {
            operation.busy = false;
            if (currentAction === operation) {
                confirmButton.disabled = false;
                confirmButton.setAttribute("aria-busy", "false");
            }
        }
    });

    function menuItem(label, symbol, handler, destructive = false) {
        const button = element("button", destructive ? "is-destructive" : "");
        button.type = "button";
        button.setAttribute("role", "menuitem");
        button.append(icon(symbol), element("span", "", label));
        button.addEventListener("click", () => {
            closeMenu();
            handler();
        });
        return button;
    }

    function positionMenu() {
        if (!menuTrigger || !menuTrigger.isConnected) { closeMenu(); return; }
        const rect = menuTrigger.getBoundingClientRect();
        const box = menu.getBoundingClientRect();
        const gap = 7;
        const above = rect.bottom + box.height + gap > window.innerHeight - 12;
        menu.style.left = `${Math.max(12, Math.min(rect.right - box.width, window.innerWidth - box.width - 12))}px`;
        menu.style.top = `${Math.max(12, above ? rect.top - box.height - gap : rect.bottom + gap)}px`;
        menu.style.setProperty("--chat-menu-origin", above ? "bottom right" : "top right");
    }

    function openMessageMenu(id, trigger) {
        if (menuOpen && trigger === menuTrigger) { closeMenu(true); return; }
        closeMenu();
        const message = messages.get(id);
        if (!message || message.is_deleted) return;
        menuTrigger = trigger;
        const own = message.author_id === config.userId;
        const buttons = [menuItem("Ответить", "reply", () => setContext(id, false))];
        if (own && Date.now() - new Date(message.created_at).getTime() <= EDIT_WINDOW_MS) buttons.push(menuItem("Изменить", "edit", () => setContext(id, true)));
        if (own || canModerate) buttons.push(menuItem("Удалить", "trash", () => ask({
            title: "Удалить сообщение?", description: "Оно исчезнет из переписки у всех участников. Отменить это действие нельзя.", confirm: "Удалить сообщение", destructive: true, origin: trigger,
            execute: () => request(config.actionUrl, {action: "delete", message_id: id}),
        }), true));
        if (!own) buttons.push(menuItem("Пожаловаться", "flag", () => ask({
            title: "Пожаловаться на сообщение", description: "Жалобу увидит только администрация. Расскажите, что нарушает правила чата.", confirm: "Отправить жалобу", report: true, origin: trigger,
            execute: async (reason) => {
                await request(config.actionUrl, {action: "report", message_id: id, reason});
                announce("Жалоба отправлена администрации.");
            },
        })));
        if (canModerate && !own && message.author_id) buttons.push(menuItem("Ограничить участника", "shield", () => ask({
            title: "Ограничить отправку?", description: `${message.author} не сможет писать в этом чате в течение суток. Читать переписку по-прежнему можно.`, confirm: "Ограничить на сутки", destructive: true, origin: trigger,
            execute: async () => {
                await request(config.actionUrl, {action: "ban", user_id: message.author_id, banned: true});
                announce("Ограничение установлено. Снять его можно в панели администратора.");
            },
        })));
        menu.replaceChildren(...buttons);
        menuOpen = true;
        trigger.setAttribute("aria-expanded", "true");
        if (supportsPopover) menu.showPopover();
        else menu.hidden = false;
        positionMenu();
        buttons[0].focus({preventScroll: true});
    }

    if (!supportsPopover) menu.hidden = true;
    menu.addEventListener("toggle", (event) => {
        if (event.newState === "closed") {
            menuOpen = false;
            if (menuTrigger) menuTrigger.setAttribute("aria-expanded", "false");
            markRead();
        }
    });
    menu.addEventListener("keydown", (event) => {
        const buttons = [...menu.querySelectorAll("button")];
        const current = buttons.indexOf(document.activeElement);
        if (event.key === "Escape" || event.key === "Tab") {
            closeMenu(true);
            if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); }
        } else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
            event.preventDefault();
            const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : (current + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length;
            buttons[next].focus();
        }
    });
    document.addEventListener("pointerdown", (event) => {
        if (!supportsPopover && menuOpen && !menu.contains(event.target) && event.target !== menuTrigger) closeMenu();
    });
    viewport.addEventListener("scroll", () => { if (menuOpen) closeMenu(); }, {passive: true});
    window.addEventListener("resize", () => { if (menuOpen) closeMenu(); }, {passive: true});

    function dateLabel(date) {
        const today = new Date();
        const yesterday = new Date();
        yesterday.setDate(today.getDate() - 1);
        if (date.toDateString() === today.toDateString()) return "Сегодня";
        if (date.toDateString() === yesterday.toDateString()) return "Вчера";
        return date.toLocaleDateString("ru-RU", {day: "numeric", month: "long", ...(date.getFullYear() !== today.getFullYear() ? {year: "numeric"} : {})});
    }

    function messageNode(message) {
        const own = message.author_id === config.userId;
        const moderator = message.author_is_moderator === true;
        const item = element("li", "city-chat-message");
        item.dataset.own = String(own);
        item.dataset.deleted = String(message.is_deleted);
        item.dataset.moderator = String(moderator);
        item.dataset.messageId = String(message.id);
        const avatar = element("span", "chat-message-avatar");
        avatar.setAttribute("aria-hidden", "true");
        const initial = Array.from(message.author)[0] || "И";
        avatar.textContent = initial;
        if (message.avatar) {
            const img = element("img", "");
            img.alt = "";
            img.loading = "lazy";
            img.addEventListener("error", () => { avatar.textContent = initial; }, { once: true });
            img.src = message.avatar;
            avatar.replaceChildren(img);
        }
        item.append(avatar);
        const column = element("div", "chat-message-column");
        if (!own || moderator) {
            const identity = element("div", "chat-message-identity");
            const authorRow = element("div", "chat-message-author-row");
            const author = element(message.profile_url ? "a" : "span", "chat-message-author", message.author);
            if (message.profile_url) author.href = message.profile_url;
            authorRow.append(author);
            if (moderator) {
                const badge = element("img", "chat-moderator-badge");
                badge.src = config.moderatorBadgeUrl;
                badge.alt = "";
                badge.width = MODERATOR_BADGE_SIZE;
                badge.height = MODERATOR_BADGE_SIZE;
                badge.setAttribute("aria-hidden", "true");
                authorRow.append(badge);
            }
            identity.append(authorRow);
            if (moderator) identity.append(element("span", "chat-moderator-label", "Модератор"));
            column.append(identity);
        }
        const bubble = element("div", "chat-bubble");
        if (message.reply_to && !message.is_deleted) {
            const original = messages.get(message.reply_to.id);
            const preview = original ? (original.is_deleted ? "Сообщение удалено" : original.text.slice(0, 120)) : message.reply_to.text;
            const quote = element("blockquote", "chat-message-reply");
            quote.append(element("strong", "", original ? original.author : message.reply_to.author), element("span", "", preview));
            bubble.append(quote);
        }
        bubble.append(element("p", "city-chat-message-text", message.is_deleted ? "Сообщение удалено" : message.text));
        column.append(bubble);
        const meta = element("div", "chat-message-meta");
        const time = element("time", "", new Date(message.created_at).toLocaleTimeString("ru-RU", {hour: "2-digit", minute: "2-digit"}));
        time.dateTime = message.created_at;
        time.title = new Date(message.created_at).toLocaleString("ru-RU");
        meta.append(time);
        if (message.is_edited && !message.is_deleted) meta.append(element("span", "", "· изменено"));
        column.append(meta);
        item.append(column);
        if (!message.is_deleted) {
            const options = element("button", "chat-icon-button chat-message-options");
            options.type = "button";
            options.setAttribute("aria-label", "Действия с сообщением");
            options.setAttribute("aria-haspopup", "menu");
            options.setAttribute("aria-controls", "chat-message-menu");
            options.setAttribute("aria-expanded", "false");
            options.append(icon("more"));
            options.addEventListener("click", () => openMessageMenu(message.id, options));
            item.append(options);
        }
        return item;
    }

    function render() {
        // Не пересоздаём неизменившиеся узлы: сохраняются фокус, выделение текста и меню.
        const desired = [];
        let previousDay = "";
        [...shown].sort((a, b) => a - b).forEach((id) => {
            const message = messages.get(id);
            const date = new Date(message.created_at);
            const day = date.toDateString();
            if (day !== previousDay) {
                const key = `date:${day}`;
                let divider = nodes.get(key);
                if (!divider) {
                    divider = element("li", "chat-date-divider", dateLabel(date));
                    nodes.set(key, divider);
                }
                desired.push(divider);
                previousDay = day;
            }
            const key = `message:${id}`;
            const original = message.reply_to ? messages.get(message.reply_to.id) : null;
            const signature = JSON.stringify([message, original ? [original.revision, original.text, original.is_deleted] : null]);
            let node = nodes.get(key);
            if (!node || node.dataset.signature !== signature) {
                const updated = messageNode(message);
                updated.dataset.signature = signature;
                if (node && node.isConnected) {
                    if (menuTrigger && node.contains(menuTrigger)) closeMenu();
                    const heldFocus = node.contains(document.activeElement);
                    node.replaceWith(updated);
                    if (heldFocus) {
                        const replacement = updated.querySelector("button");
                        (replacement || input).focus({preventScroll: true});
                    }
                }
                node = updated;
                nodes.set(key, node);
            }
            desired.push(node);
        });
        let next = list.firstChild;
        desired.forEach((node) => {
            if (node !== next) list.insertBefore(node, next);
            next = node.nextSibling;
        });
        while (next) { const following = next.nextSibling; next.remove(); next = following; }
        byId("chat-empty").hidden = shown.size > 0;
        byId("chat-empty-title").textContent = "Начните разговор";
        byId("chat-empty-description").textContent = "Найдите компанию для игры, обсудите корты или просто познакомьтесь.";
    }

    async function markRead() {
        if (initialized && !menuOpen && atBottom()) byId("chat-new").hidden = true;
        if (!initialized || stopped || document.hidden || menuOpen || !atBottom() || !latestId || readInFlight || readId >= latestId) return;
        const target = latestId;
        readInFlight = true;
        try {
            await request(config.actionUrl, {action: "read", message_id: target});
            readId = target;
            byId("chat-new").hidden = true;
            const unread = byId("chat-room-unread");
            if (unread) unread.hidden = true;
        } catch (failure) { showError(failure.message); }
        finally { readInFlight = false; }
    }

    async function synchronize() {
        if (!initialized || stopped) return;
        if (syncing) { syncAgain = true; return; }
        syncing = true;
        try {
            do {
                syncAgain = false;
                const nearBottom = atBottom();
                const data = await request(`${config.syncUrl}?after=${cursor}`);
                let newIncoming = false;
                data.events.forEach((event) => {
                    const message = event.message;
                    if (message.id > latestId) {
                        shown.add(message.id);
                        latestId = message.id;
                        if (message.author_id !== config.userId && !message.is_deleted) newIncoming = true;
                    }
                    storeMessage(message);
                });
                cursor = data.cursor;
                bannedUntil = data.banned_until;
                canModerate = data.can_moderate;
                const rolesChanged = updateModeratorRoles(data.moderator_ids);
                byId("chat-rules").textContent = data.rules;
                updateSend();
                if (data.events.length || rolesChanged) {
                    render();
                    if (nearBottom && !menuOpen) viewport.scrollTop = viewport.scrollHeight;
                    else if (newIncoming) byId("chat-new").hidden = false;
                }
                if (data.has_more) syncAgain = true;
            } while (syncAgain && !stopped);
            if (transientError && error.textContent === transientError) showError("");
            transientError = "";
            await markRead();
        } catch (failure) {
            if (failure.isNetwork) transientError = failure.message;
            showError(failure.message);
        } finally { syncing = false; }
    }

    function status(text, connected) {
        byId("chat-status").textContent = text;
        byId("chat-status").dataset.connected = String(connected);
    }

    function stopAccess() {
        stopped = true;
        clearTimeout(reconnectTimer);
        if (socket) socket.close();
        status("Доступ завершён", false);
        updateSend();
        showError("Доступ завершён: участие, документы или условия изменились. Обновите страницу, чтобы продолжить.");
    }

    function connect() {
        if (stopped) return;
        status("Подключение…", false);
        socket = new WebSocket(`${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}${config.socketPath}`);
        socket.onmessage = (event) => {
            let data;
            try { data = JSON.parse(event.data); } catch { return; }
            if (data.type === "ready") { reconnectDelay = 1000; status("На связи", true); synchronize(); }
            else if (data.type === "refresh" || data.type === "pong") synchronize();
        };
        socket.onclose = (event) => {
            if (event.code === 4403) { stopAccess(); return; }
            if (stopped) return;
            status("Переподключение…", false);
            reconnectTimer = setTimeout(connect, reconnectDelay + Math.random() * 500);
            reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX_MS);
        };
        socket.onerror = () => status("Восстанавливаем связь…", false);
    }

    byId("chat-compose").addEventListener("submit", async (event) => {
        event.preventDefault();
        if (send.disabled || !input.value.trim()) return;
        showError("");
        sending = true;
        updateSend();
        const editing = editId;
        const submittedText = input.value;
        const submittedReply = replyId;
        try {
            if (editing) await request(config.actionUrl, {action: "edit", message_id: editing, text: submittedText});
            else {
                if (!pending || pending.text !== submittedText || pending.reply_to_id !== submittedReply) pending = {action: "send", text: submittedText, client_id: crypto.randomUUID(), reply_to_id: submittedReply};
                persist();
                const result = await request(config.actionUrl, pending);
                storeMessage(result.message);
                shown.add(result.message.id);
                latestId = Math.max(latestId, result.message.id);
                pending = null;
            }
            // Не стираем новый текст, набранный во время запроса.
            if (input.value === submittedText && editId === editing && replyId === submittedReply) {
                input.value = "";
                clearContext(Boolean(editing));
            }
            persist();
            fitInput();
            render();
            viewport.scrollTop = viewport.scrollHeight;
            await synchronize();
        } catch (failure) { showError(failure.message); persist(); }
        finally {
            sending = false;
            updateSend();
            if (document.activeElement === send) input.focus({preventScroll: true});
        }
    });
    input.addEventListener("input", () => { fitInput(); persist(); });
    input.addEventListener("keydown", (event) => {
        const desktopEnter = window.matchMedia("(hover: hover) and (pointer: fine)").matches && !event.shiftKey;
        if (event.key === "Enter" && !event.isComposing && (event.ctrlKey || event.metaKey || desktopEnter)) {
            event.preventDefault();
            byId("chat-compose").requestSubmit();
        } else if (event.key === "Escape" && (editId || replyId)) {
            clearContext(true);
            persist();
        }
    });
    byId("chat-cancel").addEventListener("click", () => { clearContext(true); persist(); input.focus({preventScroll: true}); });
    document.querySelectorAll("[data-open-chat-settings]").forEach((button) => {
        button.addEventListener("click", () => openDialog(settingsDialog, button));
    });
    const withdrawalForm = byId("chat-withdraw-form");
    withdrawalForm.addEventListener("submit", (event) => {
        event.preventDefault();
        ask({
            title: "Отозвать основные согласия?",
            description: "Вы будете исключены из этого чата. Ваши сообщения, имя и фото будут скрыты. Для возвращения нужно заново подтвердить обязательные пункты.",
            confirm: "Отозвать и выйти", destructive: true,
            origin: dialogOrigins.get(settingsDialog) || byId("chat-info"),
            execute: async () => { stopped = true; withdrawalForm.submit(); },
        });
    });
    byId("chat-leave").addEventListener("click", () => ask({
        title: "Покинуть чат?", description: "Вы сможете снова вступить в этот город в любое время. История переписки сохранится.", confirm: "Покинуть чат", destructive: true, origin: byId("chat-info"),
        execute: async () => { await request(config.actionUrl, {action: "leave"}); stopped = true; location.assign(config.indexUrl); },
    }));
    byId("chat-notifications").addEventListener("change", async (event) => {
        const toggle = event.target;
        toggle.disabled = true;
        byId("chat-settings-error").hidden = true;
        try { await request(config.actionUrl, {action: "notifications", enabled: toggle.checked}); }
        catch (failure) {
            toggle.checked = !toggle.checked;
            byId("chat-settings-error").textContent = failure.message;
            byId("chat-settings-error").hidden = false;
        }
        finally { toggle.disabled = false; }
    });
    byId("chat-history").addEventListener("click", async () => {
        if (!oldestId || loadingHistory) return;
        loadingHistory = true;
        byId("chat-history").disabled = true;
        try {
            const data = await request(`${config.historyUrl}?before=${oldestId}`);
            const height = viewport.scrollHeight;
            const scroll = viewport.scrollTop;
            data.messages.forEach((message) => { storeMessage(message); shown.add(message.id); });
            if (data.messages.length) oldestId = data.messages[0].id;
            render();
            byId("chat-history").hidden = !data.has_more;
            viewport.scrollTop = scroll + viewport.scrollHeight - height;
        } catch (failure) { showError(failure.message); }
        finally { loadingHistory = false; byId("chat-history").disabled = false; }
    });
    byId("chat-new").addEventListener("click", () => { viewport.scrollTop = viewport.scrollHeight; markRead(); });
    viewport.addEventListener("scroll", markRead, {passive: true});
    document.addEventListener("visibilitychange", () => { if (!document.hidden) synchronize(); });
    const heartbeat = setInterval(() => {
        if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({type: "ping"}));
        if (!document.hidden) synchronize();
    }, HEARTBEAT_MS);
    window.addEventListener("pagehide", () => { stopped = true; clearTimeout(reconnectTimer); clearTimeout(noticeTimer); clearInterval(heartbeat); if (socket) socket.close(); });
    window.addEventListener("pageshow", (event) => { if (event.persisted) location.reload(); });

    (async () => {
        try {
            const data = await request(config.historyUrl);
            data.messages.forEach((message) => { storeMessage(message); shown.add(message.id); latestId = Math.max(latestId, message.id); });
            oldestId = data.messages.length ? data.messages[0].id : 0;
            cursor = data.cursor;
            byId("chat-history").hidden = !data.has_more;
            try {
                const draft = JSON.parse(sessionStorage.getItem(storageKey) || "null");
                if (draft && typeof draft.text === "string") { input.value = draft.text; pending = draft.pending; replyId = draft.replyId; if (replyId && messages.has(replyId)) setContext(replyId, false); }
            } catch { /* Повреждённый черновик не мешает открыть чат. */ }
            initialized = true;
            fitInput();
            render();
            viewport.scrollTop = viewport.scrollHeight;
            await markRead();
            connect();
        } catch (failure) { showError(failure.message); status("Не удалось загрузить чат", false); }
    })();
})();
