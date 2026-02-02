/**
 * Admin tournament: динамическое отображение полей в зависимости от формата.
 *
 * При выборе формата FAN появляются все поля для заполнения турнира FAN.
 * В будущем при добавлении других форматов — аналогичная логика.
 */

(function () {
    "use strict";

    const FAN_FORMAT = "single_elimination";

    function getFormatSelect() {
        return document.querySelector("#id_format, select[name='format']");
    }

    function getFormatValue() {
        const select = getFormatSelect();
        return select ? select.value : "";
    }

    function toggleFanSections(show) {
        const sections = document.querySelectorAll(".format-fan-section");
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
        toggleFanSections(format === FAN_FORMAT);
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
