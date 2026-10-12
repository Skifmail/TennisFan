(function () {
    var form = document.querySelector(".sparring-create__form");
    if (!form) return;
    var box = document.querySelector("[data-sparring-errors]");
    var list = document.querySelector("[data-sparring-errors-list]");
    if (!box || !list) return;

    function fieldMessage(input) {
        var required = input.getAttribute("data-sparring-required");
        if (required && !String(input.value || "").trim()) return required;
        if (!input.getAttribute("data-sparring-age")) return "";
        var raw = String(input.value || "").trim();
        if (!raw) return "";
        var value = Number(raw);
        if (!Number.isInteger(value) || value < 1 || value > 100) {
            return (
                input.getAttribute("data-sparring-age-error") ||
                "Укажите возраст числом от 1 до 100."
            );
        }
        return "";
    }

    function collect() {
        var messages = [];
        form.querySelectorAll("[data-sparring-required], [data-sparring-age]").forEach(
            function (input) {
                var message = fieldMessage(input);
                input.classList.toggle("is-invalid", Boolean(message));
                if (message && messages.indexOf(message) === -1) messages.push(message);
            }
        );
        var minInput = form.querySelector("[data-sparring-age='min']");
        var maxInput = form.querySelector("[data-sparring-age='max']");
        if (
            minInput &&
            maxInput &&
            !minInput.classList.contains("is-invalid") &&
            !maxInput.classList.contains("is-invalid")
        ) {
            var minRaw = String(minInput.value || "").trim();
            var maxRaw = String(maxInput.value || "").trim();
            if (minRaw && maxRaw && Number(minRaw) > Number(maxRaw)) {
                messages.push(
                    "Минимальный возраст не может быть больше максимального."
                );
                minInput.classList.add("is-invalid");
                maxInput.classList.add("is-invalid");
            }
        }
        return messages;
    }

    function render(messages) {
        list.replaceChildren();
        messages.forEach(function (message) {
            var item = document.createElement("li");
            item.textContent = message;
            list.appendChild(item);
        });
        box.hidden = messages.length === 0;
    }

    var armed = !box.hidden;
    if (armed) {
        collect();
        box.scrollIntoView({ block: "start" });
    }

    form.addEventListener("submit", function (event) {
        armed = true;
        var messages = collect();
        render(messages);
        if (!messages.length) return;
        event.preventDefault();
        box.scrollIntoView({ block: "nearest" });
    });

    function refresh() {
        if (!armed) return;
        render(collect());
    }

    form.addEventListener("input", refresh);
    form.addEventListener("change", refresh);
})();
