(function () {
    function isMoscow(value) {
        return (value || "").trim().toLowerCase() === "москва";
    }

    function initCityFilter() {
        var form = document.querySelector("[data-training-filters]");
        if (!form) {
            return;
        }
        var city = form.querySelector("[data-training-city]");
        var area = form.querySelector('input[name="area"]');
        if (!city) {
            return;
        }

        form.addEventListener("submit", function () {
            if (area && !isMoscow(city.value)) {
                area.value = "";
            }
        });

        var areaLinks = document.querySelectorAll("[data-training-area]");
        function updateAreaLinks() {
            areaLinks.forEach(function (link) {
                var url = new URL(link.href, window.location.href);
                ["sport", "type", "level"].forEach(function (name) {
                    var field = form.elements.namedItem(name);
                    if (field.value) {
                        url.searchParams.set(name, field.value);
                    } else {
                        url.searchParams.delete(name);
                    }
                });
                link.href = url.href;
            });
        }
        form.addEventListener("change", updateAreaLinks);
        updateAreaLinks();
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
