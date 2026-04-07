/**
 * Кастомные выпадающие списки фильтров.
 * Поддерживает «Клуб» и универсальные селекты в фильтрах главной.
 */
(function () {
    "use strict";
    var FILTER_WRAP_SELECTOR = "[data-club-filter], [data-filter-select]";

    function setFormDropdownOpen(form, isOpen) {
        if (!form) {
            return;
        }
        form.classList.toggle("club-filter-dropdown-open", isOpen);
    }

    function closeDropdown(wrap) {
        var dropdown = wrap.querySelector(".club-filter-select__dropdown");
        var toggle = wrap.querySelector(".club-filter-select__toggle");
        if (!dropdown || !toggle) {
            return;
        }
        dropdown.hidden = true;
        toggle.setAttribute("aria-expanded", "false");
        wrap.classList.remove("club-filter-select--open");
        setFormDropdownOpen(wrap.closest("form"), false);
    }

    function closeAllDropdowns(exceptWrap) {
        document.querySelectorAll(FILTER_WRAP_SELECTOR).forEach(function (currentWrap) {
            if (currentWrap !== exceptWrap) {
                closeDropdown(currentWrap);
            }
        });
    }

    function openDropdown(wrap) {
        var dropdown = wrap.querySelector(".club-filter-select__dropdown");
        var toggle = wrap.querySelector(".club-filter-select__toggle");
        if (!dropdown || !toggle) {
            return;
        }
        closeAllDropdowns(wrap);
        dropdown.hidden = false;
        toggle.setAttribute("aria-expanded", "true");
        wrap.classList.add("club-filter-select--open");
        /* Вся форма выше блока с таблицей (следующий sibling), иначе sticky-thead перехватывает клики */
        setFormDropdownOpen(wrap.closest("form"), true);
    }

    function setSelectedState(wrap, value) {
        var options = wrap.querySelectorAll(".club-filter-select__option");
        options.forEach(function (btn) {
            var v = btn.getAttribute("data-value");
            if (v === null) {
                v = "";
            }
            var selected = v === value;
            btn.classList.toggle("is-selected", selected);
            btn.setAttribute("aria-selected", selected ? "true" : "false");
        });
    }

    function initWrap(wrap) {
        var toggle = wrap.querySelector(".club-filter-select__toggle");
        var dropdown = wrap.querySelector(".club-filter-select__dropdown");
        var hidden = wrap.querySelector('input[type="hidden"]');
        var currentLabel = wrap.querySelector(".club-filter-select__current");
        var autoSubmit = wrap.getAttribute("data-auto-submit") === "1";
        var form = wrap.closest("form");

        if (!toggle || !dropdown || !hidden || !currentLabel) {
            return;
        }

        toggle.addEventListener("click", function (e) {
            e.preventDefault();
            e.stopPropagation();
            if (dropdown.hidden) {
                openDropdown(wrap);
            } else {
                closeDropdown(wrap);
            }
        });

        document.addEventListener("click", function (e) {
            if (!wrap.contains(/** @type {Node} */ (e.target))) {
                closeDropdown(wrap);
            }
        });

        wrap.addEventListener("keydown", function (e) {
            if (e.key === "Escape") {
                closeDropdown(wrap);
                toggle.focus();
            }
        });

        dropdown.querySelectorAll(".club-filter-select__option").forEach(function (btn) {
            btn.addEventListener("click", function (e) {
                e.preventDefault();
                e.stopPropagation();
                var raw = btn.getAttribute("data-value");
                var value = raw === null ? "" : raw;
                var labelText = (btn.textContent || "").trim();
                hidden.value = value;
                currentLabel.textContent = labelText;
                setSelectedState(wrap, value);
                closeDropdown(wrap);
                if (autoSubmit && form) {
                    form.submit();
                }
            });
        });
    }

    document.querySelectorAll(FILTER_WRAP_SELECTOR).forEach(initWrap);
})();
