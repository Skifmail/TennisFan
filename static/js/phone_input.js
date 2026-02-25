/**
 * Поля телефона с фиксированным префиксом +7.
 * Класс .js-phone-input: префикс +7 не удаляется, ввод только цифр после него.
 */
(function () {
    var PREFIX = '+7';

    function getDigitsOnly(str) {
        return (str || '').replace(/\D/g, '');
    }

    function normalizePhoneDigits(digits) {
        if (digits.length === 0) return '';
        if (digits.charAt(0) === '8' && digits.length === 11) {
            digits = '7' + digits.slice(1);
        } else if (digits.length === 10 && digits.charAt(0) !== '7') {
            digits = '7' + digits;
        } else if (digits.charAt(0) === '7' && digits.length > 11) {
            digits = digits.slice(0, 11);
        } else if (digits.charAt(0) !== '7' && digits.length <= 10) {
            digits = '7' + digits.slice(0, 10);
        } else if (digits.length > 11) {
            digits = digits.slice(0, 11);
        }
        return digits;
    }

    function toDisplayValue(digits) {
        if (!digits || digits.length <= 1) return PREFIX + (digits.length ? ' ' + digits.slice(1) : ' ');
        var after7 = digits.slice(1);
        if (after7.length <= 3) return PREFIX + ' ' + after7;
        if (after7.length <= 6) return PREFIX + ' ' + after7.slice(0, 3) + ' ' + after7.slice(3);
        if (after7.length <= 8) return PREFIX + ' ' + after7.slice(0, 3) + ' ' + after7.slice(3, 6) + ' ' + after7.slice(6);
        after7 = after7.slice(0, 10);
        return PREFIX + ' ' + after7.slice(0, 3) + ' ' + after7.slice(3, 6) + ' ' + after7.slice(6, 8) + ' ' + after7.slice(8);
    }

    function applyToInput(input) {
        if (!input || input.type === 'hidden') return;

        function setValue(digits) {
            var normalized = normalizePhoneDigits(digits);
            var display = toDisplayValue(normalized);
            if (input.value !== display) {
                input.value = display;
            }
            return normalized;
        }

        function getValueDigits() {
            return getDigitsOnly(input.value);
        }

        // Инициализация при загрузке: если пусто — "+7 ", иначе нормализовать
        (function init() {
            var val = (input.value || '').trim();
            if (val.length === 0) {
                input.value = PREFIX + ' ';
                return;
            }
            var digits = getDigitsOnly(val);
            if (digits.length === 0) {
                input.value = PREFIX + ' ';
                return;
            }
            setValue(digits);
        })();

        input.addEventListener('focus', function () {
            var val = (this.value || '').trim();
            if (val.length === 0 || val.indexOf(PREFIX) !== 0) {
                this.value = PREFIX + ' ';
            }
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
            var digits = getValueDigits();
            setValue(digits);
            this.setSelectionRange(this.value.length, this.value.length);
        });

        input.addEventListener('blur', function () {
            var digits = getValueDigits();
            var normalized = normalizePhoneDigits(digits);
            if (normalized.length <= 1) {
                this.value = PREFIX + ' ';
                return;
            }
            this.value = toDisplayValue(normalized);
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('.js-phone-input').forEach(applyToInput);
    });
})();
