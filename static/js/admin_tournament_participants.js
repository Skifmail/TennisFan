"use strict";
document.addEventListener("DOMContentLoaded", () => {
    for (const select of document.querySelectorAll("select[data-player-picker]")) {
        const element = (tag, text = "") => {
            const node = document.createElement(tag);
            node.textContent = text;
            return node;
        };
        const button = (text, action) => {
            const node = element("button", text);
            node.type = "button";
            node.addEventListener("click", action);
            return node;
        };
        const picker = element("div");
        picker.className = "tournament-player-picker";
        select.after(picker);
        select.hidden = true;
        const revealed = new Set();
        const draw = [];
        const refresh = () => draw.forEach(render => render());
        for (const chosen of [false, true]) {
            const pane = element("section");
            const title = element("h3", chosen ? "Выбранные участники" : "Доступные игроки");
            const search = element("input");
            search.type = "search";
            search.placeholder = "Имя или фамилия";
            search.setAttribute("aria-label", title.textContent + " — поиск по имени");
            const city = element("input");
            city.type = "search";
            city.placeholder = "Поиск города";
            const suggestions = element("datalist");
            suggestions.id = select.id + "-cities-" + chosen;
            city.setAttribute("list", suggestions.id);
            city.setAttribute("aria-label", title.textContent + " — город");
            const count = element("span");
            count.setAttribute("role", "status");
            count.setAttribute("aria-atomic", "true");
            const list = element("ul");
            const move = options => {
                for (const option of options) option.selected = !chosen;
                select.dispatchEvent(new Event("change", {bubbles: true}));
            };
            let visible = [];
            const bulk = button(chosen ? "Убрать всех из списка ниже" : "Добавить всех из списка ниже", () => move(visible));
            const filters = element("div");
            filters.className = "participant-filters";
            filters.append(search, city, suggestions);
            const actions = element("div");
            actions.className = "participant-actions";
            actions.append(count, bulk);
            pane.append(title, filters, actions, list);
            picker.append(pane);
            function render() {
                const options = Array.from(select.options);
                const cityQuery = city.value.trim().toLowerCase();
                const exactCity = Array.from(suggestions.options).some(o => o.value.toLowerCase() === cityQuery);
                const words = search.value.trim().toLowerCase().split(/\s+/);
                visible = options.filter(o => o.selected === chosen &&
                    (exactCity ? cityName(o).toLowerCase() === cityQuery : cityName(o).toLowerCase().includes(cityQuery)) &&
                    words.every(word => o.text.toLowerCase().includes(word)));
                const summary = "Найдено игроков: " + visible.length;
                if (count.textContent !== summary) count.textContent = summary;
                bulk.disabled = !visible.length;
                list.replaceChildren();
                for (const option of visible) {
                    const row = element("li");
                    const label = element("span", [option.text, option.dataset.city].filter(Boolean).join(" · "));
                    const contact = element("span");
                    function showPhone() {
                        revealed.add(option.value);
                        const call = element("a", "Позвонить");
                        call.href = option.dataset.tel;
                        contact.replaceChildren(option.dataset.phone + " ", call);
                    }
                    if (!option.dataset.tel) contact.textContent = "Телефон не указан";
                    else if (revealed.has(option.value)) showPhone();
                    else contact.append(button("Показать телефон", showPhone));
                    row.append(label, contact, button(chosen ? "Убрать" : "Добавить", () => move([option])));
                    list.append(row);
                }
                if (!visible.length) list.append(element("li", search.value || cityQuery ? "Игроки не найдены" : "Список пуст"));
            }
            function cityName(option) {
                return option.dataset.city || "Город не указан";
            }
            function updateCities() {
                const names = [...new Set(Array.from(select.options, cityName))];
                names.sort((a, b) => a.localeCompare(b, "ru"));
                suggestions.replaceChildren(...names.map(name => new Option(name, name)));
                render();
            }
            draw.push(updateCities);
            search.addEventListener("input", render);
            city.addEventListener("input", render);
        }
        select.addEventListener("change", refresh);
        new MutationObserver(refresh).observe(select, {childList: true, subtree: true});
        refresh();
    }
});
