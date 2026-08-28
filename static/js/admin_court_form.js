/**
 * Защита формы корта в админке: проверка доступности выбранных файлов перед отправкой.
 * ERR_FILE_NOT_FOUND возникает, если фото выбрали давно, а ОС уже удалила временный файл.
 */
(function () {
    "use strict";

    var FILE_ERROR_MSG =
        "Не удалось прочитать выбранное фото (файл мог быть удалён или перемещён). " +
        "Выберите фото заново и нажмите «Сохранить» ещё раз. " +
        "Совет: сначала сохраните корт без фото, затем добавьте фото через «Изменить».";

    /**
     * @param {FileList} fileList
     * @returns {Promise<boolean>}
     */
    function filesReadable(fileList) {
        var checks = [];
        for (var i = 0; i < fileList.length; i += 1) {
            (function (file) {
                checks.push(
                    file.slice(0, 1).arrayBuffer().then(
                        function () {
                            return true;
                        },
                        function () {
                            return false;
                        }
                    )
                );
            })(fileList[i]);
        }
        return Promise.all(checks).then(function (results) {
            return results.every(Boolean);
        });
    }

    /**
     * @param {HTMLFormElement} form
     * @returns {Promise<HTMLInputElement|null>}
     */
    function findUnreadableFileInput(form) {
        var inputs = form.querySelectorAll('input[type="file"]');
        var chain = Promise.resolve(null);

        inputs.forEach(function (input) {
            chain = chain.then(function (found) {
                if (found || !input.files || input.files.length === 0) {
                    return found;
                }
                return filesReadable(input.files).then(function (ok) {
                    return ok ? null : input;
                });
            });
        });

        return chain;
    }

    document.addEventListener("DOMContentLoaded", function () {
        var form = document.getElementById("court_form");
        if (!form) {
            return;
        }

        form.addEventListener(
            "submit",
            function (event) {
                event.preventDefault();
                var nativeSubmit = HTMLFormElement.prototype.submit.bind(form);

                findUnreadableFileInput(form).then(function (badInput) {
                    if (badInput) {
                        window.alert(FILE_ERROR_MSG);
                        badInput.focus();
                        return;
                    }
                    nativeSubmit();
                });
            },
            true
        );
    });
})();
