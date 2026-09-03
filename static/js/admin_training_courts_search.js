(function () {
    function initCourtSearch() {
        var select = document.getElementById('id_courts');
        if (!select) {
            return;
        }

        // Создаём поле поиска над селектом
        var search = document.createElement('input');
        search.type = 'text';
        search.className = 'vTextField';
        search.placeholder = 'Поиск корта по названию или населённому пункту';
        search.style.marginBottom = '4px';

        var container = select.parentNode;
        container.insertBefore(search, select);

        var allOptions = Array.prototype.map.call(select.options, function (opt) {
            return { value: opt.value, text: opt.text };
        });

        function applyFilter() {
            var q = search.value.toLowerCase().trim();
            var selected = new Set(Array.prototype.map.call(select.selectedOptions, function (o) { return o.value; }));

            // Пересобираем список опций
            select.innerHTML = '';
            allOptions.forEach(function (opt) {
                var matches = !q || opt.text.toLowerCase().indexOf(q) !== -1;
                var isSelected = selected.has(opt.value);
                if (matches || isSelected) {
                    var o = new Option(opt.text, opt.value, false, isSelected);
                    select.add(o);
                }
            });
        }

        search.addEventListener('input', applyFilter);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initCourtSearch);
    } else {
        initCourtSearch();
    }
})();
