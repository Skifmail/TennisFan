# STATE: Клубный раздел TennisFan

Живая память проекта: текущая позиция, решения, отложенные вопросы.

---

## Current Position

- **Phase:** 8 (Клубные тарифы для игроков, опционально) — **IN PROGRESS**
- **Plan:** `.planning/phases/08-club-plans/08-01-PLAN.md` (выполняется)
- **Last updated:** 2026-03-16

---

## Decisions

- **Структура приложения:** клубный функционал выделять в отдельное приложение `apps.clubs` (модели, views, urls); при необходимости подмодули или отдельные приложения для админки платформы.
- **Расширение турниров:** поля `club_id` и `is_open_interclub` добавляются в существующую модель `tournaments.Tournament` миграцией; `club_id` nullable — турниры без клуба остаются «общими» платформы.
- **Порядок работ:** сначала бэкенд (модели, API, логика), затем UI; не тратить время на фронт до рабочего API (согласно PROJECT.md).
- **Шифрование Secret Key клуба:** реквизиты ЮKassa клуба хранятся зашифрованно через Fernet (cryptography), ключ шифрования берётся из `CLUB_PAYMENT_ENCRYPTION_KEY` или генерируется детерминированно из `SECRET_KEY`.
- **Уведомления (Phase 6):** сервис уведомлений `apps/clubs/notifications.py` использует Django email + Telegram-бота платформы (`apps.telegram_bot.notifications.send_to_user_by_user`). Настройки на двух уровнях: `ClubNotificationConfig` (глобальные по клубу) и `ClubNotificationSettings` (индивидуальные по участнику). Telegram-интеграция расширяемая — при отсутствии привязки бота сообщения молча пропускаются.
- **Межклубные заявки (Phase 7):** базовый флоу — чекбокс `is_open_interclub` в форме турнира (только Про), подача заявки от клуба, одобрение/отклонение организатором. Реализовано через `ClubTournamentApplication` (Phase 1) + views/шаблоны (Phase 7).
- **Аудит-лог (Phase 7):** `PlatformAuditLog` записывает действия platform_admin (блокировка, разблокировка, смена тарифа, reset trial, автосуспенд, автоудаление). Утилита `log_platform_action()` в services.py.
- **Глобальные настройки (Phase 7):** `PlatformSettings` — singleton-модель (pk=1) с параметрами trial_days, suspended_data_retention_days, auto_delete_suspended, registration_open. Используется в `create_club_with_trial()` и CRON-командах.
- **Тарифы платформы:** лимиты и цены тарифов (Старт/Базовый/Про) хранятся в константах (`PLAN_PRICES`, `CLUB_PLAN_TOURNAMENTS_PER_MONTH`). Перенос в БД — при необходимости в будущем.

---

## Pending Todos

(Задачи добавляются через `/gsd/add-todo` или вручную.)

