/**
 * Показывать чекбоксы покрытий только для выбранного формата корта.
 */
(function () {
    "use strict";

    function rowForField(fieldId) {
        var el = document.getElementById(fieldId);
        if (!el) {
            return null;
        }
        return el.closest(".form-row") || el.closest(".form-group");
    }

    function syncRows() {
        var indoor = document.getElementById("id_is_indoor");
        var outdoor = document.getElementById("id_is_outdoor");
        var indoorRow = rowForField("id_indoor_surfaces");
        var outdoorRow = rowForField("id_outdoor_surfaces");
        if (!indoor || !outdoor || !indoorRow || !outdoorRow) {
            return;
        }
        indoorRow.style.display = indoor.checked ? "" : "none";
        outdoorRow.style.display = outdoor.checked ? "" : "none";
        if (!indoor.checked) {
            indoorRow.querySelectorAll("input[type='checkbox']").forEach(function (box) {
                box.checked = false;
            });
        }
        if (!outdoor.checked) {
            outdoorRow.querySelectorAll("input[type='checkbox']").forEach(function (box) {
                box.checked = false;
            });
        }
    }

    function syncPadelRows() {
        var sport = document.getElementById("id_venue_sport");
        if (!sport) {
            return;
        }
        var show = sport.value === "padel" || sport.value === "both";
        ["id_has_glass_walls", "id_padel_surfaces"].forEach(function (fieldId) {
            var row = rowForField(fieldId);
            if (row) {
                row.style.display = show ? "" : "none";
            }
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        var indoor = document.getElementById("id_is_indoor");
        var outdoor = document.getElementById("id_is_outdoor");
        if (indoor && outdoor) {
            indoor.addEventListener("change", syncRows);
            outdoor.addEventListener("change", syncRows);
            syncRows();
        }
        var sport = document.getElementById("id_venue_sport");
        if (sport) {
            sport.addEventListener("change", syncPadelRows);
            syncPadelRows();
        }
    });
})();
