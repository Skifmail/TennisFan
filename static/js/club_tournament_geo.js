/**
 * Каскад «регион → зона/город» в форме создания турнира клуба.
 *
 * Подключается из templates/clubs/tournament_create.html (block extra_js).
 * Читает JSON из #club-geo-areas: [{id, region, name}, ...].
 */
(function () {
    "use strict";

    function parseAreas(scriptEl) {
        if (!scriptEl || !scriptEl.textContent) {
            return [];
        }
        try {
            var data = JSON.parse(scriptEl.textContent);
            return Array.isArray(data) ? data : [];
        } catch (_err) {
            return [];
        }
    }

    function rebuildAreaOptions(areaSelect, areas, region, selectedId) {
        var emptyLabel = areaSelect.getAttribute("data-empty-label") || "Не выбрано";
        var keepValue = selectedId || areaSelect.value || "";
        areaSelect.innerHTML = "";

        var emptyOption = document.createElement("option");
        emptyOption.value = "";
        emptyOption.textContent = emptyLabel;
        areaSelect.appendChild(emptyOption);

        areas
            .filter(function (area) {
                return !region || area.region === region;
            })
            .forEach(function (area) {
                var option = document.createElement("option");
                option.value = String(area.id);
                option.textContent = area.name;
                if (String(area.id) === String(keepValue)) {
                    option.selected = true;
                }
                areaSelect.appendChild(option);
            });

        if (keepValue && areaSelect.value !== String(keepValue)) {
            areaSelect.value = "";
        }
    }

    function init() {
        var scriptEl = document.getElementById("club-geo-areas");
        var regionSelect = document.querySelector("[data-geo-region]");
        var areaSelect = document.querySelector("[data-geo-area]");
        if (!regionSelect || !areaSelect) {
            return;
        }

        var areas = parseAreas(scriptEl);
        if (!areas.length) {
            return;
        }

        areaSelect.setAttribute(
            "data-empty-label",
            areaSelect.options.length ? areaSelect.options[0].textContent : "Не выбрано"
        );

        rebuildAreaOptions(areaSelect, areas, regionSelect.value, areaSelect.value);

        regionSelect.addEventListener("change", function () {
            rebuildAreaOptions(areaSelect, areas, regionSelect.value, "");
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
