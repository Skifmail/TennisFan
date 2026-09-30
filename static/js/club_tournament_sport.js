/**
 * Форма клубного турнира: падел только в парах, корты по виду спорта.
 *
 * Подключается из templates/clubs/tournament_create.html.
 */
(function () {
    "use strict";

    var PADEL = "padel";
    var DOUBLES = "doubles";
    var LOCK_ID = "id_variant_padel_lock";

    function courtMatchesSport(venue, sport) {
        var value = venue || "tennis";
        if (sport === PADEL) {
            return value === "padel" || value === "both";
        }
        return value === "tennis" || value === "both" || value === "";
    }

    function syncPadelVariant() {
        var sport = document.querySelector("#id_sport, select[name='sport']");
        var variant = document.querySelector("#id_variant, select[name='variant']");
        if (!sport || !variant) {
            return;
        }
        var padel = sport.value === PADEL;
        var field = variant.closest(".club-tournament-create__field");
        if (padel) {
            variant.value = DOUBLES;
            variant.disabled = true;
            variant.setAttribute("aria-disabled", "true");
            var hidden = document.getElementById(LOCK_ID);
            if (!hidden) {
                hidden = document.createElement("input");
                hidden.type = "hidden";
                hidden.name = variant.name || "variant";
                hidden.id = LOCK_ID;
                variant.insertAdjacentElement("afterend", hidden);
            }
            hidden.value = DOUBLES;
            if (field) {
                field.style.opacity = "0.55";
            }
        } else {
            variant.disabled = false;
            variant.removeAttribute("aria-disabled");
            var lock = document.getElementById(LOCK_ID);
            if (lock) {
                lock.remove();
            }
            if (field) {
                field.style.opacity = "";
            }
        }
    }

    function syncCourts() {
        var sport = document.querySelector("#id_sport, select[name='sport']");
        var court = document.querySelector("#id_court, select[name='court']");
        if (!sport || !court) {
            return;
        }
        var selected = sport.value || "tennis";
        Array.prototype.forEach.call(court.options, function (option) {
            if (!option.value) {
                option.hidden = false;
                option.disabled = false;
                return;
            }
            var ok = courtMatchesSport(option.getAttribute("data-venue-sport"), selected);
            option.hidden = !ok;
            option.disabled = !ok;
        });
        var current = court.options[court.selectedIndex];
        if (current && current.disabled) {
            court.value = "";
        }
    }

    function syncSportFields() {
        syncPadelVariant();
        syncCourts();
    }

    document.addEventListener("DOMContentLoaded", function () {
        var sport = document.querySelector("#id_sport, select[name='sport']");
        if (!sport) {
            return;
        }
        sport.addEventListener("change", syncSportFields);
        sport.addEventListener("input", syncSportFields);
        syncSportFields();
    });
})();
