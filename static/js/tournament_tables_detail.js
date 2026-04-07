/**
 * Инициализация диаграмм Chart.js на странице турнирной таблицы.
 * Конфигурация: элемент #tournament-tables-charts-config (JSON).
 */
(function () {
    "use strict";

    function initTournamentTablesCharts() {
        var cfgEl = document.getElementById("tournament-tables-charts-config");
        if (!cfgEl || !cfgEl.textContent.trim()) {
            return;
        }
        var cfg;
        try {
            cfg = JSON.parse(cfgEl.textContent);
        } catch (_e) {
            return;
        }
        var ChartCtor = window.Chart;
        if (!ChartCtor) {
            return;
        }
        var colors = cfg.colors || {};
        var palette = colors.palette || [
            "#A6824A",
            "#83530c",
            "#2d5a27",
            "#6b7280",
            "#9ca3af",
        ];

        var elStatus = document.getElementById("chartStatus");
        if (
            elStatus &&
            cfg.status &&
            cfg.status.labels &&
            cfg.status.labels.length
        ) {
            new ChartCtor(elStatus, {
                type: "doughnut",
                data: {
                    labels: cfg.status.labels,
                    datasets: [
                        {
                            data: cfg.status.data,
                            backgroundColor: palette,
                            borderColor: colors.border || "#16302B",
                            borderWidth: 2,
                        },
                    ],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: true,
                    plugins: { legend: { position: "bottom" } },
                },
            });
        }

        var elRounds = document.getElementById("chartRounds");
        if (
            elRounds &&
            cfg.rounds &&
            cfg.rounds.labels &&
            cfg.rounds.labels.length
        ) {
            new ChartCtor(elRounds, {
                type: "bar",
                data: {
                    labels: cfg.rounds.labels,
                    datasets: [
                        {
                            label: "Участников",
                            data: cfg.rounds.data,
                            backgroundColor: colors.primary,
                            borderColor: colors.accent,
                            borderWidth: 1,
                        },
                    ],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: true,
                    plugins: { legend: { display: false } },
                    scales: {
                        y: { beginAtZero: true, ticks: { stepSize: 1 } },
                    },
                },
            });
        }

        var elRatings = document.getElementById("chartRatings");
        if (
            elRatings &&
            cfg.ratings &&
            cfg.ratings.labels &&
            cfg.ratings.labels.length
        ) {
            new ChartCtor(elRatings, {
                type: "bar",
                data: {
                    labels: cfg.ratings.labels,
                    datasets: [
                        {
                            label: "Рейтинг",
                            data: cfg.ratings.data,
                            backgroundColor: "rgba(166, 130, 74, 0.6)",
                            borderColor: colors.primary,
                            borderWidth: 1,
                        },
                    ],
                },
                options: {
                    indexAxis: "y",
                    responsive: true,
                    maintainAspectRatio: true,
                    plugins: { legend: { display: false } },
                    scales: { x: { beginAtZero: true } },
                },
            });
        }
    }

    document.addEventListener("DOMContentLoaded", initTournamentTablesCharts);
})();
