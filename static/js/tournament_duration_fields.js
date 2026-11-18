/** Продолжительность — единственный выбор календаря и условий однодневной оплаты. */
(function () {
    "use strict";

    function init() {
        const duration = document.querySelector("#id_duration, select[name='duration']");
        if (!duration) return;

        const days = document.querySelector("#id_match_days_per_round");
        const daysRow = days && days.closest(".form-row, .club-tournament-create__field");
        const startTime = document.querySelector("#id_start_time");

        function update() {
            const singleDay = duration.value === "single";
            const shortEvent = singleDay || duration.value === "weekend";
            if (startTime) {
                startTime.required = singleDay;
                startTime.setAttribute("aria-required", String(singleDay));
            }
            if (daysRow) {
                const hasErrors = Boolean(daysRow.querySelector(".errorlist, .club-tournament-create__error"));
                daysRow.hidden = shortEvent && !hasErrors;
                daysRow.style.display = daysRow.hidden ? "none" : "";
            }
        }

        duration.addEventListener("change", update);
        update();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
