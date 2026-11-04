/**
 * Ручное формирование пар турнира.
 *
 * Отвечает за скрытие уже распределённых участников, прогресс круга,
 * подтверждение сохранения и предупреждение о повторных парах.
 */
(function () {
    "use strict";

    function readJSON(id, fallback) {
        var node = document.getElementById(id);
        if (!node) {
            return fallback;
        }
        try {
            return JSON.parse(node.textContent);
        } catch (error) {
            return fallback;
        }
    }

    var played = readJSON("pairs-played", {});
    var initialPairs = readJSON("pairs-initial", []);
    var initialLeftover = readJSON("pairs-leftover", "");

    function pairKey(first, second) {
        return [first, second].sort().join("|");
    }

    function optionName(option) {
        if (!option) {
            return "";
        }
        return option.textContent.split("·")[0].trim();
    }

    function roundsLabel(rounds) {
        return rounds.map(function (round) {
            return String(round);
        }).join(", ");
    }

    function rematchMessage(firstName, secondName, rounds) {
        return (
            firstName +
            " — " +
            secondName +
            ": пара уже есть в круге " +
            roundsLabel(rounds)
        );
    }

    function clearValidity(select) {
        select.setCustomValidity("");
        select.classList.remove("pairs-select--invalid");
    }

    function setInvalid(select, message) {
        select.setCustomValidity(message);
        select.classList.add("pairs-select--invalid");
        select.reportValidity();
    }

    /* ------------------------------------------------------------------
     * Редактирование уже созданной пары
     * ------------------------------------------------------------------ */
    function initEditForms() {
        var forms = Array.prototype.slice.call(
            document.querySelectorAll("[data-pair-form]")
        );
        if (!forms.length) {
            return;
        }

        function syncEditForm(form) {
            var selects = Array.prototype.slice.call(
                form.querySelectorAll("[data-pair-select]")
            );

            selects.forEach(function (select) {
                Array.prototype.forEach.call(select.options, function (option) {
                    if (!option.value) {
                        return;
                    }
                    option.hidden = false;
                    option.disabled = false;
                });
                clearValidity(select);
            });

            var first = selects[0];
            var second = selects[1];
            var note = form.querySelector("[data-rematch-note]");
            var check = form.querySelector("[data-rematch-check]");
            var checkbox = check ? check.querySelector("input[type=checkbox]") : null;
            var isRematch = false;
            var text = "";

            if (first && second && first.value && second.value) {
                var key = pairKey(first.value, second.value);
                if (Object.prototype.hasOwnProperty.call(played, key)) {
                    isRematch = true;
                    text = rematchMessage(
                        optionName(first.selectedOptions[0]),
                        optionName(second.selectedOptions[0]),
                        played[key]
                    );
                }
            }

            if (note) {
                note.hidden = !isRematch;
                note.textContent = text;
            }
            if (check) {
                check.hidden = !isRematch;
            }
            if (checkbox && !isRematch) {
                checkbox.checked = false;
            }
        }

        forms.forEach(function (form) {
            form.addEventListener("change", function (event) {
                if (event.target.matches("[data-pair-select]")) {
                    syncEditForm(form);
                }
            });
            form.addEventListener("submit", function (event) {
                var selects = Array.prototype.slice.call(
                    form.querySelectorAll("[data-pair-select]")
                );
                var first = selects[0];
                var second = selects[1];
                selects.forEach(clearValidity);
                if (!first.value || !second.value) {
                    event.preventDefault();
                    setInvalid(
                        !first.value ? first : second,
                        "Выберите обоих участников пары."
                    );
                    return;
                }
                if (first.value === second.value) {
                    event.preventDefault();
                    setInvalid(second, "Участники в паре должны быть разными.");
                    return;
                }
                var key = pairKey(first.value, second.value);
                var check = form.querySelector("[data-rematch-check] input[type=checkbox]");
                if (
                    Object.prototype.hasOwnProperty.call(played, key) &&
                    check &&
                    !check.checked
                ) {
                    event.preventDefault();
                    check.focus();
                }
            });
            syncEditForm(form);
        });
    }

    /* ------------------------------------------------------------------
     * Конструктор нового круга
     * ------------------------------------------------------------------ */
    function initBuilder() {
        var form = document.querySelector("[data-pairs-form]");
        if (!form) {
            return;
        }
        var rowsContainer = form.querySelector("[data-pairs-rows]");
        var addButton = form.querySelector("[data-add-row]");
        var progressBar = form.querySelector("[data-progress-bar]");
        var progressText = form.querySelector("[data-progress-text]");
        var leftoverInput = form.querySelector("[data-leftover-input]");
        var builderNote = form.querySelector("[data-builder-rematch]");
        var allowRematchInput = form.querySelector("[data-allow-rematch]");
        var dialog = document.getElementById("pairs-confirm-dialog");
        var rowCounter = 0;
        var confirming = false;

        if (!rowsContainer) {
            return;
        }

        // Полный список участников запоминаем до первой синхронизации:
        // дальше списки пересобираются, чтобы уже выбранные исчезали
        // из остальных выпадающих списков во всех браузерах.
        var masterOptions = (function () {
            var first = form.querySelector("[data-builder-select]");
            if (!first) {
                return [];
            }
            return Array.prototype.filter
                .call(first.options, function (option) {
                    return Boolean(option.value);
                })
                .map(function (option) {
                    return {
                        value: option.value,
                        label: option.textContent,
                        strength: option.dataset.strength || "",
                    };
                });
        }());

        function allRows() {
            return Array.prototype.slice.call(
                rowsContainer.querySelectorAll("[data-pair-row]")
            );
        }

        function rowSelects(row) {
            return Array.prototype.slice.call(
                row.querySelectorAll("[data-builder-select]")
            );
        }

        function allSelects() {
            return Array.prototype.slice.call(
                form.querySelectorAll("[data-builder-select]")
            );
        }

        function createOption(optionData) {
            var option = document.createElement("option");
            option.value = optionData.value;
            option.textContent = optionData.label;
            if (optionData.strength) {
                option.dataset.strength = optionData.strength;
            }
            return option;
        }

        function rebuildSelect(select, usage) {
            var current = select.value;
            while (select.options.length) {
                select.remove(0);
            }
            var blank = document.createElement("option");
            blank.value = "";
            blank.textContent = "— выберите —";
            select.appendChild(blank);

            masterOptions.forEach(function (optionData) {
                var count = usage.get(optionData.value) || 0;
                var usedByOther =
                    count > 0 && !(current === optionData.value && count === 1);
                if (usedByOther) {
                    return;
                }
                var option = createOption(optionData);
                if (current === optionData.value) {
                    option.selected = true;
                }
                select.appendChild(option);
            });
            select.value = current;
        }

        function updateRowNumbers() {
            var rows = allRows();
            rows.forEach(function (row, index) {
                var number = row.querySelector("[data-row-number]");
                if (number) {
                    number.textContent = String(index + 1);
                }
                // Имена полей должны идти подряд: сервер читает пары
                // с pair_1 и останавливается на первом пропуске.
                rowSelects(row).forEach(function (select) {
                    var match = select.name.match(/^pair_\d+_([ab])$/);
                    if (match) {
                        select.name = "pair_" + (index + 1) + "_" + match[1];
                    }
                });
                var remove = row.querySelector("[data-remove-row]");
                if (remove) {
                    remove.disabled = rows.length === 1;
                }
            });
            rowCounter = rows.length;
        }

        function rowsWithValues() {
            var result = [];
            allRows().forEach(function (row) {
                var selects = rowSelects(row);
                if (!selects[0] || !selects[1]) {
                    return;
                }
                if (selects[0].value && selects[1].value) {
                    result.push({
                        first: selects[0],
                        second: selects[1],
                        firstToken: selects[0].value,
                        secondToken: selects[1].value,
                    });
                }
            });
            return result;
        }

        function findRematches() {
            var rematches = [];
            rowsWithValues().forEach(function (pair) {
                var key = pairKey(pair.firstToken, pair.secondToken);
                if (Object.prototype.hasOwnProperty.call(played, key)) {
                    rematches.push({
                        firstName: optionName(pair.first.selectedOptions[0]),
                        secondName: optionName(pair.second.selectedOptions[0]),
                        rounds: played[key],
                    });
                }
            });
            return rematches;
        }

        function sync() {
            var selects = allSelects();
            var usage = new Map();
            selects.forEach(function (select) {
                if (select.value) {
                    usage.set(select.value, (usage.get(select.value) || 0) + 1);
                }
            });

            selects.forEach(function (select) {
                rebuildSelect(select, usage);
            });

            var total = masterOptions.length;
            var distributed = usage.size;
            if (progressText) {
                progressText.textContent =
                    "Распределено " + distributed + " из " + total;
            }
            if (progressBar) {
                var ratio = total > 0 ? distributed / total : 0;
                progressBar.style.transform = "scaleX(" + ratio + ")";
            }

            if (leftoverInput) {
                var available = masterOptions.filter(function (option) {
                    return !usage.has(option.value);
                });
                var currentLeftover = leftoverInput.value;
                if (available.length === 1) {
                    leftoverInput.value = available[0].value;
                } else if (
                    currentLeftover &&
                    !available.some(function (option) {
                        return option.value === currentLeftover;
                    })
                ) {
                    // Выбранный отдыхающий уже поставлен в пару.
                    leftoverInput.value = "";
                }
            }

            var rematches = findRematches();
            if (builderNote) {
                builderNote.hidden = rematches.length === 0;
                builderNote.textContent = rematches.length
                    ? "Повторные пары: " +
                      rematches
                          .map(function (item) {
                              return rematchMessage(
                                  item.firstName,
                                  item.secondName,
                                  item.rounds
                              );
                          })
                          .join("; ") +
                      ". Подтвердите сохранение в окне проверки."
                    : "";
            }

            updateRowNumbers();
        }

        function addRow() {
            var rows = allRows();
            if (!rows.length) {
                return;
            }
            var clone = rows[0].cloneNode(true);
            rowCounter += 1;
            clone.querySelectorAll("select").forEach(function (select) {
                var match = select.name.match(/^pair_\d+_([ab])$/);
                select.name = "pair_" + rowCounter + "_" + (match ? match[1] : "a");
                select.value = "";
                clearValidity(select);
            });
            rowsContainer.appendChild(clone);
            updateRowNumbers();
            sync();
        }

        function ensureRows(count) {
            while (allRows().length < count) {
                addRow();
            }
        }

        function fillInitialValues() {
            ensureRows(initialPairs.length);
            var rows = allRows();
            rows.forEach(function (row) {
                var selects = rowSelects(row);
                selects.forEach(function (select) {
                    select.value = "";
                });
            });
            initialPairs.forEach(function (pair, index) {
                var row = rows[index];
                if (!row) {
                    return;
                }
                var selects = rowSelects(row);
                if (selects[0] && pair.a) {
                    selects[0].value = pair.a;
                }
                if (selects[1] && pair.b) {
                    selects[1].value = pair.b;
                }
            });
        }

        function buildSummary() {
            var dialogList = dialog.querySelector("[data-summary-list]");
            var dialogLeftover = dialog.querySelector("[data-summary-leftover]");
            var warning = dialog.querySelector("[data-summary-warning]");
            var warningList = dialog.querySelector("[data-summary-rematches]");
            var rematchCheckbox = dialog.querySelector("[data-confirm-rematch]");
            var confirmButton = dialog.querySelector("[data-dialog-confirm]");
            var rematches = findRematches();

            dialogList.innerHTML = "";
            rowsWithValues().forEach(function (pair) {
                var item = document.createElement("li");
                item.textContent =
                    optionName(pair.first.selectedOptions[0]) +
                    " — " +
                    optionName(pair.second.selectedOptions[0]);
                dialogList.appendChild(item);
            });

            if (dialogLeftover && leftoverInput) {
                if (leftoverInput.value) {
                    var selected = leftoverInput.closest("form");
                    var option = selected
                        ? selected.querySelector(
                              'option[value="' + leftoverInput.value + '"]'
                          )
                        : null;
                    dialogLeftover.hidden = false;
                    dialogLeftover.textContent =
                        "Отдыхает в этом круге: " + optionName(option);
                } else {
                    dialogLeftover.hidden = true;
                    dialogLeftover.textContent = "";
                }
            }

            warningList.innerHTML = "";
            if (rematches.length) {
                warning.hidden = false;
                rematches.forEach(function (item) {
                    var row = document.createElement("li");
                    row.textContent = rematchMessage(
                        item.firstName,
                        item.secondName,
                        item.rounds
                    );
                    warningList.appendChild(row);
                });
                if (rematchCheckbox) {
                    rematchCheckbox.checked = false;
                }
                if (confirmButton) {
                    confirmButton.disabled = true;
                }
            } else {
                warning.hidden = true;
                if (confirmButton) {
                    confirmButton.disabled = false;
                }
            }
        }

        function validatePairs() {
            var rows = allRows();
            var firstInvalid = null;
            rows.forEach(function (row) {
                var selects = rowSelects(row);
                if (!selects[0] || !selects[1]) {
                    return;
                }
                clearValidity(selects[0]);
                clearValidity(selects[1]);
                var first = selects[0].value;
                var second = selects[1].value;
                if (!first && !second) {
                    return;
                }
                if (!first || !second) {
                    var target = first ? selects[1] : selects[0];
                    target.setCustomValidity("Выберите второго участника пары.");
                    target.classList.add("pairs-select--invalid");
                    if (!firstInvalid) {
                        firstInvalid = target;
                    }
                } else if (first === second) {
                    selects[1].setCustomValidity(
                        "Участники в паре должны быть разными."
                    );
                    selects[1].classList.add("pairs-select--invalid");
                    if (!firstInvalid) {
                        firstInvalid = selects[1];
                    }
                }
            });
            if (firstInvalid) {
                firstInvalid.reportValidity();
                return false;
            }
            if (!rowsWithValues().length) {
                var emptySelect = allSelects()[0];
                if (emptySelect) {
                    setInvalid(emptySelect, "Добавьте хотя бы одну пару.");
                }
                return false;
            }
            return true;
        }

        addButton.addEventListener("click", addRow);

        rowsContainer.addEventListener("click", function (event) {
            var button = event.target.closest("[data-remove-row]");
            if (!button) {
                return;
            }
            var row = button.closest("[data-pair-row]");
            var rows = allRows();
            if (rows.length === 1) {
                rowSelects(row).forEach(function (select) {
                    select.value = "";
                    clearValidity(select);
                });
            } else {
                row.remove();
            }
            sync();
        });

        form.addEventListener("change", function (event) {
            if (event.target.matches("[data-builder-select]")) {
                clearValidity(event.target);
                sync();
            }
        });

        var fillButton = form.querySelector("[data-fill-by-strength]");
        if (fillButton) {
            fillButton.addEventListener("click", function () {
                var pool = masterOptions.slice();
                var pairs = [];
                var left = 0;
                var right = pool.length - 1;
                while (left < right) {
                    pairs.push([pool[left].value, pool[right].value]);
                    left += 1;
                    right -= 1;
                }
                ensureRows(pairs.length);
                var rows = allRows();
                rows.forEach(function (row, index) {
                    var selects = rowSelects(row);
                    if (pairs[index]) {
                        selects[0].value = pairs[index][0];
                        selects[1].value = pairs[index][1];
                    } else {
                        selects[0].value = "";
                        selects[1].value = "";
                    }
                    clearValidity(selects[0]);
                    clearValidity(selects[1]);
                });
                sync();
            });
        }

        var clearButton = form.querySelector("[data-clear-pairs]");
        if (clearButton) {
            clearButton.addEventListener("click", function () {
                allSelects().forEach(function (select) {
                    select.value = "";
                    clearValidity(select);
                });
                sync();
            });
        }

        var rematchCheckbox = dialog
            ? dialog.querySelector("[data-confirm-rematch]")
            : null;
        var confirmButton = dialog
            ? dialog.querySelector("[data-dialog-confirm]")
            : null;
        if (rematchCheckbox && confirmButton) {
            rematchCheckbox.addEventListener("change", function () {
                confirmButton.disabled = !rematchCheckbox.checked;
            });
        }

        form.addEventListener("submit", function (event) {
            if (confirming) {
                return;
            }
            if (!validatePairs()) {
                event.preventDefault();
                return;
            }
            if (!dialog) {
                return;
            }
            event.preventDefault();
            buildSummary();
            dialog.showModal();
        });

        if (confirmButton && dialog) {
            confirmButton.addEventListener("click", function () {
                confirming = true;
                if (allowRematchInput) {
                    var checkbox = dialog.querySelector("[data-confirm-rematch]");
                    allowRematchInput.value =
                        checkbox && checkbox.checked ? "1" : "0";
                }
                dialog.close();
                form.requestSubmit();
            });
        }

        var cancelButton = dialog
            ? dialog.querySelector("[data-dialog-cancel]")
            : null;
        if (cancelButton && dialog) {
            cancelButton.addEventListener("click", function () {
                dialog.close();
            });
        }

        rowCounter = allRows().length;
        fillInitialValues();
        if (leftoverInput && initialLeftover) {
            leftoverInput.value = initialLeftover;
        }
        sync();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", function () {
            initEditForms();
            initBuilder();
        });
    } else {
        initEditForms();
        initBuilder();
    }
})();
