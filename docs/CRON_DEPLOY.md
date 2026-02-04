# Установка cron-задач и автоматизация при деплое

В проекте используются периодические задачи (формирование сеток, просроченные матчи, авто-подтверждение заявок). Ниже — как их запускать в зависимости от типа сервера.

---

## Вариант A: Свой сервер (VPS, выделенный сервер)

На сервере с SSH и постоянной файловой системой используется **django-crontab**: команда `crontab add` прописывает задачи в системный crontab пользователя.

### Установка вручную (один раз)

```bash
cd /path/to/Tennison
source venv/bin/activate   # или: . venv/bin/activate
python manage.py crontab add
```

Проверить, что задачи добавлены:

```bash
python manage.py crontab show
```

### Автоматически при деплое

Добавьте вызов `crontab add` в скрипт деплоя **после** `migrate` и `collectstatic`, чтобы при каждом обновлении сайта список cron-задач обновлялся (дубликаты не создаются).

**Пример `deploy.sh`** (запускать на сервере при деплое):

```bash
#!/bin/bash
set -e
cd /path/to/Tennison
git pull
source venv/bin/activate
pip install -r requirements.txt --quiet
python manage.py migrate --noinput
python manage.py collectstatic --noinput
python manage.py crontab add   # обновить cron-задачи при каждом деплое
sudo systemctl restart gunicorn   # или: supervisorctl restart gunicorn
```

Либо, если используете **Procfile** только для запуска (не Railway/Heroku), в блоке `release` можно добавить crontab (только если release выполняется на той же машине, где потом работает приложение и есть доступ к crontab):

```bash
# В Procfile (для своих серверов, где release и web на одной машине):
release: python manage.py migrate && python manage.py collectstatic --noinput && python manage.py crontab add
web: gunicorn config.wsgi --log-file -
```

---

## Вариант B: Railway / Heroku (PaaS)

На PaaS нет постоянного crontab: каждый деплой поднимает новые контейнеры, поэтому `python manage.py crontab add` не даёт эффекта после перезапуска.

Используйте **встроенный планировщик** платформы и добавьте задачи вручную в интерфейсе.

### Railway

1. В проекте Railway откройте сервис с приложением (или создайте отдельный сервис для cron).
2. В разделе **Settings** найдите **Cron** / **Cron Jobs** (или добавьте новый сервис типа **Cron**).
3. Добавьте задачи по расписанию. Пример (команды запускать в корне проекта с активированным окружением):

| Расписание   | Команда |
|-------------|---------|
| `*/10 * * * *`  | `python manage.py generate_brackets_past_deadlines` |
| `*/15 * * * *`  | `python manage.py auto_accept_stale_proposals`      |
| `0 */6 * * *`   | `python manage.py fan_process_overdue_matches`      |
| `0 */6 * * *`   | `python manage.py olympic_process_overdue_matches`  |

В Railway Cron обычно указывают расписание (cron expression) и одну команду на задачу, т.е. 4 отдельные cron-задачи.

### Heroku

Используйте **Heroku Scheduler** (add-on):

1. Heroku Dashboard → приложение → **Resources** → добавить **Heroku Scheduler**.
2. Открыть Scheduler → **Create job**.
3. Для каждой задачи задать интервал и команду, например:
   - Every 10 minutes: `python manage.py generate_brackets_past_deadlines`
   - Every 15 minutes: `python manage.py auto_accept_stale_proposals`
   - Every 6 hours: `python manage.py fan_process_overdue_matches` и `python manage.py olympic_process_overdue_matches`

(Точный набор интервалов в Scheduler может отличаться — выберите ближайшие к указанным выше.)

---

## Список команд и расписание (справочно)

| Команда | Рекомендуемое расписание | Назначение |
|---------|---------------------------|------------|
| `generate_brackets_past_deadlines` | каждые 10 мин | Формирование сеток по дедлайну регистрации |
| `auto_accept_stale_proposals`      | каждые 15 мин | Авто-подтверждение заявок на результат (6 ч без ответа) |
| `fan_process_overdue_matches`      | каждые 6 ч   | Тех. победа по рейтингу в FAN при просрочке матча |
| `olympic_process_overdue_matches`  | каждые 6 ч   | То же для олимпийской системы |

Все команды можно запускать вручную для проверки:

```bash
python manage.py generate_brackets_past_deadlines
python manage.py auto_accept_stale_proposals
python manage.py fan_process_overdue_matches
python manage.py olympic_process_overdue_matches
```
