/**
 * Форма клубного турнира: падел только в парах.
 *
 * Подключается из templates/clubs/tournament_create.html.
 */
(function () {
    "use strict";

    var PADEL = "padel";
    var DOUBLES = "doubles";
    var LOCK_ID = "id_variant_padel_lock";

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

    function syncSportFields() {
        syncPadelVariant();
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
