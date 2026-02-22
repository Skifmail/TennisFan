# Структура базы данных TennisFan

Документ описывает все таблицы и связи. Имена таблиц соответствуют Django (приложение_модель в нижнем регистре).

---

## Системные таблицы Django

| Таблица | Назначение |
|---------|------------|
| `django_migrations` | Применённые миграции |
| `django_content_type` | Типы контента (для GenericForeignKey, прав) |
| `django_session` | Сессии пользователей |
| `django_admin_log` | Лог действий в админке |

---

## Аутентификация и пользователи

### auth_permission
Права доступа (связь с content_type).

| Поле | Тип | Описание |
|------|-----|----------|
| id | PK | |
| content_type_id | FK → django_content_type | |
| codename | varchar(100) | |
| name | varchar(255) | |

### auth_group
Группы пользователей.

| Поле | Тип |
|------|-----|
| id | PK |
| name | varchar(150) UNIQUE |

### auth_group_permissions
M2M: группы ↔ права.

---

### users_user
Пользователь (логин, email, права).

| Поле | Тип | Описание |
|------|-----|----------|
| id | PK | |
| password | varchar(128) | Хэш пароля |
| last_login | datetime | |
| is_superuser | bool | |
| first_name, last_name | varchar(150) | |
| is_staff | bool | Доступ в админку |
| is_active | bool | |
| date_joined | datetime | |
| email | varchar(254) UNIQUE | Логин |
| phone | varchar(20) | |

### users_user_groups, users_user_user_permissions
M2M: пользователь ↔ группы, пользователь ↔ права.

### users_player
Профиль игрока (1:1 с User). Рейтинг, NTRP, контакты, аватар.

| Поле | Тип | Описание |
|------|-----|----------|
| id | PK | |
| user_id | FK → users_user UNIQUE | |
| avatar | varchar(100) | Файл |
| city | varchar(100) | |
| ntrp_level | decimal(3,1) | Уровень силы 1.5–7.0 |
| skill_level | varchar(20) | novice/amateur/experienced/advanced/professional |
| birth_date | date | |
| gender | varchar(10) | |
| forehand | varchar(10) | Правая/левая рука |
| age | int | Вычисляется или задаётся |
| bio | text | |
| telegram, whatsapp | varchar | |
| max_contact | varchar(500) | Как предпочитают связываться |
| total_points | real | FAN-рейтинг (отображаемый) |
| hidden_rating | real | Для расчётов |
| matches_played | int | |
| matches_won | int | |
| is_verified | bool | |
| is_legend | bool | |
| is_bye | bool | Служебный «игрок» для сетки |
| created_at, updated_at | datetime | |

### users_notification
Уведомления пользователю.

| Поле | Тип |
|------|-----|
| id | PK |
| user_id | FK → users_user |
| message | varchar(255) |
| url | varchar(255) |
| is_read | bool |
| created_at | datetime |

---

## Турниры (tournaments)

### tournaments_tournament
Турнир: название, город, даты, формат, статус, очки, участники (M2M).

| Поле | Тип | Описание |
|------|-----|----------|
| id | PK | |
| name | varchar(200) | |
| slug | varchar(50) UNIQUE | |
| description | text | |
| city | varchar(100) | |
| tournament_type | varchar(20) | |
| format | varchar(20) | single_elimination, round_robin, olympic_consolation |
| variant | varchar(20) | |
| status | varchar(20) | draft, published, registration, in_progress, finished |
| start_date, end_date | date | |
| registration_deadline | datetime | |
| duration | varchar(10) | |
| gender | varchar(10) | |
| min_participants, max_participants | int | |
| min_teams, max_teams | int | Для парных |
| entry_fee | decimal | |
| is_one_day | bool | |
| bracket_generated | bool | |
| points_winner, points_loser | int | |
| fan_points_r1, fan_points_r2, fan_points_sf, fan_points_final, fan_points_winner | int | Очки за раунды |
| match_format | varchar(20) | |
| match_days_per_round | int | |
| image | varchar(100) | |
| insufficient_participants_notified_at | datetime | |
| created_at, updated_at | datetime | |

