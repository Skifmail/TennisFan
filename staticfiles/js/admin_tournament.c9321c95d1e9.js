/**
 * Admin tournament: динамическое отображение полей в зависимости от формата и варианта.
 *
 * Формат: Одноэтапная сетка — очки за раунды, Круговой — поля кругового.
 * Вариант: Одиночный — только участники, Парный — блок «Команды».
 */

(function () {
    "use strict";

    const FAN_FORMAT = "single_elimination";
    const OLYMPIC_FORMAT = "olympic_consolation";
    const ROUND_ROBIN_FORMAT = "round_robin";
    const VARIANT_DOUBLES = "doubles";

    function getFormatSelect() {
        return document.querySelector("#id_format, select[name='format']");
    }

    function getFormatValue() {
        const select = getFormatSelect();
        if (!select) return "";
        const value = select.value;
        // Если значение пустое, но есть опция по умолчанию (selected), используем её
        if (!value && select.options.length > 0) {
            for (let i = 0; i < select.options.length; i++) {
                if (select.options[i].selected && select.options[i].value) {
                    return select.options[i].value;
                }
            }
            // Если ничего не выбрано, но есть первая опция, используем её (для нового турнира с дефолтом)
            if (select.options[0].value) {
                return select.options[0].value;
            }
        }
        return value;
    }

    function getVariantSelect() {
        return document.querySelector("#id_variant, select[name='variant']");
    }

    function getVariantValue() {
        const select = getVariantSelect();
        return select ? select.value : "";
    }

    function toggleSections(selector, show) {
        // Ищем все элементы с указанным классом (может быть fieldset или другой элемент)
        const sections = document.querySelectorAll(selector);
        sections.forEach(function (section) {
            // В Django admin fieldset имеет классы напрямую на элементе fieldset
            // Ищем fieldset или его контейнер .module
            let container = section;
            if (section.tagName && section.tagName.toUpperCase() === "FIELDSET") {
                // Это сам fieldset
                container = section;
            } else {
                // Ищем ближайший fieldset или .module контейнер
                container = section.closest("fieldset") || section.closest(".module") || section;
            }
            if (container) {
                // Если показываем, убираем display: none, иначе скрываем
                if (show) {
                    container.style.display = "";
                    // Убираем атрибут style, если он был установлен на none
                    if (container.style.display === "none") {
                        container.removeAttribute("style");
                    }
                } else {
                    container.style.display = "none";
                }
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

    function getFieldRow(fieldName) {
        const input = document.querySelector("#id_" + fieldName + ", [name='" + fieldName + "']");
        if (!input) return null;
        return input.closest(".form-row") || input.closest(".field-" + fieldName) || input.closest("div");
    }

    function updateParticipantsVsTeamsVisibility() {
        const variant = getVariantValue();
        const isDoubles = variant === VARIANT_DOUBLES;

        const participantsRow1 = getFieldRow("min_participants");
        const participantsRow2 = getFieldRow("max_participants");
        const teamsRow1 = getFieldRow("min_teams");
        const teamsRow2 = getFieldRow("max_teams");

        [participantsRow1, participantsRow2].forEach(function (row) {
            if (row) row.style.display = isDoubles ? "none" : "";
        });
        [teamsRow1, teamsRow2].forEach(function (row) {
            if (row) row.style.display = isDoubles ? "" : "none";
        });
    }

    function updateVariantVisibility() {
        const variant = getVariantValue();
        const teamsEl = getTeamsInlineElement();
        if (teamsEl) {
            teamsEl.style.display = variant === VARIANT_DOUBLES ? "" : "none";
        }
        updateGenderOptions();
        updateParticipantsVsTeamsVisibility();
    }

    function updateGenderOptions() {
        const variant = getVariantValue();
        const genderSelect = document.querySelector("#id_gender, select[name='gender']");
        if (!genderSelect) {
            return;
        }

        // Сохраняем текущее значение
        const currentValue = genderSelect.value;
        
        // Получаем все опции
        const allOptions = Array.from(genderSelect.querySelectorAll("option"));
        const mixedOption = allOptions.find(opt => opt.value === "mixed");
        
        if (!mixedOption) {
            return;
        }

        const isDoubles = variant === VARIANT_DOUBLES;
        
        if (isDoubles) {
            // Парный турнир - показываем "Микст"
            mixedOption.disabled = false;
            mixedOption.style.display = "";
            // Убираем атрибут hidden если есть
            if (mixedOption.hasAttribute("hidden")) {
                mixedOption.removeAttribute("hidden");
            }
        } else {
            // Одиночный турнир - скрываем "Микст"
            mixedOption.disabled = true;
            mixedOption.style.display = "none";
            mixedOption.setAttribute("hidden", "hidden");
            
            // Если выбран "Микст", переключаем на "Смешанный" (open)
            if (currentValue === "mixed") {
                genderSelect.value = "open";
                // Триггерим событие change для обновления формы
                const event = new Event("change", { bubbles: true });
                genderSelect.dispatchEvent(event);
            }
        }
    }

    function updateRoundRobinPointsDefaults() {
        const format = getFormatValue();
        const isRoundRobin = format === ROUND_ROBIN_FORMAT;
        if (!isRoundRobin) return;

        const pointsWinnerInput = document.querySelector("#id_points_winner, [name='points_winner']");
        const pointsLoserInput = document.querySelector("#id_points_loser, [name='points_loser']");

        if (pointsWinnerInput && pointsLoserInput) {
            const currentWinner = parseInt(pointsWinnerInput.value) || 0;
            const currentLoser = parseInt(pointsLoserInput.value) || 0;
            // Если значения равны дефолтам для других форматов (100/-50), устанавливаем стандартные для кругового
            if (currentWinner === 100 && currentLoser === -50) {
                pointsWinnerInput.value = "1";
                pointsLoserInput.value = "0";
            }
        }
    }

    function updateVisibility() {
        const format = getFormatValue();
        const isFan = format === FAN_FORMAT;
        const isOlympic = format === OLYMPIC_FORMAT;
        const isRoundRobin = format === ROUND_ROBIN_FORMAT;
        // Общие поля — при любом выбранном формате (одноэтапная, Олимпийская, Круговой).
        toggleSections(".format-common-section", isFan || isOlympic || isRoundRobin);
        // Секция очков за раунды/места: одноэтапная сетка, Олимпийская и Круговой (одни и те же поля)
        // Эта секция имеет все три класса (format-fan-section, format-olympic-section, format-round-robin-section)
        // Показываем её для любого из форматов
        const showPointsSection = isFan || isOlympic || isRoundRobin;
        toggleSections(".format-fan-section", showPointsSection);
        toggleSections(".format-olympic-section", showPointsSection);
        // Секция "Круговой: формат матча" имеет только класс format-round-robin-section (без format-fan-section и format-olympic-section)
        // Показываем её только для кругового
        const roundRobinMatchFormatSections = document.querySelectorAll(".format-round-robin-section");
        roundRobinMatchFormatSections.forEach(function(section) {
            // Проверяем, имеет ли секция также классы format-fan-section или format-olympic-section
            // Если да - это секция очков (уже обработана выше), если нет - это секция "Формат матча"
            const hasFanOrOlympicClass = section.classList.contains("format-fan-section") || section.classList.contains("format-olympic-section");
            if (!hasFanOrOlympicClass) {
                const container = section.tagName && section.tagName.toUpperCase() === "FIELDSET" 
                    ? section 
                    : section.closest("fieldset") || section.closest(".module") || section;
                if (container) {
                    container.style.display = isRoundRobin ? "" : "none";
                }
            }
        });
        updateVariantVisibility();
        updateParticipantsVsTeamsVisibility();
    }

    function init() {
        const formatSelect = getFormatSelect();
        if (!formatSelect) {
            return;
        }

        formatSelect.addEventListener("change", updateVisibility);
        formatSelect.addEventListener("input", updateVisibility);
        const variantSelect = getVariantSelect();
        if (variantSelect) {
            variantSelect.addEventListener("change", updateVariantVisibility);
            variantSelect.addEventListener("input", updateVariantVisibility);
        }
        // Вызываем updateVisibility с небольшой задержкой, чтобы убедиться, что значения по умолчанию установлены
        setTimeout(function() {
            updateVisibility();
            updateGenderOptions();
            updateParticipantsVsTeamsVisibility();
        }, 50);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
