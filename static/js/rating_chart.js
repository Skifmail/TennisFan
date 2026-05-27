/**
 * FAN-рейтинг: интерактивная диаграмма на странице /rating/.
 */
(function () {
    'use strict';

    function formatRating(value) {
        return value.toLocaleString('ru-RU', {
            minimumFractionDigits: 1,
            maximumFractionDigits: 1,
        });
    }

    function createEl(tag, className, text) {
        var el = document.createElement(tag);
        if (className) {
            el.className = className;
        }
        if (text !== undefined && text !== null) {
            el.textContent = text;
        }
        return el;
    }

    function renderSummary(container, data, leaderPoints) {
        var header = createEl('header', 'fan-chart__header');
        var titleBlock = createEl('div', 'fan-chart__intro');
        titleBlock.appendChild(createEl('p', 'fan-chart__eyebrow', 'внутриплатформенный рейтинг'));
        titleBlock.appendChild(createEl('h2', 'fan-chart__title', 'ТОП-' + data.length + ' игроков'));
        titleBlock.appendChild(
            createEl(
                'p',
                'fan-chart__subtitle',
                'Количество очков FAN на текущей странице'
            )
        );
        header.appendChild(titleBlock);

        var leader = createEl(
            'div',
            'fan-chart__leader-value',
            formatRating(leaderPoints) + ' pts'
        );
        header.appendChild(leader);
        container.appendChild(header);
    }

    function renderRow(item, index) {
        var li = createEl('li', 'fan-chart__item');
        li.style.setProperty('--fan-chart-index', String(index));

        var link = document.createElement('a');
        link.className = 'fan-chart__row';
        link.href = item.profile_url;
        link.setAttribute('aria-label', 'Профиль: ' + item.name);
        link.title = 'Открыть профиль: ' + item.name;

        var rank = createEl('span', 'fan-chart__rank', String(item.rank));
        link.appendChild(rank);

        var name = createEl('span', 'fan-chart__name', item.name);
        link.appendChild(name);

        var track = createEl('div', 'fan-chart__track');
        track.setAttribute('role', 'presentation');
        var fill = createEl('div', 'fan-chart__fill');
        fill.style.width = '0%';
        fill.dataset.targetWidth = String(item.share);
        track.appendChild(fill);
        link.appendChild(track);

        var value = createEl('span', 'fan-chart__value', formatRating(item.points));
        link.appendChild(value);

        li.appendChild(link);
        return li;
    }

    function animateBars(container) {
        window.requestAnimationFrame(function () {
            container.querySelectorAll('.fan-chart__fill').forEach(function (fill) {
                var target = fill.dataset.targetWidth || '0';
                fill.style.width = target + '%';
            });
        });
    }

    function renderChart(container, data) {
        container.innerHTML = '';
        container.classList.remove('fan-chart--empty', 'fan-chart--error');

        if (!data.length) {
            container.classList.add('fan-chart--empty');
            container.appendChild(createEl('p', 'fan-chart__message', 'Игроки не найдены.'));
            return;
        }

        var validData = data.filter(function (item) {
            return item && typeof item.points === 'number' && !isNaN(item.points);
        });
        if (!validData.length) {
            container.classList.add('fan-chart--empty');
            container.appendChild(
                createEl('p', 'fan-chart__message', 'Нет данных для отображения.')
            );
            return;
        }

        var leaderPoints = Math.max.apply(
            null,
            validData.map(function (item) {
                return item.points;
            })
        );

        renderSummary(container, validData, leaderPoints);

        var list = createEl('ol', 'fan-chart__list');
        validData.forEach(function (item, index) {
            list.appendChild(renderRow(item, index));
        });
        container.appendChild(list);

        animateBars(container);
    }

    function initRatingChart() {
        var dataEl = document.getElementById('rating-data');
        var toggleBtn = document.getElementById('rating-toggle-btn');
        var listEl = document.getElementById('rating-list');
        var chartWrap = document.getElementById('rating-chart');
        var chartRoot = document.getElementById('rating-chart-bars');

        if (!toggleBtn || !listEl || !chartWrap || !chartRoot) {
            return;
        }

        var ratingData = [];
        if (dataEl) {
            try {
                ratingData = JSON.parse(dataEl.textContent);
            } catch (err) {
                console.error('Rating chart: invalid JSON', err);
            }
        }

        function showError() {
            chartRoot.innerHTML = '';
            chartRoot.classList.add('fan-chart--error');
            chartRoot.appendChild(
                createEl('p', 'fan-chart__message fan-chart__message--error', 'Ошибка при отображении диаграммы.')
            );
        }

        toggleBtn.addEventListener('click', function () {
            var isHidden = chartWrap.classList.contains('hidden');
            chartWrap.classList.toggle('hidden');
            listEl.classList.toggle('hidden');
            if (isHidden) {
                try {
                    renderChart(chartRoot, ratingData);
                    toggleBtn.textContent = 'Показать списком';
                } catch (err) {
                    console.error('Rating chart render error:', err);
                    showError();
                }
            } else {
                toggleBtn.textContent = 'Показать в виде диаграммы';
            }
        });
    }

    document.addEventListener('DOMContentLoaded', initRatingChart);
})();