### tournaments_tournament_participants
M2M: турнир ↔ игроки (участники).

### tournaments_tournamentallowedcategory
Разрешённые категории для турнира (novice, amateur, …).

| Поле | Тип |
|------|-----|
| id | PK |
| tournament_id | FK → tournaments_tournament |
| category | varchar(20) |

### tournaments_tournamentteam
Парная команда в турнире.

| Поле | Тип |
|------|-----|
| id | PK |
| tournament_id | FK → tournaments_tournament |
| player1_id, player2_id | FK → users_player |
| created_at | datetime |

### tournaments_match
Матч: игроки/команды, сеты, дедлайн, рейтинг.

| Поле | Тип | Описание |
|------|-----|----------|
| id | PK | |
| tournament_id | FK → tournaments_tournament | Может быть NULL (спарринг) |
| match_type | varchar(20) | singles, doubles |
| sparring_response_id | FK → sparring_sparringresponse | Если матч из спарринга |
| court_id | FK → courts_court | |
| round_name | varchar(50) | |
| round_index, round_order | int | Позиция в сетке |
| is_consolation | bool | Утешительная сетка |
| deadline | datetime | Дедлайн сыграть |
| next_match_id | FK → tournaments_match | Победитель идёт сюда |
| loser_next_match_id | FK → tournaments_match | Проигравший (утешительная) |
| placement_min, placement_max | int | Места за матч |
| player1_id, player2_id | FK → users_player | Одиночки |
| partner1_id, partner2_id | FK → users_player | Парный состав |
| team1_id, team2_id | FK → tournaments_tournamentteam | Команды |
| winner_id | FK → users_player | Победитель (одиночки) |
| winner_team_id | FK → tournaments_tournamentteam | Победитель (пары) |
| player1_set1..set3, player2_set1..set3 | smallint | Счёты по сетам |
| scheduled_datetime | datetime | |
| completed_datetime | datetime | |
| status | varchar(20) | pending, completed, walkover, … |
| points_player1, points_player2 | int | Начисленные очки |
| rating_status | varchar(20) | |
| rating_delta_player1, rating_delta_player2 | real | Изменение рейтинга |
| created_at | datetime | |

### tournaments_matchresultproposal
Предложенный результат матча (один из игроков вносит счёт).

| Поле | Тип |
|------|-----|
| id | PK |
| match_id | FK → tournaments_match |
| proposer_id | FK → users_player |
| result | varchar(20) |
| player1_set1..set3, player2_set1..set3 | smallint |
| status | varchar(20) |
| created_at | datetime |

### tournaments_deadlineextensionrequest
Запрос на продление дедлайна матча.

| Поле | Тип |
|------|-----|
| id | PK |
| match_id | FK → tournaments_match |
| requested_by_id | FK → users_player |
| status | varchar(20) |
| created_at, processed_at | datetime |

### tournaments_headtohead
Личные встречи двух игроков.

| Поле | Тип |
|------|-----|
| id | PK |
| player1_id, player2_id | FK → users_player |
| player1_wins, player2_wins | int |

### tournaments_seasonrating
Рейтинг игрока по сезону/категории.

| Поле | Тип |
|------|-----|
| id | PK |
| player_id | FK → users_player |
| season | varchar(20) |
| category | varchar(20) |
| points | int |
| rank | int |

### tournaments_tournamentplayerresult
Результат игрока в турнире: место, раунд выбывания, очки.

| Поле | Тип |
|------|-----|
| id | PK |
| tournament_id | FK → tournaments_tournament |
| player_id | FK → users_player |
| round_eliminated | varchar(10) |
| place | int |
| fan_points | int |
| is_consolation | bool |

### tournaments_seasonpoints
Текущие очки сезона у игрока.