- [x] Phase 1: спланировать детальные шаги (run `/gsd/plan-phase 1`)
- [x] Phase 1: выполнить план 01-01 (База данных) — `/gsd/execute-plan .planning/phases/01-database/01-01-PLAN.md`
- [x] Phase 2: выполнить план 02-01 — `/gsd/execute-plan .planning/phases/02-registration-crud/02-01-PLAN.md`
- [x] Phase 3: выполнить план 03-01 — `/gsd/execute-plan .planning/phases/03-club-lk/03-01-PLAN.md`
- [x] Phase 4: выполнить план 04-01 — `/gsd/execute-plan .planning/phases/04-club-dashboard/04-01-PLAN.md`
- [x] Phase 5: выполнить план 05-01 — `/gsd/execute-plan .planning/phases/05-finances/05-01-PLAN.md`
- [x] Phase 6: выполнить план 06-01 — `/gsd/execute-plan .planning/phases/06-notifications/06-01-PLAN.md`
- [x] Phase 7: выполнить план 07-01 — `/gsd/execute-plan .planning/phases/07-interclub-admin/07-01-PLAN.md`
- [ ] Phase 8: выполнить план 08-01 — `/gsd/execute-plan .planning/phases/08-club-plans/08-01-PLAN.md`
- [x] Phase 9: выполнить план 09-01 (анализ CSS) — `/gsd/execute-plan .planning/phases/09-css-refactor/09-01-PLAN.md`
- [x] Phase 9: выполнить план 09-02 (вынесение base-блоков CSS) — `/gsd/execute-plan .planning/phases/09-css-refactor/09-02-PLAN.md`
- [x] Phase 9: выполнить план 09-03 (вынесение навигации в components/nav.css) — `/gsd/execute-plan .planning/phases/09-css-refactor/09-03-PLAN.md`
- [x] Phase 9: выполнить план 09-04 (вынесение кнопок в components/buttons.css) — `/gsd/execute-plan .planning/phases/09-css-refactor/09-04-PLAN.md`
- [x] Phase 9: выполнить план 09-05 (вынесение форм в components/forms.css) — `/gsd/execute-plan .planning/phases/09-css-refactor/09-05-PLAN.md`
- [x] Phase 9: выполнить план 09-06 (вынесение карточек в components/cards.css) — `/gsd/execute-plan .planning/phases/09-css-refactor/09-06-PLAN.md`
- [x] Phase 9: выполнить план 09-07 (вынесение page-specific секций в pages/*.css) — `/gsd/execute-plan .planning/phases/09-css-refactor/09-07-PLAN.md`
- [x] Phase 9: выполнить план 09-08 (вынесение auth/tournament секций в pages/*.css) — `/gsd/execute-plan .planning/phases/09-css-refactor/09-08-PLAN.md`

---

## Deferred Issues

(Проблемы и идеи, отложенные на потом; источник: UAT или обсуждения.)

- Stripe-провайдер в Phase 5 оставлен как заглушка («в разработке»); реализация при необходимости.
- Детальная Telegram-интеграция (авторизация пользователей через клубного бота, хранение chat_id отдельно для клубов) может быть вынесена в отдельную фазу; в Phase 6 используется существующий бот платформы.
- Управление тарифами платформы (Старт/Базовый/Про) через Django Admin (отдельная модель `PlatformPlan` с лимитами, ценами, видимостью) — пока в константах; перенос в БД при необходимости.
- Полноценный межклубный флоу (учёт участников от разных клубов, сборные) — за рамками Phase 7, реализован базовый флоу заявок.

---

## Session Continuity

(Краткий контекст для возобновления работы: что делали в последней сессии, что планировали дальше.)

- Выполнен план 07-01 (Phase 7 — Межклубные заявки и админ платформы):
  - Task 1: Добавлен чекбокс `is_open_interclub` в `ClubTournamentCreateForm` (только тариф Про). Шаблон `tournament_create.html` обновлён.
  - Task 2: View `club_tournament_apply` для подачи заявки клубом на межклубный турнир. Контекст `_get_interclub_context()` в `tournament_detail` (tournaments/views.py). Блок заявок в `detail.html`.
  - Task 3: Views `interclub_applications` и `interclub_application_respond`. Шаблон `interclub_applications.html`. Ссылка в навигации дашборда.
  - Task 4: Модели `PlatformAuditLog` (с PlatformAuditAction) и `PlatformSettings` (singleton pk=1). Миграция `0004_platform_audit_and_settings`. Утилита `log_platform_action()` и `get_platform_settings()` в services.py.
  - Task 5: `ClubAdmin` расширен: инлайны (подписки, участники, взносы, уведомления), actions (block/unblock/reset_trial), фильтр `CurrentPlanFilter`, аннотации (members_count, tournaments_count). `PlatformAuditLogAdmin` (readonly). `PlatformSettingsAdmin` (singleton).
  - Task 6: Custom admin view `financial_summary_view` с MRR, выручкой, статистикой тарифов. Шаблон `admin/clubs/financial_summary.html`. Ссылка через `club_changelist.html`.
  - Task 7: Management-команда `cleanup_suspended_clubs` — удаление suspended клубов по retention. Зарегистрирована в CRONJOBS (03:00).
  - Task 8: Management-команда `suspend_expired_clubs` — автоприостановка клубов с истёкшей подпиской/trial. Зарегистрирована в CRONJOBS (02:00).
  - `create_club_with_trial()` использует `PlatformSettings.trial_days`.
- Подготовлен план 08-01 для Phase 8 (Клубные тарифы для игроков, опционально).
- Запущено выполнение плана 08-01: добавлены модели тарифов игроков, сервис лимитов/слотов, базовый UI управления тарифами и интеграция проверок тарифа в регистрацию на турниры.
- Добавлен расширенный UI правил доступа тарифов к турнирам (`tournament_plan_access`) и переходы из списка турниров клуба.
- Выполнен план 09-01 (Phase 9 — CSS рефакторинг, анализ): подготовлена карта `.planning/phases/09-css-refactor/CSS-MAP.md` с оценкой базовых стилей, компонентных блоков, page-specific секций и зон дублирования; `static/css/style.css` не изменялся.
- Выполнен план 09-02 (Phase 9 — CSS рефакторинг, вынос base): блоки `typography`, `variables`, `reset` вынесены в `static/css/base/*.css`; в `static/css/style.css` подключены через `@import`; селекторы не изменялись.
- Выполнен план 09-03 (Phase 9 — CSS рефакторинг, вынос navigation): блоки `header/nav desktop/mobile` и `dropdown-навигация` вынесены в `static/css/components/nav.css`; в `static/css/style.css` добавлен `@import`; селекторы не изменялись.
- Выполнен план 09-04 (Phase 9 — CSS рефакторинг, вынос buttons): button-блоки вынесены в `static/css/components/buttons.css`; в `style.css` добавлен `@import`; дублирующийся селектор `.sparring-response-row__actions .btn` консолидирован без изменения свойств.
- Выполнен план 09-05 (Phase 9 — CSS рефакторинг, вынос forms): auth/login/register, filter/input/score и consent-блоки вынесены в `static/css/components/forms.css`; в `style.css` добавлен `@import`; дубли `\.auth-form` консолидированы в общем mobile-scope.
- Выполнен план 09-06 (Phase 9 — CSS рефакторинг, вынос cards): блоки карточек тренировок/игроков и турнирных/ценовых карточек вынесены в `static/css/components/cards.css`; в `style.css` добавлен `@import`; дубли `\.player-card` в card-секции консолидированы по media-scope без изменения значений свойств.
- Выполнен план 09-07 (Phase 9 — CSS рефакторинг, вынос page-specific): профайл/матчи/контакты вынесены в `static/css/pages/profile.css`, `static/css/pages/matches.css`, `static/css/pages/contacts.css`; в `style.css` добавлены `@import`; селекторы и свойства не изменялись.
- Выполнен план 09-08 (Phase 9 — CSS рефакторинг, вынос auth/tournament page-specific): блоки auth (password-toggle, rotating coin logo) и tournament (FAN bracket, manage-page blocks, intermediate table) вынесены в `static/css/pages/auth.css` и `static/css/pages/tournament.css`; в `style.css` добавлены `@import`; селекторы и свойства не изменялись.
- Следующий шаг: ревизия оставшихся универсальных секций (`utilities/helpers`) и финальная чистка `style.css`.
