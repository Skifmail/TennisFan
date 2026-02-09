# Настройка восстановления и смены пароля

## Реализованный функционал

### ✅ Восстановление пароля (Password Reset)
- Страница запроса восстановления: `/users/password-reset/`
- Отправка email с токеном восстановления
- Страница ввода нового пароля: `/users/password-reset-confirm/<uidb64>/<token>/`
- Страница успешного завершения

### ✅ Смена пароля (Password Change)
- Страница смены пароля: `/users/password-change/` (требует авторизации)
- Страница успешного завершения
- Доступна из профиля пользователя

## Настройка Email для Production

### Вариант 1: SMTP сервер (Gmail, Yandex, Mail.ru и т.д.)

Добавьте в `.env` файл:

```env
# Email Backend
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend

# SMTP настройки (пример для Gmail)
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=your-email@gmail.com
EMAIL_HOST_PASSWORD=your-app-password

# Отправитель писем
DEFAULT_FROM_EMAIL=noreply@tennisfan.ru
SERVER_EMAIL=noreply@tennisfan.ru
```

**Важно для Gmail:**
- Используйте "Пароль приложения" вместо обычного пароля
- Включите двухфакторную аутентификацию в Google аккаунте
- Создайте пароль приложения: https://myaccount.google.com/apppasswords

**Для Yandex:**
```env
EMAIL_HOST=smtp.yandex.ru
EMAIL_PORT=465
EMAIL_USE_SSL=True
EMAIL_HOST_USER=your-email@yandex.ru
EMAIL_HOST_PASSWORD=your-password
```

**Для Mail.ru:**
```env
EMAIL_HOST=smtp.mail.ru
EMAIL_PORT=465
EMAIL_USE_SSL=True
EMAIL_HOST_USER=your-email@mail.ru
EMAIL_HOST_PASSWORD=your-password
```

### Вариант 2: Специализированные сервисы

#### SendGrid
```bash
pip install sendgrid-django
```

```env
EMAIL_BACKEND=sendgrid_backend.SendgridBackend
SENDGRID_API_KEY=your-sendgrid-api-key
DEFAULT_FROM_EMAIL=noreply@tennisfan.ru
```

#### Amazon SES
```bash
pip install django-ses
```

```env
EMAIL_BACKEND=django_ses.SESBackend
AWS_SES_REGION_NAME=us-east-1
AWS_SES_REGION_ENDPOINT=email.us-east-1.amazonaws.com
AWS_ACCESS_KEY_ID=your-access-key
AWS_SECRET_ACCESS_KEY=your-secret-key
DEFAULT_FROM_EMAIL=noreply@tennisfan.ru
```

#### Mailgun
```bash
pip install django-mailgun
```

```env
EMAIL_BACKEND=django_mailgun.MailgunBackend
MAILGUN_API_KEY=your-mailgun-api-key
MAILGUN_DOMAIN=your-domain.com
DEFAULT_FROM_EMAIL=noreply@your-domain.com
```

### Вариант 3: Development (консольный вывод)

Для локальной разработки письма выводятся в консоль:

```env
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
```

## Тестирование

### Проверка отправки email в development:

1. Запустите сервер: `python manage.py runserver`
2. Перейдите на `/users/password-reset/`
3. Введите email пользователя
4. Проверьте консоль - там должно появиться письмо с ссылкой

### Проверка в production:

1. Убедитесь, что все переменные окружения установлены
2. Протестируйте отправку через Django shell:
   ```python
   from django.core.mail import send_mail
   send_mail(
       'Test Subject',
       'Test message',
       'noreply@tennisfan.ru',
       ['test@example.com'],
       fail_silently=False,
   )
   ```

## Безопасность

- Токены восстановления пароля действительны **24 часа** (стандарт Django)
- Токены одноразовые - после использования становятся недействительными
- Ссылки содержат криптографически безопасные токены
- Email адреса проверяются на существование в базе данных

## Шаблоны писем

Шаблоны находятся в:
- `templates/users/password_reset_email.html` - тело письма
- `templates/users/password_reset_subject.txt` - тема письма

Вы можете настроить их под свой стиль.

## URL маршруты

Все маршруты определены в `apps/users/urls.py`:

- `password_reset` - запрос восстановления
- `password_reset_done` - подтверждение отправки
- `password_reset_confirm` - ввод нового пароля
- `password_reset_complete` - успешное завершение
- `password_change` - смена пароля (авторизованные)
- `password_change_done` - подтверждение смены

## Интеграция в интерфейс

- Ссылка "Забыли пароль?" добавлена на странице входа (`/users/auth/?mode=login`)
- Ссылка "Сменить пароль" добавлена в редактирование профиля (`/users/profile/edit/`)