| Поле | Тип |
|------|-----|
| id | PK |
| player_id | FK → users_player UNIQUE |
| current_season_points | int |
| season_name | varchar(20) |
| season_year | int |
| updated_at | datetime |

### tournaments_seasonarchive
Архив сезона: итоговые очки и место.

| Поле | Тип |
|------|-----|
| id | PK |
| player_id | FK → users_player |
| season_name | varchar(20) |
| season_year | int |
| final_points | int |
| final_rank | int |
| archived_at | datetime |

---

## Корты (courts)

### courts_court
Корт: название, адрес, покрытие, контакты, координаты.

| Поле | Тип |
|------|-----|
| id | PK |
| name | varchar(200) |
| slug | varchar(50) UNIQUE |
| city | varchar(100) |
| address | varchar(255) |
| district | varchar(100) |
| description | text |
| surface | varchar(20) |
| courts_count | smallint |
| has_lighting | bool |
| is_indoor | bool |
| phone | varchar(50) |
| whatsapp | varchar(20) |
| website | varchar(200) |
| sells_balls | bool |
| sells_water | bool |
| multiple_payment_methods | bool |
| image | varchar(100) |
| latitude, longitude | decimal |
| price_per_hour | decimal |
| is_active | bool |
| created_at, updated_at | datetime |

### courts_courtapplication
Заявка на добавление/изменение корта.

| Поле | Тип |
|------|-----|
| id | PK |
| status | varchar(20) |
| court_id | FK → courts_court (nullable) |
| applicant_name, applicant_email, applicant_phone | |
| name, city, address, description | |
| surface, courts_count | |
| has_lighting, is_indoor | |
| phone, whatsapp, website | |
| image | |
| latitude, longitude, price_per_hour | |
| created_at, updated_at | datetime |

### courts_courtrating
Оценка корта пользователем.

| Поле | Тип |
|------|-----|
| id | PK |
| court_id | FK → courts_court |
| user_id | FK → users_user |
| score | smallint |
| created_at, updated_at | datetime |

---

## Спарринг (sparring)

### sparring_sparringrequest
Заявка на одиночный спарринг 1×1.

| Поле | Тип |
|------|-----|
| id | PK |
| player_id | FK → users_player |
| city | varchar(100) |
| desired_category | varchar(20) |
| description | text |
| preferred_days | varchar(100) |
| preferred_time | varchar(100) |
| desired_partner_age_min, desired_partner_age_max | int |
| preferred_location | varchar(200) |
| is_friendly | bool |
| match_type | varchar(20) |
| preferred_gender | varchar(20) |
| status | varchar(20) |
| created_at, updated_at | datetime |

### sparring_sparringresponse
Отклик на заявку (игрок откликнулся на sparring_request).

| Поле | Тип |
|------|-----|
| id | PK |
| sparring_request_id | FK → sparring_sparringrequest |
| respondent_id | FK → users_player |
| contact_method | varchar(20) |
| status | varchar(20) |
| created_at, updated_at | datetime |

### doubles_sparring_match_request (парный спарринг 2×2)
Заявка на парный матч.

| Поле | Тип |
|------|-----|
| id | PK |
| status | varchar(20) |
| created_by_id | FK → users_player |
| city | varchar(100) |
| preferred_gender | varchar(20) |
| is_friendly | bool |
| description | text |
| preferred_days | varchar(100) |
| preferred_time | varchar(100) |
| desired_level | varchar(20) |
| desired_age_min, desired_age_max | int |
| preferred_location | varchar(200) |
| created_at, updated_at | datetime |
| confirmed_at | datetime |
| match_id | FK → tournaments_match (если создан матч) |

### doubles_sparring_team
Команда в заявке на парный спарринг (сторона A/B).

| Поле | Тип |
|------|-----|
| id | PK |
| match_request_id | FK → doubles_sparring_match_request |
| side | varchar(20) |

### doubles_sparring_team_member
Участник команды (2 человека в команде).

