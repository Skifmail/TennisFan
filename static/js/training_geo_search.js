(function () {
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

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initCourtSearch);
    } else {
        initCourtSearch();
    }
})();
