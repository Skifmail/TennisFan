# Структура репозитория TennisFan (Tennison)

## Корень проекта

```
Tennison/
├── config/                 # Настройки Django-проекта
│   ├── settings.py         # Основной конфиг (env, apps, DB, media, cron, logging)
│   ├── urls.py             # Корневая маршрутизация
│   ├── wsgi.py
│   ├── sitemaps.py         # Sitemap для статических страниц
│   └── validators.py       # Валидаторы (изображения, миксины)
├── apps/                   # Приложения
├── templates/              # Глобальные шаблоны
├── static/                 # Исходная статика (до collectstatic)
├── staticfiles/            # Собранная статика (WhiteNoise)
├── media/                   # Медиа (если не S3)
├── logs/                    # Логи приложения
├── requirements.txt
├── pyproject.toml          # black, ruff, mypy
├── manage.py
└── .env / .env.local       # Переменные окружения
```

## Приложения (apps/)

| Приложение | Основные модели / сущности | URL-префикс |
|------------|----------------------------|-------------|
| **core** | UserTelegramLink, SupportMessage, Feedback, City, FooterSocialLink | `/` (главная, рейтинг, правила, обратная связь, поддержка, API городов/матчей) |
| **users** | User, NtrpTestResult, Notification | `/users/` |
| **tournaments** | Tournament, Match, TVDGroup, TournamentTeam, SeasonRating, TournamentEntryPayment | `/tournaments/` |
| **courts** | Court, CourtPhoto, CourtRating | `/courts/` |
| **sparring** | SparringRequest, SparringResponse, DoublesMatchRequest, DoublesTeam | `/sparring/` |
| **training** | Coach, TrainingEnrollment | `/training/` |
| **content** | Page, Video, VideoPage, StringerPage, ContactPage, RulesSection, LiveStream | `/news/`, `/pages/`, `/videos/`, `/stringers/`, `/gallery/`, `/about/`, `/contacts/` |
| **comments** | Comment | (подключается в других приложениях) |
| **subscriptions** | SubscriptionTier, UserSubscription, RegionalTierPrice | `/subscriptions/` |
| **payments** | SavedPaymentMethod, PaymentRecord | `/payments/` |
| **legal** | — (views + шаблоны) | `/legal/` |
| **navigation** | MenuItem | (context_processor для меню) |
| **shop** | ShopPage, Product, PurchaseRequest | `/shop/` |
| **telegram_bot** | — (views, webhooks) | `/telegram/` |
| **player_ratings** | PlayerSkillRating, SkillMetricConfig, PlayerSkillAggregate | `/ratings/` |

## Важные файлы по доменам

- **Турниры**: `apps/tournaments/models.py`, `apps/tournaments/tvd.py` (сетки, матчи, форматы), `apps/tournaments/urls.py`, management commands в `apps/tournaments/management/commands/`.
- **Пользователи и авторизация**: `apps/users/models.py` (User, Player), `apps/users/views.py`, `config/settings.py` (AUTH_USER_MODEL, LOGIN_URL).
- **Оплаты и подписки**: `apps/payments/`, `apps/subscriptions/`, настройки YOOKASSA_*, cron для рекуррентных платежей.
- **Контент**: `apps/content/models.py`, множество `apps/content/urls_*.py` (news, pages, videos, stringers, gallery, about, contacts).
- **Интеграции**: Telegram — `apps/core` (support webhook, ссылки), `apps/telegram_bot`; геокодер — `apps/courts`.

## Шаблоны

- Общая база и блоки в `templates/` (в т.ч. `base.html`, переиспользуемые компоненты).
- Подкаталоги по приложениям/разделам: `users/`, `tournaments/`, `courts/`, `content/` и т.д.
- Статика: `static/` (исходники), после `collectstatic` — `staticfiles/`.

## Миграции

- В каждом приложении: `apps/<app>/migrations/`.
- Запуск: `python manage.py migrate`.

## Планирование (после map-codebase)

- Карта кодовой базы: `.planning/codebase/` (stack.md, architecture.md, structure.md).
- Дальнейшие артефакты GSD (roadmap, фазы, планы) — в `.planning/` по необходимости.
