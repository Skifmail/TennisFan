/**
 * Выпадающий фильтр покрытия на странице кортов: несколько чекбоксов.
 */
(function () {
    "use strict";

    function serialize(wrap) {
        var values = [];
        wrap.querySelectorAll('input[type="checkbox"]:checked').forEach(function (box) {
            values.push(box.value);
        });
        return values.join("|");
    }

    function selectedLabel(wrap) {
        var checked = wrap.querySelectorAll('input[type="checkbox"]:checked');
        if (!checked.length) {
            return "Все покрытия";
        }
        var labels = [];
        checked.forEach(function (box) {
            var text = box.parentElement ? (box.parentElement.textContent || "").trim() : "";
            if (text) {
                labels.push(text);
            }
        });
        return labels.join(", ");
    }

    function closeDropdown(wrap, submitIfChanged) {
        var dropdown = wrap.querySelector(".club-filter-select__dropdown");
        var toggle = wrap.querySelector(".club-filter-select__toggle");
        if (!dropdown || !toggle) {
            return;
        }
        var wasOpen = !dropdown.hidden;
        dropdown.hidden = true;
        toggle.setAttribute("aria-expanded", "false");
        wrap.classList.remove("club-filter-select--open");
        var form = wrap.closest("form");
        if (form) {
            form.classList.remove("club-filter-dropdown-open");
        }
        if (
            wasOpen &&
            submitIfChanged &&
            wrap.getAttribute("data-auto-submit") === "1" &&
            form &&
            serialize(wrap) !== wrap.getAttribute("data-initial-surfaces")
        ) {
            form.submit();
        }
    }

    function openDropdown(wrap) {
        var dropdown = wrap.querySelector(".club-filter-select__dropdown");
        var toggle = wrap.querySelector(".club-filter-select__toggle");
        if (!dropdown || !toggle) {
            return;
        }
        dropdown.hidden = false;
        toggle.setAttribute("aria-expanded", "true");
        wrap.classList.add("club-filter-select--open");
        var form = wrap.closest("form");
        if (form) {
            form.classList.add("club-filter-dropdown-open");
        }
    }

    function initWrap(wrap) {
        var toggle = wrap.querySelector(".club-filter-select__toggle");
        var dropdown = wrap.querySelector(".club-filter-select__dropdown");
        var currentLabel = wrap.querySelector(".club-filter-select__current");
        if (!toggle || !dropdown || !currentLabel) {
            return;
        }
        wrap.setAttribute("data-initial-surfaces", serialize(wrap));

        toggle.addEventListener("click", function (event) {
            event.preventDefault();
            event.stopPropagation();
            if (dropdown.hidden) {
                openDropdown(wrap);
            } else {
                closeDropdown(wrap, true);
            }
        });

        document.addEventListener("click", function (event) {
            if (!wrap.contains(/** @type {Node} */ (event.target))) {
                closeDropdown(wrap, true);
            }
        });

        wrap.addEventListener("keydown", function (event) {
            if (event.key === "Escape") {
                closeDropdown(wrap, true);
                toggle.focus();
            }
        });

        wrap.querySelectorAll('input[type="checkbox"]').forEach(function (box) {
            box.addEventListener("click", function (event) {
                event.stopPropagation();
            });
            box.addEventListener("change", function () {
                var option = box.closest(".court-surface-filter__option");
                if (option) {
                    option.classList.toggle("is-selected", box.checked);
                }
                currentLabel.textContent = selectedLabel(wrap);
            });
        });
    }

    document.querySelectorAll("[data-court-surface-filter]").forEach(initWrap);
})();
