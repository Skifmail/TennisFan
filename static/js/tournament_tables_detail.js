/**
 * Стат-дашборд турнирной таблицы: Chart.js, count-up KPI, reveal-анимации.
 * Конфигурация: элемент #tournament-tables-charts-config (JSON).
 */
(function () {
    "use strict";

    function parseConfig() {
        var cfgEl = document.getElementById("tournament-tables-charts-config");
        if (!cfgEl || !cfgEl.textContent.trim()) {
            return null;
        }
        try {
            return JSON.parse(cfgEl.textContent);
        } catch (_e) {
            return null;
        }
    }

    function staggeredAnimation(delayStep) {
        return {
            duration: 900,
            easing: "easeOutQuart",
            delay: function (ctx) {
                return (ctx.dataIndex || 0) * (delayStep || 40);
            },
        };
    }

    function makeGradient(ctx, colorTop, colorBottom) {
        var chart = ctx.chart;
        var area = chart.chartArea;
        if (!area) {
            return colorTop;
        }
        var g = chart.ctx.createLinearGradient(0, area.top, 0, area.bottom);
        g.addColorStop(0, colorTop);
        g.addColorStop(1, colorBottom);
        return g;
    }

    function baseScaleOptions(colors) {
        return {
            grid: { color: colors.grid || "rgba(148, 163, 184, 0.15)" },
            ticks: { color: colors.text || "#94a3b8" },
        };
    }

    function initCharts(cfg) {
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
        var scaleOpts = baseScaleOptions(colors);

        var elTimeline = document.getElementById("chartTimeline");
        if (
            elTimeline &&
            cfg.timeline &&
            cfg.timeline.labels &&
            cfg.timeline.labels.length
        ) {
            new ChartCtor(elTimeline, {
                type: "line",
                data: {
                    labels: cfg.timeline.labels,
                    datasets: [
                        {
                            label: "За день",
                            data: cfg.timeline.daily,
                            borderColor: colors.primary,
                            backgroundColor: function (ctx) {
                                return makeGradient(
                                    ctx,
                                    "rgba(166, 130, 74, 0.45)",
                                    "rgba(166, 130, 74, 0.02)"
                                );
                            },
                            fill: true,
                            tension: 0.35,
                            pointRadius: 3,
                            pointHoverRadius: 5,
                            yAxisID: "y",
                        },
                        {
                            label: "Накопительно",
                            data: cfg.timeline.cumulative,
                            borderColor: "#2d5a27",
                            backgroundColor: "transparent",
                            borderDash: [6, 4],
                            tension: 0.25,
                            pointRadius: 0,
                            yAxisID: "y1",
                        },
                    ],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: { mode: "index", intersect: false },
                    animation: staggeredAnimation(30),
                    plugins: {
                        legend: {
                            position: "bottom",
                            labels: { color: colors.text || "#94a3b8" },
                        },
                    },
                    scales: {
                        x: scaleOpts,
                        y: Object.assign(
                            { beginAtZero: true, ticks: { stepSize: 1 } },
                            scaleOpts
                        ),
                        y1: Object.assign(
                            {
                                beginAtZero: true,
                                position: "right",
                                grid: { drawOnChartArea: false },
                                ticks: {
                                    color: colors.text || "#94a3b8",
                                    stepSize: 1,
                                },
                            },
                            {}
                        ),
                    },
                },
            });
        }

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
                            hoverOffset: 8,
                        },
                    ],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: "58%",
                    animation: staggeredAnimation(60),
                    plugins: {
                        legend: {
                            position: "bottom",
                            labels: { color: colors.text || "#94a3b8" },
                        },
                    },
                },
            });
        }

        var elCharacter = document.getElementById("chartCharacter");
        if (
            elCharacter &&
            cfg.character &&
            cfg.character.labels &&
            cfg.character.labels.length
        ) {
            new ChartCtor(elCharacter, {
                type: "doughnut",
                data: {
                    labels: cfg.character.labels,
                    datasets: [
                        {
                            data: cfg.character.data,
                            backgroundColor: [
                                "#A6824A",
                                "#2d5a27",
                                "#6b7280",
                            ],
                            borderColor: colors.border || "#16302B",
                            borderWidth: 2,
                            hoverOffset: 8,
                        },
                    ],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: "55%",
                    animation: staggeredAnimation(60),
                    plugins: {
                        legend: {
                            position: "bottom",
                            labels: { color: colors.text || "#94a3b8" },
                        },
                    },
                },
            });
        }

        var elSets = document.getElementById("chartSetScores");
        if (
            elSets &&
            cfg.setScores &&
            cfg.setScores.labels &&
            cfg.setScores.labels.length
        ) {
            new ChartCtor(elSets, {
                type: "bar",
                data: {
                    labels: cfg.setScores.labels,
                    datasets: [
                        {
                            label: "Сетов",
                            data: cfg.setScores.data,
                            backgroundColor: "rgba(166, 130, 74, 0.7)",
                            borderColor: colors.primary,
                            borderWidth: 1,
                            borderRadius: 6,
                        },
                    ],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: staggeredAnimation(45),
                    plugins: { legend: { display: false } },
                    scales: {
                        x: scaleOpts,
                        y: Object.assign(
                            { beginAtZero: true, ticks: { stepSize: 1 } },
                            scaleOpts
                        ),
                    },
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
                            borderRadius: 6,
                        },
                    ],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: staggeredAnimation(50),
                    plugins: { legend: { display: false } },
                    scales: {
                        x: scaleOpts,
                        y: Object.assign(
                            { beginAtZero: true, ticks: { stepSize: 1 } },
                            scaleOpts
                        ),
                    },
                },
            });
        }

        var elDeltas = document.getElementById("chartRatingDeltas");
        if (
            elDeltas &&
            cfg.ratingDeltas &&
            cfg.ratingDeltas.labels &&
            cfg.ratingDeltas.labels.length
        ) {
            var deltaColors = (cfg.ratingDeltas.data || []).map(function (v) {
                return v >= 0
                    ? "rgba(45, 90, 39, 0.75)"
                    : "rgba(155, 58, 58, 0.75)";
            });
            new ChartCtor(elDeltas, {
                type: "bar",
                data: {
                    labels: cfg.ratingDeltas.labels,
                    datasets: [
                        {
                            label: "Δ рейтинга",
                            data: cfg.ratingDeltas.data,
                            backgroundColor: deltaColors,
                            borderWidth: 0,
                            borderRadius: 6,
                        },
                    ],
                },
                options: {
                    indexAxis: "y",
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: staggeredAnimation(35),
                    plugins: { legend: { display: false } },
                    scales: {
                        x: Object.assign({ beginAtZero: true }, scaleOpts),
                        y: scaleOpts,
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
                            borderRadius: 4,
                        },
                    ],
                },
                options: {
                    indexAxis: "y",
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: staggeredAnimation(25),
                    plugins: { legend: { display: false } },
                    scales: {
                        x: Object.assign({ beginAtZero: true }, scaleOpts),
                        y: scaleOpts,
                    },
                },
            });
        }
    }

    function animateCountUp(el) {
        var target = parseFloat(el.getAttribute("data-count-up") || "0");
        if (isNaN(target)) {
            return;
        }
        var decimals = parseInt(el.getAttribute("data-decimals") || "0", 10);
        var suffix = el.getAttribute("data-suffix") || "";
        var duration = 900;
        var start = null;

        function frame(ts) {
            if (start === null) {
                start = ts;
            }
            var p = Math.min(1, (ts - start) / duration);
            var eased = 1 - Math.pow(1 - p, 3);
            var value = target * eased;
            if (decimals > 0) {
                el.textContent = value.toFixed(decimals) + suffix;
            } else {
                el.textContent = Math.round(value) + suffix;
            }
            if (p < 1) {
                window.requestAnimationFrame(frame);
            }
        }
        window.requestAnimationFrame(frame);
    }

    function initProgressRing(el) {
        var pct = parseFloat(el.getAttribute("data-progress") || "0");
        var circle = el.querySelector(".stat-progress-ring__value");
        if (!circle) {
            return;
        }
        var radius = 30;
        var circumference = 2 * Math.PI * radius;
        circle.style.strokeDasharray = String(circumference);
        circle.style.strokeDashoffset = String(circumference);
        window.requestAnimationFrame(function () {
            var offset = circumference * (1 - Math.min(100, Math.max(0, pct)) / 100);
            circle.style.strokeDashoffset = String(offset);
        });
    }

    function initRevealAndCounters() {
        var revealEls = document.querySelectorAll("[data-reveal]");
        var countEls = document.querySelectorAll("[data-count-up]");
        var rings = document.querySelectorAll(".stat-progress-ring");

        if (!("IntersectionObserver" in window)) {
            revealEls.forEach(function (el) {
                el.classList.add("is-revealed");
            });
            countEls.forEach(animateCountUp);
            rings.forEach(initProgressRing);
            return;
        }

        var revealObs = new IntersectionObserver(
            function (entries) {
                entries.forEach(function (entry) {
                    if (entry.isIntersecting) {
                        entry.target.classList.add("is-revealed");
                        revealObs.unobserve(entry.target);
                    }
                });
            },
            { threshold: 0.12, rootMargin: "0px 0px -40px 0px" }
        );
        revealEls.forEach(function (el) {
            revealObs.observe(el);
        });

        var counted = false;
        var countObs = new IntersectionObserver(
            function (entries) {
                entries.forEach(function (entry) {
                    if (!entry.isIntersecting || counted) {
                        return;
                    }
                    counted = true;
                    countEls.forEach(animateCountUp);
                    rings.forEach(initProgressRing);
                    countObs.disconnect();
                });
            },
            { threshold: 0.25 }
        );
        var kpiSection = document.querySelector(".tables-stats-cards");
        if (kpiSection) {
            countObs.observe(kpiSection);
        } else {
            countEls.forEach(animateCountUp);
            rings.forEach(initProgressRing);
        }
    }

    document.addEventListener("DOMContentLoaded", function () {
        var cfg = parseConfig();
        if (cfg) {
            initCharts(cfg);
        }
        initRevealAndCounters();
    });
})();
