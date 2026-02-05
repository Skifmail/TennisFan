# Telegram-бот для пользователей

Бот для уведомлений о турнирах, матчах и подписке. Привязка хранится в общей таблице `UserTelegramLink` (core).

## Переменные окружения

- `TELEGRAM_USER_BOT_TOKEN` — токен бота (обязательно).
- `TELEGRAM_USER_BOT_USERNAME` — @username бота (опционально, иначе запрашивается через getMe).
- `TELEGRAM_USER_BOT_WEBHOOK_SECRET` — секрет для заголовка `X-Telegram-Bot-Api-Secret-Token` (опционально).
- `TELEGRAM_BOT_SITE_BASE_URL` — базовый URL сайта для ссылок в боте, например `https://tennisfan.ru` (без слэжа в конце). Если не задан, в разработке используется `http://localhost:8000`.

## Webhook

URL: `POST /telegram/user-bot-webhook/`

Настройка (замените `YOUR_TOKEN` и `https://your-domain.com`):

```bash
curl -X POST "https://api.telegram.org/botYOUR_TOKEN/setWebhook?url=https://your-domain.com/telegram/user-bot-webhook/"
```

Если задан `TELEGRAM_USER_BOT_WEBHOOK_SECRET`, при настройке webhook можно передать секрет (см. документацию Telegram Bot API).

## Подключение бота пользователем

1. Пользователь заходит в «Редактирование профиля» и нажимает «Подключить Telegram-бот».
2. Происходит редирект на `t.me/BotUsername?start=TOKEN`.
3. Пользователь нажимает «Start» в боте; webhook получает `/start TOKEN` и привязывает `chat_id` к пользователю в `UserTelegramLink`.
4. В профиле кнопка становится неактивной, отображается «Бот уже подключён».

## Поведение бота (п. 1–3)

- `/start` с токеном — привязка, приветствие и меню (Мои матчи, Мой профиль, Моя подписка).
- `/start` без токена — если уже привязан: меню; иначе предложение подключить бота с сайта.
- Кнопки меню ведут на соответствующие страницы сайта (URL из `TELEGRAM_BOT_SITE_BASE_URL`).
