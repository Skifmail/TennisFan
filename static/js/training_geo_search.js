(function () {
    function isMoscow(value) {
        return (value || "").trim().toLowerCase() === "москва";
    }

    function initCityFilter() {
        var form = document.querySelector(".training-browse__filters");
        if (!form) {
            return;
        }
        var city = form.querySelector("[data-training-city]");
        var area = form.querySelector('input[name="area"]');
        if (!city) {
            return;
        }

        function apply() {
            if (area && !isMoscow(city.value)) {
                area.value = "";
            }
            form.submit();
        }

        city.addEventListener("change", apply);
        city.addEventListener("keydown", function (event) {
            if (event.key !== "Enter") {
                return;
            }
            event.preventDefault();
            window.setTimeout(apply, 0);
        });
    }

    function initCourtSearch() {
        var input = document.querySelector("[data-court-search]");
        var list = document.querySelector("[data-court-search-list]");
        var empty = document.querySelector("[data-court-search-empty]");
        if (!input || !list) {
            return;
        }

        var items = list.querySelectorAll("[data-court-search-item]");

        input.addEventListener("input", function () {
            var query = input.value.toLowerCase().trim();
            var visible = 0;

            items.forEach(function (item) {
                var name = (item.getAttribute("data-court-name") || "").toLowerCase();
                var matches = !query || name.indexOf(query) !== -1;
                item.hidden = !matches;
                if (matches) {
                    visible += 1;
                }
            });

            list.hidden = visible === 0;
            if (empty) {
                empty.hidden = visible !== 0;
            }
        });
    }

    function init() {
        initCityFilter();
        initCourtSearch();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
