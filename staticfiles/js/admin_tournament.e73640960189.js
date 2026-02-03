/**
 * Admin tournament: динамическое отображение полей в зависимости от формата и варианта.
 *
 * Формат: FAN — поля FAN, Круговой — поля кругового.
 * Вариант: Одиночный — только участники, Парный — блок «Команды».
 */

(function () {
    "use strict";

    const FAN_FORMAT = "single_elimination";
    const ROUND_ROBIN_FORMAT = "round_robin";
    const VARIANT_DOUBLES = "doubles";

    function getFormatSelect() {
        return document.querySelector("#id_format, select[name='format']");
    }

    function getFormatValue() {
        const select = getFormatSelect();
        return select ? select.value : "";
    }

    function getVariantSelect() {
        return document.querySelector("#id_variant, select[name='variant']");
    }

    function getVariantValue() {
        const select = getVariantSelect();
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

    function getTeamsInlineElement() {
        // Класс задаётся в TournamentTeamInline.classes
        const byClass = document.querySelector(".variant-doubles-only");
        if (byClass) {
            return byClass;
        }
        // Fallback: по id (стандартный префикс формсета)
        const byId = document.querySelector("#tournamentteam_set-group");
        if (byId) {
            return byId;
        }
        // Fallback: ищем блок с заголовком «Команды»
        const modules = document.querySelectorAll(".module, fieldset.module");
        for (let i = 0; i < modules.length; i++) {
            const h2 = modules[i].querySelector("h2");
            if (h2 && (h2.textContent || "").trim().indexOf("Команды") !== -1) {
                return modules[i];
            }
        }
        return null;
    }

    function updateVariantVisibility() {
        const variant = getVariantValue();
        const teamsEl = getTeamsInlineElement();
        if (teamsEl) {
            teamsEl.style.display = variant === VARIANT_DOUBLES ? "" : "none";
        }
    }

    function updateVisibility() {
        const format = getFormatValue();
        const isFan = format === FAN_FORMAT;
        const isRoundRobin = format === ROUND_ROBIN_FORMAT;
        // Общие поля — при любом выбранном формате (FAN или Круговой).
        toggleSections(".format-common-section", isFan || isRoundRobin);
        toggleSections(".format-fan-section", isFan);
        toggleSections(".format-round-robin-section", isRoundRobin);
        updateVariantVisibility();
    }

    function init() {
        const formatSelect = getFormatSelect();
        if (!formatSelect) {
            return;
        }

        formatSelect.addEventListener("change", updateVisibility);
        const variantSelect = getVariantSelect();
        if (variantSelect) {
            variantSelect.addEventListener("change", updateVariantVisibility);
        }
        updateVisibility();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
