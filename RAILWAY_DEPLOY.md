# Деплой на Railway

## Инструкция по деплою

### 1. Подготовка репозитория
```bash
git init
git add .
git commit -m "Initial commit"
```

### 2. Создание проекта на Railway
1. Перейди на https://railway.app
2. Нажми "New Project"
3. Выбери "Deploy from GitHub" и подключи репозиторий
4. Выбери ветку для деплоя

### 3. Переменные окружения в Railway
В панели Railway установи переменные:
- `SECRET_KEY` - сгенерируй новый ключ (можно на https://djecrety.ir/)
- `DEBUG` = False
- `ALLOWED_HOSTS` = yourdomain.railway.app
- `PYTHON_VERSION` = 3.12.3
- `CSRF_TRUSTED_ORIGINS` = https://yourdomain.railway.app,https://*.railway.app

### 4. Статические файлы
- В Procfile добавлен шаг `collectstatic` в release: миграции + сборка статики
- WhiteNoise раздаёт файлы из `staticfiles/`
- Убедись, что `STATIC_URL` начинается с `/static/` (уже настроено)

### 5. Файлы уже готовы
✅ `Procfile` - определяет как запустить приложение
✅ `runtime.txt` - указывает версию Python
✅ `requirements.txt` - обновлён с gunicorn и whitenoise
✅ `.railwayignore` - файлы для исключения при деплое

### 6. База данных
Railway автоматически создаст SQLite базу. Миграции выполнятся автоматически в фазе `release`.

### 7. Проверка деплоя
После деплоя проверь логи в Railway для ошибок.

## Потенциальные проблемы и решения

### Проблема: 502 Bad Gateway
- Проверь логи в Railway
- Убедись что SECRET_KEY установлен
- Проверь что DEBUG = False

### Проблема: Статические файлы не загружаются
- Выполни `python manage.py collectstatic` локально
- WhiteNoise должен обработать это автоматически

### Проблема: Ошибка при миграциях
- Проверь что все модели правильно определены
- Убедись что все приложения в INSTALLED_APPS

## Локальное тестирование перед деплоем
```bash
# Установи зависимости
pip install -r requirements.txt

# Собери статические файлы
python manage.py collectstatic --noinput

# Запусти как в продакшене (локально)
gunicorn config.wsgi
```

## Полезные команды Railway CLI
```bash
# Установка
npm i -g @railway/cli

# Логин
railway login

# Привязка к проекту
railway link

# Просмотр логов
railway logs

# Просмотр переменных
railway variables

# Деплой
git push  # Автоматический деплой из GitHub
```