| Поле | Тип |
|------|-----|
| id | PK |
| team_id | FK → doubles_sparring_team |
| player_id | FK → users_player |
| is_captain | bool |

### doubles_sparring_join_request
Заявка на присоединение к одной из сторон парного матча.

| Поле | Тип |
|------|-----|
| id | PK |
| match_request_id | FK → doubles_sparring_match_request |
| target_side | varchar(20) |
| status | varchar(20) |
| created_by_id | FK → users_player |
| created_at, updated_at, processed_at | datetime |

### doubles_sparring_join_request_member
Участники заявки на присоединение (пара откликается).

| Поле | Тип |
|------|-----|
| id | PK |
| join_request_id | FK → doubles_sparring_join_request |
| player_id | FK → users_player |
| order | smallint |

---

## Тренировки (training)

### training_coach
Тренер: привязка к User, фото, био, контакты.

| Поле | Тип |
|------|-----|
| id | PK |
| user_id | FK → users_user UNIQUE |
| name | varchar(100) |
| slug | varchar(50) UNIQUE |
| photo | varchar(100) |
| bio | text |
| experience_years | smallint |
| specialization | varchar(200) |
| phone | varchar(20) |
| telegram | varchar(100) |
| whatsapp | varchar(20) |
| max_contact | varchar(500) |
| city | varchar(100) |
| is_active | bool |
| created_at | datetime |

### training_coachapplication
Заявка на добавление/изменение тренера.

| Поле | Тип |
|------|-----|
| id | PK |
| status | varchar(20) |
| coach_id | FK → training_coach (nullable) |
| applicant_user_id | FK → users_user (nullable) |
| applicant_name, applicant_email, applicant_phone | |
| name, photo, bio | |
| experience_years, specialization | |
| phone, telegram, whatsapp, max_contact, city | |
| created_at, updated_at | datetime |

### training_training
Тренировка: название, тип, уровень, тренер, корт, расписание, цена.

| Поле | Тип |
|------|-----|
| id | PK |
| title | varchar(200) |
| slug | varchar(50) UNIQUE |
| description | text |
| short_description | varchar(300) |
| training_type | varchar(20) |
| skill_level | varchar(20) |
| target_category | varchar(20) |
| coach_id | FK → training_coach |
| court_id | FK → courts_court |
| city | varchar(100) |
| duration_minutes | smallint |
| max_participants | smallint |
| price | decimal |
| schedule | text |
| image | varchar(100) |
| is_active | bool |
| is_featured | bool |
| created_at, updated_at | datetime |

### training_trainingenrollment
Запись на тренировку.

| Поле | Тип |
|------|-----|
| id | PK |
| training_id | FK → training_training |
| player_id | FK → users_player |
| status | varchar(20) |
| full_name | varchar(200) |
| telegram | varchar(100) |
| whatsapp | varchar(20) |
| email | varchar(254) |
| preferred_datetime | datetime |
| desired_court_id | FK → courts_court |
| message | text |
| created_at, updated_at | datetime |

---

## Контент (content)

### content_news
Новость: заголовок, slug, текст, картинка, дата публикации.

| Поле | Тип |
|------|-----|
| id | PK |
| title | varchar(200) |
| slug | varchar(50) UNIQUE |
| excerpt | varchar(300) |
| content | text |
| image | varchar(100) |
| is_published | bool |
| is_featured | bool |
| views_count | int |
| created_at, updated_at | datetime |
| published_at | datetime |

### content_newsphoto
Фото к новости (несколько).

| Поле | Тип |
|------|-----|
| id | PK |
| news_id | FK → content_news |
| image | varchar(100) |
| caption | varchar(200) |
| order | smallint |

### content_gallery
Фото-галерея (может быть привязана к турниру).

| Поле | Тип |
|------|-----|
| id | PK |
| title | varchar(200) |
| slug | varchar(50) UNIQUE |
| description | text |
| cover_image | varchar(100) |
| tournament_id | FK → tournaments_tournament |
| is_published | bool |
| created_at | datetime |

