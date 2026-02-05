# Обратная связь и Telegram

## Отдельный бот для поддержки

Для обратной связи используется **отдельный бот** (не основной бот уведомлений).

**Обязательно в .env:**
- `TELEGRAM_SUPPORT_BOT_TOKEN` — токен бота поддержки ([@BotFather](https://t.me/BotFather))
- `TELEGRAM_ADMIN_CHAT_ID` — ID чата, куда приходят сообщения (ваш диалог с ботом или группа)

**Можно не задавать:**
- `TELEGRAM_SUPPORT_BOT_USERNAME` — если не задан, ссылка привязки (t.me/Бот?start=...) строится сама: бот запрашивает свой @username у Telegram. Задавать вручную нужно только если по какой-то причине запрос к API не работает.
- `TELEGRAM_SUPPORT_WEBHOOK_SECRET` — если не задан, webhook принимает все запросы (проверка по секрету отключена). Задавать имеет смысл, если хотите дополнительно убедиться, что на ваш URL шлёт запросы именно Telegram (при setWebhook укажите тот же `secret_token`).

Сообщения с сайта уходят админу в Telegram. Админ отвечает reply — ответ уходит пользователю в Telegram. Подробная архитектура: [SUPPORT_TELEGRAM_ARCHITECTURE.md](SUPPORT_TELEGRAM_ARCHITECTURE.md).

## Webhook бота поддержки

1. URL должен быть доступен по HTTPS:  
   `https://ваш-домен/telegram/support-webhook/`

2. Установите webhook для **бота поддержки** (токен `TELEGRAM_SUPPORT_BOT_TOKEN`):
   ```bash
   curl "https://api.telegram.org/bot<TELEGRAM_SUPPORT_BOT_TOKEN>/setWebhook?url=https://ваш-домен/telegram/support-webhook/"
   ```
   С секретом (если задан `TELEGRAM_SUPPORT_WEBHOOK_SECRET`):
   ```bash
   curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://ваш-домен/telegram/support-webhook/&secret_token=<TELEGRAM_SUPPORT_WEBHOOK_SECRET>"
   ```

3. Проверить:  
   `https://api.telegram.org/bot<TELEGRAM_SUPPORT_BOT_TOKEN>/getWebhookInfo`

4. Удалить:  
   `https://api.telegram.org/bot<TELEGRAM_SUPPORT_BOT_TOKEN>/deleteWebhook`

## Очистка привязок (смена бота или пробные привязки)

Чтобы удалить все привязки Telegram (например после смены бота или неудачной привязки через старый бот):

```bash
python manage.py clear_telegram_support_bindings
```

С `--dry-run` — только показать количество записей без удаления.
