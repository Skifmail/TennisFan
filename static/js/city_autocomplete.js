/**
 * Универсальное автодополнение для полей ввода города на сайте.
 * Подключается на всех страницах, находит поля по name="city", id/class содержащим "city".
 */

(function () {
    "use strict";

    var DEBOUNCE_MS = 300;
    var MIN_QUERY_LENGTH = 2;
    var MAX_RESULTS = 10;
    var API_URL = "/api/cities/";

    /**
     * Находит все input-поля, относящиеся к городу.
     * @returns {NodeListOf<HTMLInputElement>}
     */
    function findCityInputs() {
        var inputs = document.querySelectorAll('input[type="text"], input[type="search"]');
        var result = [];
        for (var i = 0; i < inputs.length; i++) {
            var el = inputs[i];
            var name = (el.getAttribute("name") || "").toLowerCase();
            var id = (el.getAttribute("id") || "").toLowerCase();
            var cls = (el.getAttribute("class") || "").toLowerCase();
            if (
                name === "city" ||
                (id && id.indexOf("city") !== -1) ||
                (cls && cls.indexOf("city") !== -1)
            ) {
                result.push(el);
            }
        }
        return result;
    }

    /**
     * Debounce для вызова fn после паузы delay мс.
     */
    function debounce(fn, delay) {
        var timer = null;
        return function () {
            var args = arguments;
            if (timer) clearTimeout(timer);
            timer = setTimeout(function () {
                timer = null;
                fn.apply(null, args);
            }, delay);
        };
    }

    /**
     * Создаёт DOM для выпадающего списка под полем ввода.
     */
    function createDropdown() {
        var wrap = document.createElement("div");
        wrap.className = "city-autocomplete";
        wrap.setAttribute("role", "listbox");
        wrap.setAttribute("aria-hidden", "true");
        wrap.style.display = "none";
        // Базовые стили по умолчанию (на случай, если CSS не подключен, как в админке)
        wrap.style.background = "#fff";
        wrap.style.border = "1px solid #ccc";
        wrap.style.borderRadius = "4px";
        wrap.style.boxShadow = "0 4px 12px rgba(0,0,0,0.15)";
        wrap.style.maxHeight = "240px";
        wrap.style.overflowY = "auto";
        wrap.style.padding = "2px 0";
        return wrap;
    }

    /**
     * Позиционирует выпадающий список под полем ввода.
     */
    function positionDropdown(input, dropdown) {
        var rect = input.getBoundingClientRect();
        dropdown.style.position = "fixed";
        dropdown.style.left = rect.left + "px";
        dropdown.style.top = rect.bottom + "px";
        dropdown.style.width = Math.max(rect.width, 200) + "px";
        dropdown.style.zIndex = "9999";
    }

    /**
     * Показывает список вариантов.
     */
    function showSuggestions(dropdown, items, input, onSelect) {
        dropdown.innerHTML = "";
        dropdown.setAttribute("aria-hidden", "false");
        dropdown.style.display = "block";

        items.forEach(function (name, index) {
            var item = document.createElement("div");
            item.className = "city-autocomplete__item";
            item.setAttribute("role", "option");
            item.setAttribute("data-index", index);
            item.textContent = name;
            // Базовые стили по умолчанию для элемента
            item.style.padding = "4px 10px";
            item.style.cursor = "pointer";
            item.style.background = "#fff";
            item.style.color = "#000";

            item.addEventListener("click", function (event) {
                event.preventDefault();
                event.stopPropagation();
                onSelect(name);
            });
            item.addEventListener("mouseenter", function () {
                setHighlight(dropdown, index);
            });
            dropdown.appendChild(item);
        });

        setHighlight(dropdown, 0);
    }

    function setHighlight(dropdown, index) {
        var items = dropdown.querySelectorAll(".city-autocomplete__item");
        items.forEach(function (el, i) {
            var active = i === index;
            el.classList.toggle("city-autocomplete__item--active", active);
            if (active) {
                el.style.background = "#1e90ff";
                el.style.color = "#fff";
            } else {
                el.style.background = "#fff";
                el.style.color = "#000";
            }
        });
    }

    function getHighlightedIndex(dropdown) {
        var active = dropdown.querySelector(".city-autocomplete__item--active");
        if (!active) return -1;
        return parseInt(active.getAttribute("data-index"), 10);
    }

    function hideDropdown(dropdown) {
        dropdown.style.display = "none";
        dropdown.innerHTML = "";
        dropdown.setAttribute("aria-hidden", "true");
    }

    /**
     * Подключает автодополнение к одному полю ввода.
     */
    function attachToInput(input) {
        if (input.dataset.cityAutocomplete === "attached") return;
        input.dataset.cityAutocomplete = "attached";

        // Отключаем нативный autocomplete браузера, чтобы не мешал нашему списку
        input.setAttribute("autocomplete", "off");
        input.setAttribute("autocapitalize", "off");
        input.setAttribute("autocorrect", "off");
        input.setAttribute("spellcheck", "false");

        var dropdown = createDropdown();
        document.body.appendChild(dropdown);

        var abortController = null;
        var debouncedFetch = debounce(function (query) {
            if (abortController) abortController.abort();
            abortController = new AbortController();

            if (query.length < MIN_QUERY_LENGTH) {
                hideDropdown(dropdown);
                return;
            }

            var url = API_URL + "?q=" + encodeURIComponent(query);
            fetch(url, { signal: abortController.signal })
                .then(function (res) {
                    return res.json();
                })
                .then(function (data) {
                    if (Array.isArray(data) && data.length > 0) {
                        positionDropdown(input, dropdown);
                        showSuggestions(dropdown, data, input, function (name) {
                            input.value = name;
                            hideDropdown(dropdown);
                            // Явно триггерим событие change, но не открываем подсказки заново
                            var evt = new Event('change', { bubbles: true });
                            input.dispatchEvent(evt);
                        });
                    } else {
                        hideDropdown(dropdown);
                    }
                })
                .catch(function (err) {
                    if (err.name !== "AbortError") hideDropdown(dropdown);
                });
        }, DEBOUNCE_MS);

        function onInput() {
            var q = (input.value || "").trim();
            if (q.length < MIN_QUERY_LENGTH) {
                hideDropdown(dropdown);
                return;
            }
            debouncedFetch(q);
        }

        function onKeydown(e) {
            if (dropdown.style.display !== "block") return;
            var items = dropdown.querySelectorAll(".city-autocomplete__item");
            var current = getHighlightedIndex(dropdown);

            if (e.key === "ArrowDown") {
                e.preventDefault();
                var next = current < items.length - 1 ? current + 1 : 0;
                setHighlight(dropdown, next);
                return;
            }
            if (e.key === "ArrowUp") {
                e.preventDefault();
                var prev = current > 0 ? current - 1 : items.length - 1;
                setHighlight(dropdown, prev);
                return;
            }
            if (e.key === "Enter") {
                e.preventDefault();
                if (current >= 0 && items[current]) {
                    input.value = items[current].textContent;
                    hideDropdown(dropdown);
                }
                return;
            }
            if (e.key === "Escape") {
                e.preventDefault();
                hideDropdown(dropdown);
            }
        }

        function closeOnClickOutside(e) {
            if (
                dropdown.contains(e.target) ||
                input === e.target
            ) return;
            hideDropdown(dropdown);
        }

        input.addEventListener("input", onInput);
        input.addEventListener("focus", onInput);
        input.addEventListener("keydown", onKeydown);
        input.addEventListener("blur", function () {
            // На мобильных даём возможность обработать клик по подсказке, затем скрываем список, если фокус ушёл
            window.setTimeout(function () {
                if (!dropdown.contains(document.activeElement)) {
                    hideDropdown(dropdown);
                }
            }, 150);
        });
        document.addEventListener("click", closeOnClickOutside);
    }

    function init() {
        var inputs = findCityInputs();
        for (var i = 0; i < inputs.length; i++) {
            attachToInput(inputs[i]);
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
