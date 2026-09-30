/**
 * Скрывает поля региона и района Москвы, если населённый пункт не из
 * Москвы / области. Подключается на форме клуба и в админке турнира.
 *
 * Читает JSON из #geo-areas-payload: [{id, region, name, aliases}, ...].
 */
(function () {
    "use strict";

    var MOSCOW_NAMES = {
        москва: true,
        moscow: true,
        moskva: true,
    };

    function normalize(text) {
        return String(text || "")
            .trim()
            .toLowerCase()
            .replace(/[‐‑‒–—−]/g, "-")
            .replace(/\s+/g, " ");
    }

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

    function cityUsesMoscowGeo(city, areas) {
        var needle = normalize(city);
        if (!needle) {
            return true;
        }
        if (MOSCOW_NAMES[needle]) {
            return true;
        }
        for (var i = 0; i < areas.length; i += 1) {
            var area = areas[i];
            if (area.region !== "moscow_oblast") {
                continue;
            }
            var names = [area.name].concat(area.aliases || []);
            for (var j = 0; j < names.length; j += 1) {
                if (normalize(names[j]) === needle) {
                    return true;
                }
            }
        }
        return false;
    }

    function fieldWrapper(el) {
        if (!el) {
            return null;
        }
        return (
            el.closest("[data-moscow-geo-field]") ||
            el.closest(".form-row") ||
            el.closest(".form-group") ||
            el.parentElement
        );
    }

    function setHidden(wrapper, hidden) {
        if (!wrapper) {
            return;
        }
        wrapper.hidden = hidden;
        wrapper.style.display = hidden ? "none" : "";
    }

    function init() {
        var scriptEl =
            document.getElementById("geo-areas-payload") ||
            document.getElementById("club-geo-areas");
        var cityInput = document.querySelector("[data-geo-city]");
        var regionSelect = document.querySelector("[data-geo-region]");
        var areaSelect = document.querySelector("[data-geo-area]");
        if (!cityInput || !regionSelect || !areaSelect) {
            return;
        }

        var areas = parseAreas(scriptEl);
        var regionWrap = fieldWrapper(regionSelect);
        var areaWrap = fieldWrapper(areaSelect);

        function sync() {
            var show = cityUsesMoscowGeo(cityInput.value, areas);
            setHidden(regionWrap, !show);
            setHidden(areaWrap, !show);
            if (!show) {
                regionSelect.value = "";
                areaSelect.value = "";
            }
        }

        cityInput.addEventListener("change", sync);
        cityInput.addEventListener("input", sync);
        sync();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
