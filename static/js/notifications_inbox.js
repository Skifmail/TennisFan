/**
 * Фильтр групп на странице уведомлений.
 * Кнопки только скрывают уже отрисованные карточки, запрос на сервер не уходит.
 */
(function () {
    "use strict";

    var root = document.querySelector("[data-inbox]");
    if (!root) {
        return;
    }

    var buttons = root.querySelectorAll("[data-inbox-filter]");
    var cards = root.querySelectorAll("[data-kind]");
    var empty = document.getElementById("inbox-filter-empty");
    if (!buttons.length || !cards.length) {
        return;
    }

    /**
     * Показать карточки выбранной группы и обновить состояние кнопок.
     *
     * @param {string} kind Ключ группы или "all".
     */
    function applyFilter(kind) {
        var visible = 0;
        cards.forEach(function (card) {
            var show = kind === "all" || card.getAttribute("data-kind") === kind;
            card.hidden = !show;
            if (show) {
                visible += 1;
            }
        });
        if (empty) {
            empty.hidden = visible !== 0;
        }
        buttons.forEach(function (button) {
            var active = button.getAttribute("data-inbox-filter") === kind;
            button.classList.toggle("is-active", active);
            button.setAttribute("aria-pressed", active ? "true" : "false");
        });
    }

    buttons.forEach(function (button) {
        button.addEventListener("click", function () {
            applyFilter(button.getAttribute("data-inbox-filter") || "all");
        });
    });
}());
