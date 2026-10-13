/**
 * Подсказки кортов в поле «Предпочтительное место» заявки на спарринг.
 */
(function () {
    "use strict";

    var DEBOUNCE_MS = 250;

    function cityValue() {
        var city = document.querySelector('input[name="city"]');
        return city ? city.value.trim() : "";
    }

    function sportValue() {
        var sport = document.querySelector('input[name="sport"], select[name="sport"]');
        return sport ? sport.value.trim() : "tennis";
    }

    function createDropdown() {
        var list = document.createElement("div");
        list.className = "city-autocomplete";
        list.setAttribute("role", "listbox");
        list.style.display = "none";
        list.style.position = "absolute";
        list.style.zIndex = "9999";
        list.style.background = "#ffffff";
        list.style.color = "#1a3a2a";
        list.style.border = "1px solid #cbd5e1";
        list.style.borderRadius = "8px";
        list.style.boxShadow = "0 8px 20px rgba(15, 23, 42, 0.18)";
        list.style.maxHeight = "240px";
        list.style.overflowY = "auto";
        list.style.padding = "4px 0";
        document.body.appendChild(list);
        return list;
    }

    function placeDropdown(input, list) {
        var rect = input.getBoundingClientRect();
        var scrollX = window.pageXOffset || document.documentElement.scrollLeft || 0;
        var scrollY = window.pageYOffset || document.documentElement.scrollTop || 0;
        list.style.left = rect.left + scrollX + "px";
        list.style.top = rect.bottom + scrollY + "px";
        list.style.width = Math.max(rect.width, 220) + "px";
    }

    function hide(list) {
        list.style.display = "none";
        list.innerHTML = "";
    }

    function show(list, courts, input) {
        list.innerHTML = "";
        courts.forEach(function (court) {
            var item = document.createElement("button");
            item.type = "button";
            item.className = "city-autocomplete__item";
            item.setAttribute("role", "option");
            item.style.display = "block";
            item.style.width = "100%";
            item.style.textAlign = "left";
            item.style.background = "transparent";
            item.style.border = "0";
            item.style.padding = "8px 12px";
            item.style.cursor = "pointer";
            item.style.color = "#1a3a2a";
            var title = document.createElement("span");
            title.textContent = court.name;
            title.style.display = "block";
            title.style.fontWeight = "600";
            var meta = document.createElement("span");
            var metaText = court.city || "";
            if (court.address) {
                metaText = metaText ? metaText + " · " + court.address : court.address;
            }
            meta.textContent = metaText;
            meta.style.display = "block";
            meta.style.fontSize = "12px";
            meta.style.color = "#52675c";
            item.appendChild(title);
            item.appendChild(meta);
            item.addEventListener("mousedown", function (event) {
                event.preventDefault();
                input.value = court.name;
                hide(list);
            });
            list.appendChild(item);
        });
        list.style.display = "block";
    }

    function init(input) {
        var url = input.getAttribute("data-court-suggest");
        if (!url) {
            return;
        }
        var list = createDropdown();
        var timer = null;
        var requestId = 0;

        function load() {
            var current = ++requestId;
            var params = new URLSearchParams();
            params.set("q", input.value.trim());
            params.set("city", cityValue());
            params.set("sport", sportValue());
            fetch(url + "?" + params.toString(), {
                headers: { Accept: "application/json" },
                credentials: "same-origin",
            })
                .then(function (response) {
                    if (!response.ok) {
                        throw new Error("court suggest failed");
                    }
                    return response.json();
                })
                .then(function (data) {
                    if (current !== requestId) {
                        return;
                    }
                    var courts = (data && data.courts) || [];
                    if (!courts.length) {
                        hide(list);
                        return;
                    }
                    placeDropdown(input, list);
                    show(list, courts, input);
                })
                .catch(function () {
                    if (current === requestId) {
                        hide(list);
                    }
                });
        }

        function schedule() {
            if (timer) {
                clearTimeout(timer);
            }
            timer = setTimeout(load, DEBOUNCE_MS);
        }

        input.addEventListener("input", schedule);
        input.addEventListener("focus", schedule);
        input.addEventListener("keydown", function (event) {
            if (event.key === "Escape") {
                hide(list);
            }
        });
        document.addEventListener("click", function (event) {
            if (event.target === input || list.contains(event.target)) {
                return;
            }
            hide(list);
        });
        window.addEventListener("resize", function () {
            if (list.style.display === "block") {
                placeDropdown(input, list);
            }
        });
    }

    document.querySelectorAll("input[data-court-suggest]").forEach(init);
})();
