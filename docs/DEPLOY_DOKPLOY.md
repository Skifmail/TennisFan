# Деплой TennisFan (Tennison) на сервер с Dokploy

Пошаговая инструкция: от настройки сервера до автоматического обновления по пушу в репозиторий. Настройки и привязки Telegram-ботов хранятся в Dokploy и не сбрасываются при деплое.

---

## 1. Подготовка сервера

### 1.1 Требования

- **ОС:** Ubuntu 22.04 LTS (рекомендуется) или другой дистрибутив с поддержкой Docker.
- **Ресурсы:** минимум 2 GB RAM, 2 vCPU, 20 GB SSD.
- **Доступ:** root или пользователь с sudo.

### 1.2 Обновление и базовые пакеты

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y curl git
```

### 1.3 Установка Docker

Официальный скрипт (Docker Engine + Docker Compose plugin):

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
```

Выйдите из SSH и зайдите снова, чтобы применилась группа `docker`. Проверка:

```bash
docker --version
docker compose version
```

### 1.4 Файрвол (опционально, но рекомендуется)

```bash
sudo ufw allow 22/tcp    # SSH
sudo ufw allow 80/tcp    # HTTP (Traefik)
sudo ufw allow 443/tcp   # HTTPS
sudo ufw enable
```

---

## 2. Установка Dokploy

### 2.1 Клонирование и запуск

```bash
git clone https://github.com/dokploy/dokploy.git
cd dokploy
```

Создайте `.env` в каталоге `dokploy` (или отредактируйте существующий):

```bash
nano .env
```

Минимально задайте:

- `DOKPLOY_SUPER_ADMIN_USER` — логин админ-панели Dokploy.
- `DOKPLOY_SUPER_ADMIN_PASSWORD` — пароль.
- При необходимости укажите домен и настройки для HTTPS (см. документацию Dokploy).

Запуск:

```bash
docker compose up -d
```

Дождитесь запуска (обычно 1–2 минуты). Откройте в браузере: `http://IP_СЕРВЕРА:3000` (или ваш домен Dokploy).

### 2.2 Первый вход

- Войдите под учётной записью супер-админа.
- При первом запуске может потребоваться смена пароля или подтверждение настроек — следуйте подсказкам в UI.

---

## 3. Создание проекта и приложения в Dokploy

### 3.1 Проект

1. В боковом меню: **Project** → **Create Project**.
2. Укажите имя, например: `tennisfan`.
3. Сохраните.

### 3.2 Docker Compose приложение

1. В проекте нажмите **Add Service** → **Docker Compose**.
2. Заполните:
   - **Name:** например `tennisfan-app`.
   - **Source:** **Git**.
   - **Repository URL:** URL вашего репозитория (HTTPS или SSH), например  
     `https://github.com/your-username/Tennison.git`.
   - **Branch:** ветка для деплоя (обычно `main` или `master`).
   - **Compose file path:** `docker-compose.dokploy.yml`  
     (в репозитории должен быть файл `docker-compose.dokploy.yml` в корне).
3. Сохраните (кнопка **Save** или **Create**).

---

## 4. Переменные окружения (критично для ботов и настроек)

Переменные задаются в Dokploy и **не хранятся в репозитории**. Они подставляются в `docker-compose.dokploy.yml` при деплое и **сохраняются между деплоями**.

1. Откройте созданное приложение Docker Compose.
2. Вкладка **Environment** (или **Env**).
3. Добавьте переменные по одной (ключ и значение). Используйте список ниже.

### 4.1 Обязательные

| Переменная | Пример / описание |
|------------|-------------------|
| `SECRET_KEY` | Длинная случайная строка (Django) |
| `ALLOWED_HOSTS` | `tennisfan.ru,www.tennisfan.ru` |
| `CSRF_TRUSTED_ORIGINS` | `https://tennisfan.ru,https://www.tennisfan.ru` |
| `POSTGRES_DB` | `tennisfan` |
| `POSTGRES_USER` | `tennisfan` |
| `POSTGRES_PASSWORD` | Надёжный пароль БД |
| `TELEGRAM_BOT_TOKEN` | Токен бота уведомлений админа |
| `TELEGRAM_SUPPORT_BOT_TOKEN` | Токен бота поддержки |
| `TELEGRAM_USER_BOT_TOKEN` | Токен бота ЛК |
| `TELEGRAM_ADMIN_CHAT_ID` | ID чата для уведомлений админа |
| `TELEGRAM_BOT_SITE_BASE_URL` | `https://tennisfan.ru` (без слэша в конце) |
| `TELEGRAM_PRIVATE_COMMUNITY_CHAT_ID` | ID приватного чата (если есть) |
| `TELEGRAM_PUBLIC_COMMUNITY_URL` | Например `https://t.me/TennisFanu` |

В `docker-compose.dokploy.yml` для сервисов приложения используется **`POSTGRES_HOST=db`** (внутреннее имя сервиса PostgreSQL). Отдельно задавать `POSTGRES_HOST` в Environment не нужно, если используете этот compose.

### 4.2 Остальные (по необходимости)

- **Яндекс:** `YANDEX_MAPS_API_KEY`, `YANDEX_GEOCODER_API_KEY`, `YANDEX_GEOCODER_REFERER`
- **Почта:** `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_USE_SSL`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL`
- **S3:** `USE_S3`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET_NAME`, `S3_ENDPOINT_URL`, `S3_REGION`
- **Опционально:** `TELEGRAM_USER_BOT_WEBHOOK_SECRET`, `TELEGRAM_SUPPORT_WEBHOOK_SECRET`, `ADMIN_URL`

