/**
 * Форма рассылки в Telegram: не даём отправить форму по Enter.
 * В части браузеров/мобильных клавиш Enter в поле текста отправляет форму,
 * пользователь попадает на страницу «объект с ID add не существует».
 * Разрешаем отправку только по явному клику по кнопке «Сохранить».
 */
(function() {
  'use strict';
  var path = window.location.pathname || '';
  if (path.indexOf('/telegram_bot/telegrambroadcast/add') === -1) {
    return;
  }
  var form = document.querySelector('form');
  if (!form) return;

  form.addEventListener('submit', function(e) {
    var submitter = e.submitter;
    if (!submitter || (submitter.type !== 'submit' && submitter.type !== 'image')) {
      e.preventDefault();
      return false;
    }
  });
})();
