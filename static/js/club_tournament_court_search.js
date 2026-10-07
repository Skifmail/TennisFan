/**
 * Поиск корта в форме клубного турнира.
 *
 * Подключается из templates/clubs/tournament_create.html.
 */
(function () {
    "use strict";

    var PADEL = "padel";
    var MIN_QUERY_LENGTH = 2;
    var DEBOUNCE_MS = 250;

    function courtMatchesSport(venue, sport) {
        var value = venue || "tennis";
        if (sport === PADEL) {
            return value === "padel" || value === "both";
        }
        return value === "tennis" || value === "both" || value === "";
    }

    function currentSport() {
        var sport = document.querySelector("#id_sport, select[name='sport']");
        return (sport && sport.value) || "tennis";
    }

    function init(root) {
        var url = root.getAttribute("data-search-url");
        var hidden = root.querySelector("[data-court-value]");
        var input = root.querySelector("[data-court-query]");
        var list = root.querySelector("[data-court-list]");
        var clearButton = root.querySelector("[data-court-clear]");
        if (!url || !hidden || !input || !list) {
            return;
        }

        var timer = null;
        var items = [];
        var active = -1;
        var requestId = 0;

        function setClearVisible(visible) {
            if (clearButton) {
                clearButton.hidden = !visible;
            }
        }

        function closeList() {
            list.hidden = true;
            list.innerHTML = "";
            items = [];
            active = -1;
        }

        function markActive() {
            var nodes = list.querySelectorAll(".court-search__item");
            Array.prototype.forEach.call(nodes, function (node, index) {
                node.classList.toggle("is-active", index === active);
                if (index === active) {
                    input.setAttribute("aria-activedescendant", node.id);
                }
            });
        }

        function selectItem(item) {
            hidden.value = String(item.id);
            input.value = item.name + " — " + item.city;
            root.setAttribute("data-venue-sport", item.venue_sport || "tennis");
            setClearVisible(true);
            closeList();
        }

        function clearSelection() {
            hidden.value = "";
            input.value = "";
            root.setAttribute("data-venue-sport", "");
            setClearVisible(false);
            closeList();
            input.focus();
        }

        function render(results) {
            list.innerHTML = "";
            items = results;
            active = -1;
            input.removeAttribute("aria-activedescendant");
            if (!results.length) {
                var empty = document.createElement("li");
                empty.className = "court-search__empty";
                empty.textContent = "Ничего не найдено";
                list.appendChild(empty);
                list.hidden = false;
                return;
            }
            results.forEach(function (item, index) {
                var row = document.createElement("li");
                row.className = "court-search__item";
                row.setAttribute("role", "option");
                row.id = input.id + "-opt-" + index;
                var title = document.createElement("span");
                title.className = "court-search__name";
                title.textContent = item.name;
                var meta = document.createElement("span");
                meta.className = "court-search__meta";
                meta.textContent = item.city + (item.address ? " · " + item.address : "");
                row.appendChild(title);
                row.appendChild(meta);
                row.addEventListener("mousedown", function (event) {
                    event.preventDefault();
                    selectItem(item);
                });
                list.appendChild(row);
            });
            list.hidden = false;
        }

        function search() {
            var query = input.value.trim();
            if (query.length < MIN_QUERY_LENGTH) {
                closeList();
                return;
            }
            var id = ++requestId;
            var params = new URLSearchParams({q: query, sport: currentSport()});
            fetch(url + "?" + params.toString(), {
                headers: {Accept: "application/json"},
                credentials: "same-origin",
            })
                .then(function (response) {
                    return response.ok ? response.json() : {results: []};
                })
                .then(function (data) {
                    if (id !== requestId) {
                        return;
                    }
                    render(data.results || []);
                })
                .catch(function () {
                    if (id === requestId) {
                        closeList();
                    }
                });
        }

        input.addEventListener("input", function () {
            hidden.value = "";
            root.setAttribute("data-venue-sport", "");
            setClearVisible(Boolean(input.value));
            if (timer) {
                clearTimeout(timer);
            }
            timer = setTimeout(search, DEBOUNCE_MS);
        });

        input.addEventListener("keydown", function (event) {
            if (list.hidden || !items.length) {
                return;
            }
            if (event.key === "ArrowDown") {
                event.preventDefault();
                active = Math.min(active + 1, items.length - 1);
                markActive();
            } else if (event.key === "ArrowUp") {
                event.preventDefault();
                active = Math.max(active - 1, 0);
                markActive();
            } else if (event.key === "Enter") {
                event.preventDefault();
                selectItem(items[active >= 0 ? active : 0]);
            } else if (event.key === "Escape") {
                closeList();
            }
        });

        if (clearButton) {
            clearButton.addEventListener("click", clearSelection);
        }

        document.addEventListener("click", function (event) {
            if (!root.contains(event.target)) {
                closeList();
            }
        });

        var sport = document.querySelector("#id_sport, select[name='sport']");
        if (sport) {
            sport.addEventListener("change", function () {
                var venue = root.getAttribute("data-venue-sport") || "";
                if (hidden.value && !courtMatchesSport(venue, currentSport())) {
                    hidden.value = "";
                    input.value = "";
                    root.setAttribute("data-venue-sport", "");
                    setClearVisible(false);
                    closeList();
                    return;
                }
                if (input.value.trim().length >= MIN_QUERY_LENGTH && !hidden.value) {
                    search();
                }
            });
        }

        setClearVisible(Boolean(hidden.value));
    }

    document.addEventListener("DOMContentLoaded", function () {
        document.querySelectorAll("[data-court-search]").forEach(init);
    });
})();