Можно взять полный список из `.env.example` в репозитории и перенести ключи и значения в Environment в Dokploy (значения — свои, продакшен).

После добавления переменных снова нажмите **Save**.

---

## 5. Домен и HTTPS

1. В приложении откройте вкладку **Domains**.
2. **Add Domain**:
   - Укажите домен, например `tennisfan.ru`.
   - При необходимости добавьте `www.tennisfan.ru` отдельно или настройте редирект в Traefik/Dokploy.
3. Выберите сервис, к которому привязать домен: **web** (порт 8000).
4. Включите **HTTPS** (Let's Encrypt), если Dokploy это поддерживает в вашей конфигурации.

В DNS у регистратора создайте A-записи:

- `tennisfan.ru` → IP вашего сервера  
- `www.tennisfan.ru` → IP вашего сервера  

(или CNAME на домен Dokploy/Traefik, если так задумано.)

---

## 6. Первый деплой

1. Убедитесь, что в **Environment** сохранены все нужные переменные.
2. Вкладка **Deployments** (или **General** → кнопка деплоя).
3. Нажмите **Deploy** (или **Redeploy**).
4. Дождитесь окончания сборки и запуска контейнеров (логи смотрите в **Logs**).

Порядок запуска в compose:

- Поднимается **db** (PostgreSQL), затем **web** и **cron**.
- **web** при старте: ожидание БД → `migrate` → `collectstatic` → **`set_telegram_webhooks`** → gunicorn.
- **cron** при старте: ожидание БД → `crontab add` → демон cron.

Таким образом, при каждом деплое webhook’и Telegram переустанавливаются автоматически и не «слетают».

---

## 7. Авто-деплой по пушу в репозиторий

Чтобы при пуше в Git приложение обновлялось без ручного нажатия Deploy:

### 7.1 Включение Auto Deploy в Dokploy

1. Откройте приложение Docker Compose.
2. Вкладка **General**.
3. Включите переключатель **Auto Deploy** (или аналогичный).
4. Сохраните.

### 7.2 Webhook в репозитории

1. В Dokploy откройте вкладку **Deployments** (или **Logs** при деплое).
2. Скопируйте **Webhook URL** (выдаётся Dokploy для этого приложения).
3. В GitHub/GitLab/Bitbucket:
   - Репозиторий → **Settings** → **Webhooks** (или **Integrations**).
   - **Add webhook**:
     - **Payload URL:** вставьте Webhook URL из Dokploy.
     - **Content type:** `application/json` (если есть выбор).
     - **Trigger:** push events (или только ветка `main`/`master`).
   - Сохраните webhook.

Убедитесь, что в Dokploy в настройках приложения указана **та же ветка**, в которую вы пушите (иначе будет «Branch Not Match»).

**Приватный репозиторий:** в настройках приложения в Dokploy укажите SSH URL репозитория и добавьте **SSH Deploy Key** (или учётные данные), чтобы Dokploy мог делать `git clone` при деплое.

После этого каждый push в выбранную ветку будет запускать деплой. Переменные окружения и домены в Dokploy при этом не меняются — обновляется только код из Git.

---

## 8. Что не слетает при обновлении

- **Переменные окружения** — хранятся в Dokploy (вкладка Environment), не в репозитории.
- **Токены и привязки ботов** — заданы в переменных; при каждом старте контейнера **web** вызывается `set_telegram_webhooks`, поэтому webhook’и снова привязываются к вашему домену.
- **База данных** — хранится в volume `postgres_data`; при пересборке образов и перезапуске контейнеров данные не удаляются.
- **Статика** — в volume `static_data`; при деплое заново выполняется `collectstatic`.
- **Cron-задачи** — контейнер **cron** при старте снова выполняет `crontab add` по настройкам из кода.

---

## 9. Проверка после деплоя

1. Откройте сайт: `https://tennisfan.ru` (или ваш домен).
2. Проверьте админку: `https://tennisfan.ru/admin/` (или ваш `ADMIN_URL`).
3. Telegram:
   - Бот ЛК: команда /start или кнопка «Открыть ЛК» — должна открываться ссылка на сайт.
   - Бот поддержки: отправка сообщения — должна дойти до админов.
4. В Dokploy откройте **Logs** сервиса **web** и убедитесь, что нет ошибок при `set_telegram_webhooks` и при обработке запросов.

При необходимости выполните установку webhook’ов вручную внутри контейнера:

```bash
# В Dokploy: выберите сервис web → Advanced → Execute Command (или аналог)
python manage.py set_telegram_webhooks
```

(Команда уже вызывается в entrypoint при каждом старте.)

---

## 10. Полезные замечания

- **Первый деплой:** убедитесь, что в репозитории в корне есть файл **`docker-compose.dokploy.yml`** и в настройках приложения в Dokploy указан путь к нему.
- **Миграции** выполняются при каждом старте контейнера **web** (в `entrypoint.sh`).
- **Cron** описан в `docs/CRON_DEPLOY.md` и в `crontab.example`; в Docker-деплое используется контейнер **cron** с `cron-entrypoint.sh`.
- Резервное копирование: настройте бэкапы volume **postgres_data** (через Dokploy Volume Backups или свой скрипт).
- Логи и мониторинг смотрите во вкладках **Logs** и **Monitoring** по каждому сервису в Dokploy.

Если что-то пойдёт не так, в первую очередь проверьте логи **web** и **db** и список переменных окружения (нет ли опечаток и пустых значений для обязательных ключей).
