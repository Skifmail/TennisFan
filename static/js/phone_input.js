/**
 * Поля телефона с фиксированным префиксом +7.
 * Класс .js-phone-input: префикс +7 не удаляется, ввод только цифр после него.
 *
 * Внутри хранится «национальная» часть номера — 10 цифр без кода страны.
 * Если пользователь вставляет номер с кодом 7 или 8, код отбрасывается,
 * поэтому номер не превращается молча в +7899…
 */
(function () {
    var PREFIX = '+7';
    var NATIONAL_LENGTH = 10;

    function digitsOf(value) {
        return (value || '').replace(/\D/g, '');
    }

    /**
     * Выделить 10 цифр номера без кода страны.
     * @param {string} raw значение поля или вставленный текст
     * @returns {string}
     */
    function nationalDigits(raw) {
        var value = (raw || '').trim();
        var digits = digitsOf(value);
        if (!digits) return '';

        // Первая 7 — код страны из самого префикса.
        if (value.indexOf(PREFIX) === 0) {
            digits = digits.slice(1);
        }
        // Вставили полный номер: 8XXXXXXXXXX или 7XXXXXXXXXX.
        if (digits.length >= 11 && (digits.charAt(0) === '7' || digits.charAt(0) === '8')) {
            digits = digits.slice(1);
        }
        // Одинокая 8 после префикса — это забытый код страны, а не номер.
        if (digits === '8') {
            return '';
        }
        if (digits.length > NATIONAL_LENGTH) {
            digits = digits.slice(-NATIONAL_LENGTH);
        }
        return digits;
    }

    /**
     * Отформатировать национальные цифры как +7 999 123 45 67.
     * @param {string} national
     * @returns {string}
     */
    function formatPhone(national) {
        var n = (national || '').slice(0, NATIONAL_LENGTH);
        if (!n) return PREFIX + ' ';
        var groups = [];
        if (n.length > 0) groups.push(n.slice(0, 3));
        if (n.length > 3) groups.push(n.slice(3, 6));
        if (n.length > 6) groups.push(n.slice(6, 8));
        if (n.length > 8) groups.push(n.slice(8, 10));
        return PREFIX + ' ' + groups.join(' ');
    }

    function applyToInput(input) {
        if (!input || input.type === 'hidden') return;

        function render(national) {
            var formatted = formatPhone(national);
            if (input.value !== formatted) {
                input.value = formatted;
            }
        }

        (function init() {
            render(nationalDigits(input.value));
        })();

        input.addEventListener('focus', function () {
            // Не стираем вставленный номер без префикса — просто приводим к маске.
            render(nationalDigits(this.value));
            this.setSelectionRange(this.value.length, this.value.length);
        });

        input.addEventListener('keydown', function (e) {
            var selStart = this.selectionStart;
            var selEnd = this.selectionEnd;
            if (e.key === 'Backspace' && selStart <= PREFIX.length && selEnd <= PREFIX.length) {
                e.preventDefault();
            }
            if (e.key === 'Delete' && selStart < PREFIX.length && selEnd <= PREFIX.length) {
                e.preventDefault();
            }
        });

        input.addEventListener('input', function () {
            render(nationalDigits(this.value));
            this.setSelectionRange(this.value.length, this.value.length);
        });

        input.addEventListener('blur', function () {
            render(nationalDigits(this.value));
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('.js-phone-input').forEach(applyToInput);
    });
})();
