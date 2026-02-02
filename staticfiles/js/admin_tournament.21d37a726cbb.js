/**
 * Admin tournament: динамическое отображение полей в зависимости от формата.
 *
 * При выборе формата FAN — поля FAN. При выборе Круговой — поля кругового.
 */

(function () {
    "use strict";

    const FAN_FORMAT = "single_elimination";
    const ROUND_ROBIN_FORMAT = "round_robin";

    function getFormatSelect() {
        return document.querySelector("#id_format, select[name='format']");
    }

    function getFormatValue() {
        const select = getFormatSelect();
        return select ? select.value : "";
    }

    function toggleSections(selector, show) {
        const sections = document.querySelectorAll(selector);
        sections.forEach(function (section) {
            const module = section.closest(".module");
            if (module) {
                module.style.display = show ? "" : "none";
            } else {
                section.style.display = show ? "" : "none";
            }
        });
    }

    function updateVisibility() {
        const format = getFormatValue();
        toggleSections(".format-fan-section", format === FAN_FORMAT);
        toggleSections(".format-round-robin-section", format === ROUND_ROBIN_FORMAT);
    }

    function init() {
        const formatSelect = getFormatSelect();
        if (!formatSelect) {
            return;
        }

        formatSelect.addEventListener("change", updateVisibility);
        updateVisibility();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