### content_photo
Фото в галерее.

| Поле | Тип |
|------|-----|
| id | PK |
| gallery_id | FK → content_gallery |
| image | varchar(100) |
| caption | varchar(200) |
| order | smallint |
| created_at | datetime |

### content_page
Статическая страница (О нас, правила и т.д.).

| Поле | Тип |
|------|-----|
| id | PK |
| title | varchar(200) |
| slug | varchar(50) UNIQUE |
| content | text |
| is_published | bool |
| show_in_footer | bool |
| order | smallint |
| created_at, updated_at | datetime |

### content_aboutus
Страница «О нас» (одна запись).

| Поле | Тип |
|------|-----|
| id | PK |
| subtitle | varchar(300) |
| image | varchar(100) |
| body | text |
| updated_at | datetime |

### content_contactpage, content_contactitem
Контакты: страница и элементы (телефон, email, соцсети).

### content_rulessection
Разделы правил (slug, title, body).

### content_videopage
Страница видео: заголовки блоков.

### content_livestream
Прямые трансляции.

| Поле | Тип |
|------|-----|
| id | PK |
| video_page_id | FK → content_videopage |
| title | varchar(200) |
| url | varchar(200) |
| platform | varchar(20) |
| is_active | bool |
| order | smallint |
| created_at | datetime |

### content_video
Видео в плейлисте.

| Поле | Тип |
|------|-----|
| id | PK |
| video_page_id | FK → content_videopage |
| title | varchar(200) |
| description | text |
| url | varchar(200) |
| platform | varchar(20) |
| thumbnail_url | varchar(200) |
| is_published | bool |
| order | smallint |
| views_count | int |
| created_at, updated_at | datetime |

### content_stringerpage
Страница стрингеров (включена/выключена).

### content_stringercompany
Компания-стрингер: название, адрес, цена, описание.

| Поле | Тип |
|------|-----|
| id | PK |
| stringer_page_id | FK → content_stringerpage |
| name | varchar(200) |
| address | varchar(300) |
| price | varchar(200) |
| description | text |
| is_active | bool |
| order | smallint |
| created_at, updated_at | datetime |

### content_stringercompanycontact
Контакт компании (телефон, сайт и т.д.).

### content_stringercompanyphoto
Фото компании.

### content_stringercompanyrating
Оценка компании пользователем (score, comment).

---

## Комментарии (comments)

### comments_comment
Универсальный комментарий (content_type + object_id): к матчу, к игроку и т.д.

| Поле | Тип |
|------|-----|
| id | PK |
| content_type_id | FK → django_content_type |
| object_id | int |
| author_id | FK → users_player |
| text | text |
| rating_agreement | smallint |
| rating_judging | smallint |
| is_approved | bool |
| created_at, updated_at | datetime |

---

## Подписки (subscriptions)

### subscriptions_subscriptiontier
Тариф подписки: название, цена, лимиты, возможности.

| Поле | Тип |
|------|-----|
| id | PK |
| name | varchar(50) UNIQUE |
| price | decimal |
| max_tournaments | int |
| is_unlimited | bool |
| one_day_tournament_discount | int |
| can_see_stats | bool |
| can_read_comments | bool |
| can_write_comments | bool |
| can_rate_opponents | bool |
| has_private_chat | bool |
| has_sparring | bool |
| has_admin_support | bool |
| has_badge | bool |

### subscriptions_usersubscription
Подписка пользователя на тариф.

| Поле | Тип |
|------|-----|
| id | PK |
| user_id | FK → users_user UNIQUE |
| tier_id | FK → subscriptions_subscriptiontier |
| start_date, end_date | datetime |
| is_active | bool |
| cancelled_at | datetime |
| tournaments_registered_count | int |

### subscriptions_regionaltierprice
Региональная цена тарифа (вариант названия/цены).

| Поле | Тип |
|------|-----|
| id | PK |
| tier_id | FK → subscriptions_subscriptiontier |
| price | decimal |
| name | varchar(100) |

---

## Навигация (navigation)

### navigation_menuitem
Пункт меню: title, url, order, is_active.

---

## Магазин (shop)

### shop_shoppage
Страница магазина (intro_text).

### shop_product
Товар: название, размер, количество, описание, цена.

| Поле | Тип |
|------|-----|
| id | PK |
| name | varchar(200) |
| size | varchar(100) |
| quantity | int |
| description | text |
| price | decimal |
| order | smallint |
| created_at, updated_at | datetime |

### shop_productphoto
Фото товара.

### shop_purchaserequest
Заявка на покупку.

| Поле | Тип |
|------|-----|
| id | PK |
| product_id | FK → shop_product |
| user_id | FK → users_user |
| first_name, last_name | varchar(100) |
| contact_phone | varchar(50) |
| comment | text |
| status | varchar(20) |
| created_at, updated_at | datetime |

---

## Ядро и Telegram (core)

### core_usertelegramlink
Связь пользователя с Telegram (чат бота ЛК, токен привязки).

| Поле | Тип |
|------|-----|
| id | PK |
| user_id | FK → users_user UNIQUE |
| telegram_chat_id | bigint UNIQUE |
| user_bot_chat_id | bigint UNIQUE |
| binding_token | varchar(64) UNIQUE |
| token_created_at | datetime |
| created_at, updated_at | datetime |

### core_telegramtransferconsentlog
Лог согласия на передачу данных в Telegram.

### core_supportmessage
Сообщение в поддержку (от пользователя или гостя, ответ админа в Telegram).

| Поле | Тип |
|------|-----|
| id | PK |
| user_id | FK → users_user (nullable) |
| guest_name, guest_contact | varchar(200) |
| guest_telegram_username | varchar(100) |
| guest_telegram_chat_id | bigint |
| guest_binding_token | varchar(64) UNIQUE |
| subject | varchar(200) |
| text | text |
| is_from_admin | bool |
| created_at | datetime |
| admin_telegram_message_id | bigint |
| admin_telegram_text | text |

### core_feedback
Обратная связь с сайта.

| Поле | Тип |
|------|-----|
| id | PK |
| user_id | FK → users_user |
| subject | varchar(200) |
| message | text |
| created_at | datetime |
| telegram_message_id | bigint |

### core_feedbackreply
Ответ на обратную связь.

| Поле | Тип |
|------|-----|
| id | PK |
| feedback_id | FK → core_feedback |
| text | text |
| created_at | datetime |

### telegram_bot_telegrambroadcast
Рассылка в Telegram (текст, картинка, дата отправки, кто создал).

---

## Защита от брутфорса (axes)

### axes_accessattempt
Попытки входа (IP, username, user_agent, время, количество неудач).

### axes_accessfailurelog
Лог блокировок.

### axes_accesslog
Успешные входы (для учёта сессий).

### axes_accessattemptexpiration
Время истечения блокировки по попытке.

---

## Сводка связей

- **users_user** — центр: к нему привязаны **users_player**, подписки, уведомления, поддержка, обратная связь, привязка Telegram.
- **users_player** — участвует в турнирах (participants, команды, матчи), спаррингах, тренировках, комментариях, рейтингах, сезонах.
- **tournaments_match** — связывает турнир (или спарринг), игроков/команды, корт, предложения результата, продления дедлайна.
- **courts_court** — используется в матчах, тренировках, заявках на корты.
- Контент (новости, страницы, галереи, видео, стрингеры) — отдельные деревья с FK внутри content.

Полный SQL-дамп схемы (CREATE TABLE + индексы) можно получить командой:
```bash
sqlite3 db.sqlite3 ".schema"
```
или для PostgreSQL после миграций:
```bash
python manage.py sqlmigrate <app> <migration_number>
```
