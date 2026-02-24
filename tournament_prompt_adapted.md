# Промт для Claude Code: Система ТВД-турниров (адаптация под существующую БД)
## Бэкенд + Фронтенд + Интеграция в существующий проект

---

## ⚠️ КРИТИЧЕСКИ ВАЖНО — ЧИТАТЬ ПЕРВЫМ

В проекте уже существует база данных с работающими турнирами другого формата.
**Категорически запрещается:**
- Удалять или изменять существующие таблицы
- Переименовывать существующие колонки
- Менять существующие внешние ключи
- Создавать таблицы с именами, которые уже существуют

Вся новая функциональность строится либо через **новые таблицы**, либо через **расширение существующих** (добавление колонок с DEFAULT, чтобы не сломать текущие записи), либо через **повторное использование** уже существующих таблиц, если их структура подходит.

---

# ШАГ 0: ОБЯЗАТЕЛЬНАЯ РАЗВЕДКА СХЕМЫ (выполнить ПЕРВЫМ)

Перед написанием любого кода выполни следующие SQL-запросы и тщательно изучи результаты.

## 0.1 Полная карта таблиц

```sql
SELECT
  t.table_name,
  obj_description(pgc.oid, 'pg_class') AS table_comment
FROM information_schema.tables t
JOIN pg_class pgc ON pgc.relname = t.table_name
WHERE t.table_schema = 'public'
  AND t.table_type = 'BASE TABLE'
ORDER BY t.table_name;
```

## 0.2 Структура каждой таблицы (колонки, типы, дефолты, NOT NULL)

```sql
SELECT
  table_name,
  column_name,
  data_type,
  character_maximum_length,
  column_default,
  is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
ORDER BY table_name, ordinal_position;
```

## 0.3 Все внешние ключи

```sql
SELECT
  tc.table_name AS child_table,
  kcu.column_name AS child_column,
  ccu.table_name AS parent_table,
  ccu.column_name AS parent_column,
  tc.constraint_name
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
  ON tc.constraint_name = kcu.constraint_name
JOIN information_schema.constraint_column_usage ccu
  ON ccu.constraint_name = tc.constraint_name
WHERE tc.constraint_type = 'FOREIGN KEY'
ORDER BY child_table, child_column;
```

## 0.4 Индексы и уникальные ограничения

```sql
SELECT indexname, tablename, indexdef
FROM pg_indexes
WHERE schemaname = 'public'
ORDER BY tablename, indexname;
```

## 0.5 Пример данных из ключевых таблиц

После изучения структуры выполни:
```sql
-- Подставить реальные имена таблиц, которые ты нашёл выше
SELECT * FROM <таблица_игроков> LIMIT 3;
SELECT * FROM <таблица_турниров> LIMIT 3;
SELECT * FROM <таблица_матчей> LIMIT 3;
SELECT * FROM <таблица_рейтинга> LIMIT 3;
```

## 0.6 Составь маппинг (ОБЯЗАТЕЛЬНО задокументируй в SCHEMA_MAP.md)

После изучения схемы создай файл `docs/SCHEMA_MAP.md` со следующей таблицей:

```markdown
# Маппинг существующей схемы на функциональность ТВД

| Нужная сущность        | Существующая таблица     | Колонки совпадают | Нужные добавления |
|------------------------|--------------------------|-------------------|-------------------|
| Игроки                 | ???                      | ???               | ???               |
| Сезоны                 | ???                      | ???               | ???               |
| Турниры                | ???                      | ???               | ???               |
| Участники турнира      | ???                      | ???               | ???               |
| Группы                 | ??? или создать новую    | ???               | ???               |
| Участники групп        | ??? или создать новую    | ???               | ???               |
| Матчи                  | ???                      | ???               | ???               |
| Плей-офф сетка         | ???                      | ???               | ???               |
| Итоги турнира          | ???                      | ???               | ???               |
| Сезонный рейтинг       | ???                      | ???               | ???               |
| Рейтинг мастерства     | ???                      | ???               | ???               |
```

**Только после составления этого маппинга приступай к написанию кода.**

---

# ШАГ 1: МИГРАЦИИ (только расширения, без разрушений)

На основе маппинга из Шага 0 создай файл `db/migrations/tvd_001_initial.sql`.

## 1.1 Принцип написания миграций

```sql
-- Каждое изменение существующей таблицы:
ALTER TABLE <существующая_таблица>
  ADD COLUMN IF NOT EXISTS <новая_колонка> <тип> DEFAULT <значение>;

-- Каждая новая таблица:
CREATE TABLE IF NOT EXISTS <новая_таблица> (
  ...
);

-- Каждый новый индекс:
CREATE INDEX IF NOT EXISTS <имя> ON <таблица>(<колонка>);
```

**Никаких `DROP`, `ALTER COLUMN`, `RENAME` без явного согласования.**

## 1.2 Что необходимо обеспечить в схеме

Независимо от существующей структуры, следующие данные должны где-то храниться.
Используй существующие таблицы если подходят, создай новые если нет.

### Для турниров типа ТВД:

Поле-маркер типа турнира — `tournament_type` или аналог со значением `'tvd'`.
Поле формата матча — `match_format`: `'one_set_supertb'` (один сет + тай-брейк) или `'best_of_three'`.

Если в существующей таблице турниров уже есть поле типа — добавь значение `'tvd'` в допустимые.
Если нет — добавь колонку `ADD COLUMN IF NOT EXISTS tournament_type VARCHAR(30) DEFAULT 'standard'`.

### Для групп (скорее всего таблицы нет — создать):

```sql
CREATE TABLE IF NOT EXISTS tvd_groups (
  id SERIAL PRIMARY KEY,
  tournament_id INTEGER NOT NULL REFERENCES <таблица_турниров>(id) ON DELETE CASCADE,
  name VARCHAR(10) NOT NULL,        -- 'A', 'B', 'C', 'D'...
  group_order INTEGER NOT NULL,
  created_at TIMESTAMP DEFAULT NOW(),
  UNIQUE(tournament_id, name)
);

CREATE TABLE IF NOT EXISTS tvd_group_members (
  id SERIAL PRIMARY KEY,
  group_id INTEGER NOT NULL REFERENCES tvd_groups(id) ON DELETE CASCADE,
  player_id INTEGER NOT NULL REFERENCES <таблица_игроков>(id),
  seed_number INTEGER,
  wins INTEGER DEFAULT 0,
  losses INTEGER DEFAULT 0,
  games_won INTEGER DEFAULT 0,
  games_lost INTEGER DEFAULT 0,
  games_diff INTEGER GENERATED ALWAYS AS (games_won - games_lost) STORED,
  final_place INTEGER,              -- 1, 2, 3 (или 4 для групп по 4)
  tiebreak_method VARCHAR(30),      -- 'head_to_head' | 'random' — как определено место
  UNIQUE(group_id, player_id)
);
```

### Для матчей ТВД:

Если существующая таблица матчей достаточно гибкая (есть поле типа/этапа) — используй её,
добавив недостающие колонки:

```sql
ALTER TABLE <таблица_матчей>
  ADD COLUMN IF NOT EXISTS tvd_group_id INTEGER REFERENCES tvd_groups(id),
  ADD COLUMN IF NOT EXISTS tvd_stage VARCHAR(30),
  -- 'group' | 'main_quarterfinal' | 'main_semifinal' | 'main_final'
  -- 'third_place' | 'consolation_semifinal' | 'consolation_final'
  ADD COLUMN IF NOT EXISTS set1_score_p1 INTEGER,
  ADD COLUMN IF NOT EXISTS set1_score_p2 INTEGER,
  ADD COLUMN IF NOT EXISTS supertb_score_p1 INTEGER,
  ADD COLUMN IF NOT EXISTS supertb_score_p2 INTEGER,
  ADD COLUMN IF NOT EXISTS match_order INTEGER,
  ADD COLUMN IF NOT EXISTS is_walkover BOOLEAN DEFAULT FALSE;
```

Если структура существующей таблицы матчей несовместима — создай отдельную:

```sql
CREATE TABLE IF NOT EXISTS tvd_matches (
  id SERIAL PRIMARY KEY,
  tournament_id INTEGER NOT NULL REFERENCES <таблица_турниров>(id),
  tvd_group_id INTEGER REFERENCES tvd_groups(id),
  tvd_stage VARCHAR(30) NOT NULL,
  player1_id INTEGER NOT NULL REFERENCES <таблица_игроков>(id),
  player2_id INTEGER NOT NULL REFERENCES <таблица_игроков>(id),
  set1_score_p1 INTEGER,
  set1_score_p2 INTEGER,
  supertb_score_p1 INTEGER,
  supertb_score_p2 INTEGER,
  winner_id INTEGER REFERENCES <таблица_игроков>(id),
  status VARCHAR(20) DEFAULT 'scheduled',
  is_walkover BOOLEAN DEFAULT FALSE,
  match_order INTEGER,
  played_at TIMESTAMP,
  created_at TIMESTAMP DEFAULT NOW(),
  CHECK (player1_id != player2_id)
);
```

### Для итогов турнира и рейтинга:

Если уже есть таблица результатов — расширь:
```sql
ALTER TABLE <таблица_результатов>
  ADD COLUMN IF NOT EXISTS tvd_final_place INTEGER,
  ADD COLUMN IF NOT EXISTS tvd_points_earned INTEGER DEFAULT 0,
  ADD COLUMN IF NOT EXISTS elo_before INTEGER,
  ADD COLUMN IF NOT EXISTS elo_after INTEGER;
```

Если нет — создай:
```sql
CREATE TABLE IF NOT EXISTS tvd_tournament_results (
  id SERIAL PRIMARY KEY,
  tournament_id INTEGER NOT NULL REFERENCES <таблица_турниров>(id),
  player_id INTEGER NOT NULL REFERENCES <таблица_игроков>(id),
  final_place INTEGER NOT NULL,
  points_earned INTEGER NOT NULL DEFAULT 0,
  elo_before INTEGER NOT NULL,
  elo_after INTEGER NOT NULL,
  matches_played INTEGER DEFAULT 0,
  UNIQUE(tournament_id, player_id)
);
```

### Для рейтинга мастерства:

Найди где хранится числовой рейтинг игрока (поле типа `rating`, `elo`, `strength` и т.п.).
Если существует — использовать его. Если нет:
```sql
ALTER TABLE <таблица_игроков>
  ADD COLUMN IF NOT EXISTS elo_rating INTEGER NOT NULL DEFAULT 1200,
  ADD COLUMN IF NOT EXISTS total_matches_played INTEGER NOT NULL DEFAULT 0;
```

### Для сезонного рейтинга:

Найди существующую таблицу сезонных очков. Если подходит — используй.
Если нет, создай:
```sql
CREATE TABLE IF NOT EXISTS tvd_season_rating (
  id SERIAL PRIMARY KEY,
  player_id INTEGER NOT NULL REFERENCES <таблица_игроков>(id),
  season_id INTEGER NOT NULL REFERENCES <таблица_сезонов>(id),
  total_points INTEGER NOT NULL DEFAULT 0,
  tournaments_played INTEGER NOT NULL DEFAULT 0,
  best_result INTEGER,              -- лучшее место в сезоне
  UNIQUE(player_id, season_id)
);
```

---

# ШАГ 2: ЛОГИКА ФОРМИРОВАНИЯ ГРУПП

Создай `src/services/tvd/groupFormation.js`.

## 2.1 Определение структуры групп

```javascript
function calculateGroupStructure(participantCount) {
  // participantCount % 3 === 0 → все группы по 3
  // participantCount % 3 === 1 → (N-1) групп по 3, 1 группа по 4
  // participantCount % 3 === 2 → (N-1) групп по 3, 1 группа по 2
  // Минимум: 4 участника (2 группы)
  // Предупреждение: > 24 участника (слишком много групп)
}
```

## 2.2 Посев игроков

```javascript
async function assignSeeds(tournamentId, db) {
  // 1. Загрузить участников турнира с их текущим elo_rating
  //    (использовать реальное имя поля из маппинга Шага 0)
  // 2. Сортировать: elo_rating DESC, при равных → по дате регистрации ASC
  // 3. Игроки с NULL рейтингом → в конец списка
  // 4. Первые N игроков (N = количество групп) → seed_number 1..N
  // 5. Записать seed_number в <таблица_участников_турнира>
}
```

## 2.3 Распределение змейкой

```javascript
function serpentineDistribution(sortedPlayers, groupCount) {
  // Раунд 1 (→): players[0]→G[0], players[1]→G[1], ..., players[N-1]→G[N-1]
  // Раунд 2 (←): players[N]→G[N-1], players[N+1]→G[N-2], ...
  // Раунд 3 (→): players[2N]→G[0], ...
  // Чередовать направление каждый раунд
  // Гарантия: сеяные (1..N) попадают ровно по одному в каждую группу
}
```

## 2.4 Генерация матчей группового этапа

```javascript
function generateGroupMatches(groupMembers) {
  // Группа 3 человека [A,B,C]:
  //   A-B (match_order=1), A-C (order=2), B-C (order=3)
  // Группа 4 человека [A,B,C,D]:
  //   A-B(1), C-D(2), A-C(3), B-D(4), A-D(5), B-C(6)
  // Записать в таблицу матчей со stage='group', status='scheduled'
}
```

---

# ШАГ 3: ПОДСЧЁТ МЕСТ В ГРУППЕ

Создай `src/services/tvd/groupStandings.js`.

```javascript
async function recalculateGroupStandings(groupId, db) {
  // После каждого завершённого матча группы:
  // 1. Загрузить все завершённые матчи группы
  // 2. Пересчитать wins, losses, games_won, games_lost для каждого участника
  // 3. Сортировать по приоритету:
  //    a) wins DESC
  //    b) games_diff DESC
  //    c) Личная встреча: победитель матча между равными игроками
  //    d) Жеребьёвка (random) — логировать в поле tiebreak_method
  // 4. Записать final_place (1, 2, 3...) в tvd_group_members
  // 5. Если все матчи группы completed/walkover → группа завершена
}
```

---

# ШАГ 4: ПЛЕЙ-ОФФ СЕТКИ

Создай `src/services/tvd/playoffBracket.js`.

## 4.1 Формирование основной сетки

```javascript
async function generateMainBracket(tournamentId, db) {
  // Основная сетка: final_place=1 (победители) + final_place=2 (вторые)
  // Утешительная: final_place=3 (и final_place=4 если были группы по 4)
  //
  // Размер сетки: ближайшая степень 2 >= числа участников
  // BYE: лучшим по (wins → games_diff) среди победителей групп
  //
  // Составление пар 1/4 (4 группы A,B,C,D):
  //   Пара 1: 1A vs 2D ─┐ → полуфинал 1
  //   Пара 2: 1C vs 2B ─┘
  //   Пара 3: 1B vs 2C ─┐ → полуфинал 2
  //   Пара 4: 1D vs 2A ─┘
  //
  // Правило: игроки из одной группы НЕ встречаются в 1/4
  // После генерации: создать матчи в БД со статусом 'scheduled'
  //                  победители предыдущих раундов = player1_id/player2_id = NULL (TBD)
}
```

## 4.2 Продвижение победителя

```javascript
async function advanceWinner(matchId, winnerId, db) {
  // Найти следующий матч в сетке по stage и match_order
  // Записать winnerId в player1_id или player2_id следующего матча
  // Если оба игрока следующего матча определены → статус → 'scheduled'
}
```
# ШАГ 4-Б: НЕСТАНДАРТНЫЕ ФОРМАТЫ ПЛЕЙ-ОФФ

## Система должна поддерживать несколько сценариев плей-офф в зависимости от количества групп. Формат определяется автоматически при генерации плей-офф.

---

### Сценарий 1: 4 группы (12 участников) — стандартный
8 игроков → 1/4 финала → Полуфинал → Финал (нокаут)

---

### Сценарий 2: 6 групп (18 участников) — воронка 1/6 → 1/3 → групповой финал
12 игроков (все 1-е и 2-е места)  
→ 1/6 финала (6 матчей)  
→ 1/3 финала (3 матча)  
→ Топ-3 победителя  

Финал: ROUND-ROBIN (мини-группа из 3 игроков)

Итог:
- 1 / 2 / 3 места определяются по:
  - количеству побед (wins)
  - разнице геймов (games_diff) внутри финальной группы

---

### Сценарий 3: 3 группы (9 участников)
6 игроков → Полуфинал (3 пары + BYE лучшему) → Финал (нокаут)

---

### Сценарий 4: 5 групп (15 участников)
10 игроков → 1/5 финала (5 матчей)  
→ Полуфинал (4 победителя + BYE лучшему)  
→ Финал (нокаут)

ИЛИ: упростить до сетки на 8 игроков с BYE для 2 лучших победителей групп

---

## Таблица для автовыбора сценария

```javascript
function getPlayoffFormat(groupCount) {
  const formats = {
    2: 'semifinal_final',      // 4 игрока → сразу полуфинал
    3: 'semifinal_final',      // 6 → полуфинал с 1 BYE
    4: 'standard_bracket',     // 8 → 1/4 → 1/2 → финал
    5: 'extended_bracket',     // 10 → 1/5 → 1/2 → финал
    6: 'funnel_group_final',   // 12 → 1/6 → 1/3 → групповой финал
  };

  return formats[groupCount] || 'standard_bracket';
}
---

# ШАГ 5: ВВОД РЕЗУЛЬТАТОВ МАТЧЕЙ

Создай `src/services/tvd/matchResult.js`.

## 5.1 Валидация счёта

```javascript
function validateSetScore(p1, p2) {
  // Допустимые счёты сета:
  // 6:0, 6:1, 6:2, 6:3, 6:4  (или зеркально)
  // 7:5 (или 5:7)
  // 7:6 (или 6:7) — тай-брейк
  // НЕ допускается: 6:6, 8:5, и т.п.
}

function validateSupertiebreak(p1, p2) {
  // Победитель >= 10, разница >= 2
  // Примеры: 10:8 ✓, 10:0 ✓, 12:10 ✓, 10:9 ✗, 9:7 ✗
}
```

## 5.2 Сохранение результата (всё в одной транзакции)

```javascript
async function saveMatchResult(matchId, scores, db) {
  // BEGIN TRANSACTION
  // 1. Валидировать scores
  // 2. Определить winner_id
  // 3. Записать счёт и winner в таблицу матчей, статус → 'completed'
  // 4. Если групповой матч:
  //    → recalculateGroupStandings(groupId)
  //    → проверить завершённость всего группового этапа
  //    → если завершён: generateMainBracket(tournamentId)
  // 5. Если плей-офф матч:
  //    → advanceWinner(matchId, winnerId)
  //    → проверить завершённость всего плей-офф
  // 6. Рассчитать изменение Elo (см. Шаг 6) и обновить рейтинги
  // COMMIT
  // При ошибке: ROLLBACK
}
```

## 5.3 Неявка (Walkover)

```javascript
async function recordWalkover(matchId, winnerId, db) {
  // Записать is_walkover=true, winner_id, status='walkover'
  // Elo пересчитывается как за реальное поражение/победу
  // Проигравший по неявке получает минимум очков (5)
  // Продолжить пайплайн как при обычной победе
}
```

---

# ШАГ 6: РЕЙТИНГ МАСТЕРСТВА (Elo с гибридной формулой)

Создай `src/services/tvd/eloRating.js`.

## 6.1 Точные формулы (использовать ИМЕННО эти, не стандартный Elo)

```javascript
/**
 * Ожидаемый результат игрока A против B
 * Делитель 800 (не 400!) — более мягкая кривая для любителей
 */
function expectedScore(ratingA, ratingB) {
  return 1 / (1 + Math.pow(10, (ratingB - ratingA) / 800));
}

/**
 * Фактический результат игрока A — ГИБРИДНАЯ формула
 * 70% от доли выигранных геймов + 30% от факта победы в матче
 *
 * @param {number} gamesWonA   - количество геймов, выигранных A
 * @param {number} gamesWonB   - количество геймов, выигранных B
 * @param {boolean} aWon       - победил ли A в матче
 */
function actualScore(gamesWonA, gamesWonB, aWon) {
  const totalGames = gamesWonA + gamesWonB;
  if (totalGames === 0) return aWon ? 1 : 0; // edge case: walkover
  const gameShare = gamesWonA / totalGames;
  const matchResult = aWon ? 1 : 0;
  return 0.7 * gameShare + 0.3 * matchResult;
}

/**
 * K-фактор (коэффициент чувствительности)
 * Зависит от ОБЩЕГО числа сыгранных матчей игрока (не только за сезон)
 * Использовать поле total_matches_played из таблицы игроков
 */
function kFactor(totalMatchesPlayed) {
  return totalMatchesPlayed < 10 ? 250 : 50;
}

/**
 * Полный расчёт изменения рейтинга для матча
 * Возвращает { newRatingA, newRatingB, deltaA, deltaB }
 */
function calculateEloChange(ratingA, ratingB, gamesA, gamesB, aWon, matchesA, matchesB) {
  const EA = expectedScore(ratingA, ratingB);
  const EB = 1 - EA; // симметрично

  const SA = actualScore(gamesA, gamesB, aWon);
  const SB = actualScore(gamesB, gamesA, !aWon);

  const KA = kFactor(matchesA);
  const KB = kFactor(matchesB);

  const deltaA = Math.round(KA * (SA - EA));
  const deltaB = Math.round(KB * (SB - EB));

  // Минимальный рейтинг: не опускать ниже 800
  const newRatingA = Math.max(800, ratingA + deltaA);
  const newRatingB = Math.max(800, ratingB + deltaB);

  return { newRatingA, newRatingB, deltaA, deltaB };
}
```

## 6.2 Подсчёт геймов из счёта матча

```javascript
function extractGames(set1p1, set1p2, supertbP1, supertbP2) {
  // Геймы первого сета: set1_score_p1, set1_score_p2
  // Матч-тай-брейк: НЕ считать как геймы (это очки, не геймы)
  //   → добавить 1 гейм победителю тай-брейка
  // Итого: gamesA = set1p1 + (supertbP1 > supertbP2 ? 1 : 0)
  //        gamesB = set1p2 + (supertbP2 > supertbP1 ? 1 : 0)
}
```

## 6.3 Порядок пересчёта Elo в турнире

```
Пересчитывать после КАЖДОГО сохранённого матча немедленно:
  1. Загрузить текущие рейтинги обоих игроков
  2. Загрузить total_matches_played обоих игроков
  3. Вычислить calculateEloChange(...)
  4. Обновить elo_rating и total_matches_played в таблице игроков
  5. Записать elo_before/elo_after в tvd_tournament_results (или аналог)

Это обеспечивает правильный порядок: каждый следующий матч
использует уже обновлённый рейтинг от предыдущего.
```

---

# ШАГ 7: НАЧИСЛЕНИЕ ТУРНИРНЫХ ОЧКОВ

Создай `src/services/tvd/scoring.js`.

```javascript
// Базовая таблица очков для 4 групп (12 участников)
const BASE_POINTS = {
  1: 75, 2: 55, 3: 40, 4: 30,
  5: 25, 6: 25,         // оба проигравших полуфиналиста
  7: 20, 8: 20,         // оба проигравших четвертьфиналиста
  9: 10,                // победитель утешительного финала
};
const DEFAULT_POINTS = 5; // все остальные

// Масштабирование под реальный размер турнира:
// points = Math.round(basePoints * (groupCount / 4))
// Минимум: 5 очков за участие

// После финализации турнира:
// INSERT INTO tvd_season_rating ... ON CONFLICT DO UPDATE total_points += earned
```

---

# ШАГ 8: REST API

Создай роуты в `src/routes/tvd/`. Все эндпоинты с префиксом `/api/tvd/`.

```
# Управление турниром
POST   /api/tvd/tournaments                          — создать ТВД-турнир
GET    /api/tvd/tournaments                          ?season=&status=
GET    /api/tvd/tournaments/:id
PATCH  /api/tvd/tournaments/:id/status

# Участники
POST   /api/tvd/tournaments/:id/participants
DELETE /api/tvd/tournaments/:id/participants/:playerId
GET    /api/tvd/tournaments/:id/participants

# Группы
POST   /api/tvd/tournaments/:id/generate-groups      — посев + змейка + создание матчей
GET    /api/tvd/tournaments/:id/groups               — группы + участники + матчи
POST   /api/tvd/tournaments/:id/groups/swap          — поменять двух игроков между группами
                                                        Body: { player1Id, player2Id }

# Плей-офф
POST   /api/tvd/tournaments/:id/generate-playoffs
GET    /api/tvd/tournaments/:id/bracket              — основная сетка (JSON-дерево)
GET    /api/tvd/tournaments/:id/consolation          — утешительная сетка

# Матчи
GET    /api/tvd/matches/:id
POST   /api/tvd/matches/:id/result
  Body: { set1_score_p1, set1_score_p2, supertb_score_p1?, supertb_score_p2? }
PATCH  /api/tvd/matches/:id/result                   — исправить (только до finalize)
POST   /api/tvd/matches/:id/walkover
  Body: { winner_id }

# Финализация
POST   /api/tvd/tournaments/:id/finalize             — начислить очки и зафиксировать Elo
GET    /api/tvd/tournaments/:id/results              — итоговая таблица

# Рейтинги (переиспользовать/дополнить существующие если есть)
GET    /api/tvd/rating/season/:seasonId
GET    /api/tvd/rating/elo
GET    /api/tvd/players/:id/history                  — история ТВД-турниров игрока

# Технический
GET    /api/tvd/health                               — статус сервиса
```

Формат ошибок: `{ "error": "ERROR_CODE", "message": "Текст", "details": {} }`

---

# ШАГ 9: БИЗНЕС-ПРАВИЛА

```
Регистрация:
  ✓ Только при tournament_type='tvd' И status='registration'
  ✓ Один игрок — один раз (UNIQUE constraint)
  ✓ Минимум 4 участника для генерации групп

Генерация групп:
  ✓ Меняет статус: registration → group_stage
  ✓ Нельзя регенерировать если хотя бы один матч сыгран
  ✓ Swap двух игроков: только если оба не сыграли ни одного матча

Ввод результата:
  ✓ Только матчи со status='scheduled' или 'in_progress'
  ✓ В плей-офф: предыдущий раунд должен быть завершён
  ✓ Исправление: разрешено до finalize (пересчитать всё)

Финализация:
  ✓ Только при status='playoffs'
  ✓ Все матчи: completed или walkover
  ✓ После финализации: status='completed', данные readonly

Walkover:
  ✓ winner_id должен быть участником этого матча
  ✓ Проигравший по неявке: минимум 5 очков
```

---

# ШАГ 10: ФРОНТЕНД — НАСТРОЙКА

Создай фронтенд в папке `client/` (или интегрируй в существующий фронтенд если он есть).

**СНАЧАЛА ПРОВЕРЬ:** существует ли уже фронтенд проект, какой стек используется (React/Vue/Next.js и т.д.), и какова его структура. Адаптируй интеграцию под существующий стек.

## Если фронтенд создаётся с нуля:

```bash
npm create vite@latest client -- --template react
cd client
npm install
npm install -D tailwindcss postcss autoprefixer
npm install react-router-dom axios
npx tailwindcss init -p
```

## 10.1 Дизайн-система (токены в tailwind.config.js)

```javascript
theme: {
  extend: {
    colors: {
      court: {
        50:  '#f0fdf4',
        400: '#4ade80',
        500: '#22c55e',   // основной акцент — теннисный корт
        600: '#16a34a',
        900: '#14532d',
      },
      surface: {
        900: '#0a0f0d',   // фон страницы
        800: '#111812',   // карточки
        700: '#1a2318',   // вторичный фон
        600: '#243020',   // hover
        400: '#4a5e44',   // разделители
        200: '#8fa888',   // вторичный текст
      }
    },
    fontFamily: {
      display: ['Bebas Neue', 'sans-serif'],
      body:    ['DM Sans', 'sans-serif'],
      mono:    ['JetBrains Mono', 'monospace'],
    },
    animation: {
      'slide-in':       'slideIn 0.3s ease-out',
      'pulse-green':    'pulseGreen 2s ease-in-out infinite',
      'bracket-reveal': 'bracketReveal 0.5s ease-out forwards',
      'stagger-in':     'slideIn 0.4s ease-out both',
    }
  }
}
```

Подключи шрифты в `index.html`:
```html
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=DM+Sans:ital,opsz,wght@0,9..40,300;0,9..40,400;0,9..40,500;0,9..40,600&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
```

## 10.2 Глобальные стили (src/index.css)

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

:root { color-scheme: dark; }

body {
  background-color: #0a0f0d;
  color: #e8f0e5;
  font-family: 'DM Sans', sans-serif;
}

/* Текстура корта — SVG-сетка */
body::before {
  content: '';
  position: fixed;
  inset: 0;
  background-image:
    linear-gradient(rgba(34,197,94,0.03) 1px, transparent 1px),
    linear-gradient(90deg, rgba(34,197,94,0.03) 1px, transparent 1px);
  background-size: 40px 40px;
  pointer-events: none;
  z-index: 0;
}

@keyframes slideIn {
  from { opacity: 0; transform: translateY(8px); }
  to   { opacity: 1; transform: translateY(0); }
}
@keyframes pulseGreen {
  0%, 100% { box-shadow: 0 0 0 0 rgba(34,197,94,0); }
  50%       { box-shadow: 0 0 0 6px rgba(34,197,94,0.2); }
}
@keyframes bracketReveal {
  from { opacity: 0; transform: scaleX(0.95); }
  to   { opacity: 1; transform: scaleX(1); }
}
```

---

# ШАГ 11: РОУТИНГ И СТРАНИЦЫ

## 11.1 App.jsx — роутер

```
/                              — главная (активные турниры + топ рейтинга)
/tournaments                   — все ТВД-турниры (фильтр по сезону/статусу)
/tournaments/new               — создать турнир [ADMIN]
/tournaments/:id               — страница турнира (4 таба)
/tournaments/:id/admin         — панель администратора [ADMIN]
/players                       — список игроков
/players/:id                   — профиль игрока
/rating                        — рейтинговые таблицы (2 таба)
/login                         — вход администратора
```

Система ролей: `localStorage.getItem('tvd_role')` = `'admin' | 'viewer'`.
Пароль администратора из `VITE_ADMIN_PASSWORD` (env).
Защищённые роуты: обернуть в `<RequireAdmin>` компонент.

## 11.2 Layout.jsx

```
Шапка (фиксированная, backdrop-blur-md):
  - Логотип: SVG теннисного мяча + название лиги (Bebas Neue 24px)
  - Навигация: Турниры | Игроки | Рейтинг
  - Справа: badge «ADMIN» (зелёный) или кнопка «Войти»
  - Зелёная линия-акцент снизу (2px, court-500)

Сайдбар (240px, коллапсируемый на мобиле):
  - Ближайшие/активные турниры
  - Каждый: название + дата + статус-пилюля

Мобиль: sidebar → нижняя навигационная панель (5 иконок)
```

---

# ШАГ 12: БАЗОВЫЕ UI-КОМПОНЕНТЫ

Создай в `src/components/ui/`:

## StatusBadge.jsx
```jsx
// registration → ⬤ Регистрация (серый, пульс)
// group_stage  → ⬤ Групповой этап (синий)
// playoffs     → ⬤ Плей-офф (жёлтый)
// completed    → ⬤ Завершён (зелёный)
```

## ScoreDisplay.jsx
```jsx
// Победитель: белый, font-mono font-bold text-lg
// Проигравший: surface-200, font-mono text-base
// Тай-брейк: надстрочный текст text-xs рядом с 7
// Live матч: пульсирующая зелёная точка слева
<ScoreDisplay
  player1={{ name, sets: [6, 7], supertb: null, isWinner: true }}
  player2={{ name, sets: [3, 5], supertb: null, isWinner: false }}
  live={false}
/>
```

## PlayerCard.jsx
```jsx
// Компактная карточка: имя | Elo | позиция в рейтинге
// Посев: badge [1] [2] court-500
// Hover: ring-1 ring-court-500/50
```

## Button.jsx
```jsx
// Варианты: primary (court-500), secondary (surface-700), danger (red-600)
// Размеры: sm, md, lg
// Состояния: loading (spinner), disabled
// Всегда: rounded-lg, transition-all
```

## ConfirmDialog.jsx
```jsx
// Backdrop затемнение
// Белый/тёмный диалог по центру
// Props: title, message, confirmLabel, onConfirm, onCancel, variant ('danger'|'default')
// Danger: красная кнопка подтверждения
```

## Toast.jsx
```jsx
// Позиция: правый нижний угол
// success: зелёный, error: красный, info: серый
// Автоскрытие через 4 секунды, slide-in снизу
// Очередь: до 3 одновременно
// Использовать Context + useToast() хук
```

---

# ШАГ 13: СТРАНИЦА ТУРНИРА (4 ТАБА)

`src/pages/TournamentPage.jsx`

## Таб 1: «Участники»

```
Шапка: название турнира, дата, клуб, покрытие (цветной badge), статус

Таблица участников:
  # | Имя | Elo | Изм.Elo (последний турнир) | Сезон.очки | Посев

Для ADMIN при status='registration':
  Кнопка «+ Добавить игрока» → поиск autocomplete по существующим игрокам
  Кнопка «✕» напротив каждого участника
  Кнопка «Сформировать группы» (активна при ≥ 4 участниках)
  Confirmation dialog перед формированием групп

После формирования групп: предпросмотр распределения (compact view)
```

## Таб 2: «Группы»

```
CSS Grid: 2 колонки десктоп, 1 мобиль

Каждая группа — карточка (surface-800, rounded-xl):
  Заголовок: «ГРУППА A» (Bebas Neue, 28px, court-500)
  Таблица: Игрок | В | П | Геймы | ±Геймов | Место
  ──────────────────────────────────────────
  Победитель группы: строка с bg-court-900/50, court-500 текст
  Последнее место: surface-700, surface-200 текст

  Список матчей группы под таблицей:
    Каждый матч — строка: Игрок1 vs Игрок2 | Счёт или «–:–»
    Для ADMIN: клик → открыть MatchResultModal

Прогресс-бар вверху:
  «Сыграно X из Y матчей» + зелёная полоска прогресса
  Анимация заполнения при обновлении
```

## Таб 3: «Сетка» — ГЛАВНЫЙ КОМПОНЕНТ

`src/components/BracketView.jsx`

```
Горизонтальная сетка, слева → направо:
  [1/4 финала] → [Полуфиналы] → [Финал] → [Победитель]

Слот матча (каждый):
  ┌────────────────────────────────┐
  │ [2] Петров С.         6  7(5) │  ← победитель (белый, жирный)
  │ [1] Иванов Д.         3  6(3) │  ← проигравший (surface-200)
  └────────────────────────────────┘
  Ширина 200px, граница: border surface-600
  Победитель: bg-court-900/30 на своей строке

Пустой слот (TBD): «— TBD —» surface-400, курсив
BYE слот: «— BYE —» surface-400

SVG-коннекторы между раундами:
  Линия выходит из центра правого края слота →
  горизонтально до середины промежутка →
  вертикально до уровня следующего матча →
  горизонтально до левого края следующего слота
  Цвет: surface-600 (нейтральная) | court-500 (победная ветка)
  Реализация: <svg position:absolute overflow:visible><path d="M...">

Анимация загрузки:
  Раунды появляются слева → направо с animation-delay
  style={{ animationDelay: `${roundIndex * 150}ms` }}

Live-матч: пульсирующая зелёная точка (animate-pulse-green) в углу слота

Утешительная сетка:
  Секция ниже с заголовком «УТЕШИТЕЛЬНАЯ СЕТКА» (Bebas Neue, surface-200)
  Та же структура, 80% масштаб

Адаптивность:
  Десктоп (lg+): полная горизонтальная сетка
  Планшет (md): горизонтальный скролл
  Мобиль (sm): вертикальные коллапсируемые секции по раундам

ADMIN: клик на любой матч → MatchResultModal
```

## Таб 4: «Итоги» (только при status='completed')

```
Пьедестал:
  Серебро (2-е) | ЗОЛОТО (1-е, выше) | Бронза (3-е)
  Имя: Bebas Neue 48px/32px/28px
  Очки и ΔElo под именем

Таблица итогов с анимацией появления строк (stagger, 50ms задержка):
  Место | Игрок | Очки | ΔElo | Победы | Поражения
  Места 1–3: иконка медали (SVG)
  ΔElo: +N зелёный со стрелкой ↑ | −N красный ↓

Кнопка «Поделиться»: копирует текстовое резюме в буфер
```

---

# ШАГ 14: ПАНЕЛЬ АДМИНИСТРАТОРА

`src/pages/AdminPanel.jsx` — `/tournaments/:id/admin`

```
Заголовок: «УПРАВЛЕНИЕ — [Название турнира]» + кнопка «← К просмотру»

Секция «Статус турнира»:
  Текущий статус (большой badge)
  Кнопки следующего действия (зависят от статуса):
    registration → «Сформировать группы» (если ≥ 4 участников)
    group_stage  → «Перейти к плей-офф» (если все группы завершены)
    playoffs     → «Завершить турнир» (если все матчи сыграны)
  Все кнопки требуют ConfirmDialog

Секция «Ввод результатов»:
  Показывать только незавершённые матчи ТЕКУЩЕГО этапа
  Для каждого матча — MatchCard с инлайн-формой:

  ┌──────────────────────────────────────────────┐
  │ [2] Петров С.    [6] : [3]    [1] Иванов Д. │
  │ Тай-брейк:       [──] : [──]                 │  ← скрыт пока не нужен
  │            [Сохранить]  [Walkover ↓]         │
  └──────────────────────────────────────────────┘

  Числовые input (type="number", min=0, max=7 для гемов, max=30 для тай-брейка)
  При set1=6:6 → автопоказать строку тай-брейка с анимацией
  Живая валидация: подсвечивать некорректный счёт красным
  Победитель определяется и показывается зелёным до нажатия «Сохранить»
  После сохранения: карточка уходит вправо с fade-out

Секция «Правка групп» (только group_stage + 0 матчей сыграно):
  Для каждой группы: select для каждого участника (обмен между группами)
  Кнопка «Сохранить изменения»

Секция «Опасная зона» (красный фон):
  «Исправить результат матча» — выбор матча + форма переввода
  «Пересгенерировать группы» — только если 0 матчей сыграно
  Все действия с ConfirmDialog
```

### MatchResultModal.jsx (для клика по матчу в сетке/группах)

```jsx
// Fullscreen overlay на мобиле, центрированный modal на десктопе
// Та же форма что в AdminPanel
// Props: match, onSuccess, onClose
// onSuccess → обновить данные турнира через invalidateQueries
```

---

# ШАГ 15: СТРАНИЦА РЕЙТИНГА

`src/pages/RatingPage.jsx`

```
Два таба: «Сезонный рейтинг» | «Рейтинг мастерства»

Таб «Сезонный»:
  Select сезона (из существующих сезонов БД)
  Поиск по имени (live-фильтр)
  Таблица:
    # | Игрок | Очков | Турниров | Лучшее место
  Строки 1–3: золотой/серебряный/бронзовый highlight
  Анимация: строки появляются с stagger

Таб «Мастерство (Elo)»:
  Таблица:
    # | Игрок | Elo | ΔElo (посл.турнир) | Матчей | Победы% | Тренд
  Тренд: 5 последних изменений Elo → мини-спарклайн из CSS-полосок
    Каждая полоска — div с высотой пропорциональной |ΔElo|
    Зелёная если +, красная если -
  ΔElo: «+23 ↑» зелёный | «−15 ↓» красный

Общее: горизонтальный скролл таблицы на мобиле
```

---

# ШАГ 16: ПРОФИЛЬ ИГРОКА

`src/pages/PlayerPage.jsx`

```
Шапка профиля:
  Имя (Bebas Neue, 64px) + «#N в рейтинге мастерства»
  Карточки-метрики в ряд:
    Elo рейтинг | Сезонных очков | Всего матчей | Win rate %

История ТВД-турниров (таблица):
  Дата | Турнир | Место | Очки | Elo до → после | ΔElo
  ΔElo: цветная стрелка ↑↓

SVG-график Elo (без внешних библиотек):
  viewBox динамический, padding 40px
  X-ось: турниры в хронологии (даты/подписи)
  Y-ось: Elo (авто-масштаб min-40..max+40)
  Линия: <polyline> court-500, strokeWidth=2
  Точки: <circle r=4> court-500 с hover tooltip
  Tooltip: <title> или кастомный div с position:absolute
  Пунктирная горизонтальная линия на 1200 (стартовый Elo), surface-400
  Анимация: SVG stroke-dashoffset для «рисования» линии при загрузке

Статистика против других игроков (топ-5 соперников):
  Соперник | В | П | Win rate
```

---

# ШАГ 17: ГЛАВНАЯ СТРАНИЦА

`src/pages/HomePage.jsx`

```
Hero:
  «[НАЗВАНИЕ ЛИГИ]» — Bebas Neue, 96px, letter-spacing: 0.05em
  Подзаголовок: «Любительский теннисный турнир · Сезон [год]»
  Две кнопки: «Все турниры» | «Рейтинг»

Активные ТВД-турниры:
  Grid 3 колонки (1 на мобиле)
  У live (group_stage/playoffs): «● LIVE» badge с animate-pulse-green
  Если нет активных: «Нет активных турниров. Следите за анонсами.»

Последний завершённый турнир:
  Мини-пьедестал (компактный): 2-е | 1-е | 3-е
  Дата, название, ссылка «Полные результаты →»

Топ-5 мастерства:
  Компактная таблица: # | Имя | Elo
  Ссылка «Полный рейтинг →»
```

---

# ШАГ 18: STATE MANAGEMENT И API-СЛОЙ

## api/tvd.js

```javascript
import axios from 'axios'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || 'http://localhost:3000',
  timeout: 10000,
})

// Interceptor: показывать toast при ошибке
api.interceptors.response.use(
  r => r.data,
  error => {
    const msg = error.response?.data?.message || 'Ошибка сервера'
    window.dispatchEvent(new CustomEvent('toast', { detail: { msg, type: 'error' } }))
    return Promise.reject(error)
  }
)

export const tvdApi = {
  tournaments: {
    list:             (p) => api.get('/api/tvd/tournaments', { params: p }),
    get:              (id) => api.get(`/api/tvd/tournaments/${id}`),
    create:           (d) => api.post('/api/tvd/tournaments', d),
    generateGroups:   (id) => api.post(`/api/tvd/tournaments/${id}/generate-groups`),
    generatePlayoffs: (id) => api.post(`/api/tvd/tournaments/${id}/generate-playoffs`),
    finalize:         (id) => api.post(`/api/tvd/tournaments/${id}/finalize`),
    getBracket:       (id) => api.get(`/api/tvd/tournaments/${id}/bracket`),
    getConsolation:   (id) => api.get(`/api/tvd/tournaments/${id}/consolation`),
    getResults:       (id) => api.get(`/api/tvd/tournaments/${id}/results`),
    addParticipant:   (id, d) => api.post(`/api/tvd/tournaments/${id}/participants`, d),
    removeParticipant:(id, pid) => api.delete(`/api/tvd/tournaments/${id}/participants/${pid}`),
    swapPlayers:      (id, d) => api.post(`/api/tvd/tournaments/${id}/groups/swap`, d),
  },
  matches: {
    submitResult: (id, d) => api.post(`/api/tvd/matches/${id}/result`, d),
    updateResult: (id, d) => api.patch(`/api/tvd/matches/${id}/result`, d),
    walkover:     (id, d) => api.post(`/api/tvd/matches/${id}/walkover`, d),
  },
  rating: {
    season: (seasonId) => api.get(`/api/tvd/rating/season/${seasonId}`),
    elo:    ()         => api.get('/api/tvd/rating/elo'),
  },
  players: {
    list:    ()   => api.get('/api/tvd/players'),
    get:     (id) => api.get(`/api/tvd/players/${id}`),
    history: (id) => api.get(`/api/tvd/players/${id}/history`),
  }
}
```

## Кастомные хуки (src/hooks/)

```javascript
// useTournament(id)       — данные турнира, статус, участники
// useGroups(tournamentId) — группы + матчи + standings, polling 30s при active
// useBracket(tournamentId)— основная и утешительная сетки
// useRating(type)         — сезонный или Elo рейтинг
// usePlayerHistory(id)    — история турниров игрока
// useAdmin()              — мутации для действий администратора
// useToast()              — показать toast уведомление
```

---

# ШАГ 19: ТЕСТЫ

## Бэкенд (server/tests/)

```javascript
// groupFormation.test.js
✓ 9 → 3 группы по 3
✓ 12 → 4 группы по 3
✓ 10 → 3×3 + 1×1 (выбор: 4 или 2)
✓ 8 → 2×3 + 1×2
✓ Сеяные: по одному в каждой группе
✓ Змейка: убывание рейтинга равномерно по группам

// groupStandings.test.js
✓ Простой случай (разные победы)
✓ Тай-брейк по games_diff
✓ Тай-брейк по личной встрече
✓ Три игрока с одинаковыми wins и games_diff

// eloRating.test.js
✓ expectedScore: делитель 800, не 400
✓ actualScore: 0.7*gameShare + 0.3*matchResult
✓ K=250 при totalMatchesPlayed < 10
✓ K=50 при totalMatchesPlayed >= 10
✓ Рейтинг не ниже 800
✓ Walkover: gameShare корректен при totalGames=0
✓ Симметричность: EA + EB = 1 (приблизительно)

// playoffBracket.test.js
✓ 4 группы → 8 игроков основной, 4 утешиловки
✓ 3 группы → 6 игроков, 2 BYE
✓ Игроки одной группы не в 1/4 вместе
✓ BYE получают лучшие по wins→games_diff
```

## Фронтенд (client/src/__tests__/)

```javascript
// BracketView.test.jsx
✓ Рендер 4-группового турнира (8 слотов 1/4)
✓ BYE слоты отображаются корректно
✓ TBD для незаполненных матчей
✓ SVG-коннекторы: правильное количество

// MatchResultForm.test.jsx
✓ Показ тай-брейка при 6:6
✓ Валидация: 8:5 → ошибка
✓ Валидация: суперматч-тай-брейк 9:7 → ошибка
✓ Победитель определяется до submit

// EloChart.test.jsx
✓ Правильное количество точек на графике
✓ Линия 1200 отображается
```

---

# ШАГ 20: ДОКУМЕНТАЦИЯ И ЗАВЕРШЕНИЕ

## docs/SCHEMA_MAP.md (создан в Шаге 0)
## docs/TVD_API.md — документация API с примерами curl

```bash
# Полный цикл турнира:

# 1. Создать турнир
curl -X POST /api/tvd/tournaments \
  -H "Content-Type: application/json" \
  -d '{"name":"Rookie 2026 #9","season_id":1,"club":"ПСК Высота","surface":"clay","match_format":"one_set_supertb","scheduled_at":"2026-02-28T19:00:00Z"}'

# 2. Зарегистрировать игроков
curl -X POST /api/tvd/tournaments/1/participants \
  -d '{"player_id": 42}'

# 3. Сформировать группы
curl -X POST /api/tvd/tournaments/1/generate-groups

# 4. Ввести результат матча
curl -X POST /api/tvd/matches/15/result \
  -d '{"set1_score_p1":6,"set1_score_p2":3}'

# 5. Перейти к плей-офф
curl -X POST /api/tvd/tournaments/1/generate-playoffs

# 6. Завершить турнир
curl -X POST /api/tvd/tournaments/1/finalize
```

## README.md (обновить существующий или создать раздел)

Добавить раздел «ТВД-турниры» с:
- Описанием формата
- Объяснением формулы Elo (делитель 800, гибридный SA)
- Инструкцией запуска
- Переменными окружения

---

# ТЕХНИЧЕСКИЙ СТЕК

| Слой | Технология |
|---|---|
| БД | PostgreSQL (существующая) |
| Query | node-postgres (pg), параметризованные запросы, транзакции |
| Бэкенд | Node.js 20+, Express 4 |
| Тесты | Jest (бэкенд), Vitest + RTL (фронтенд) |
| Фронтенд | React 18, Vite 5 (или существующий стек) |
| Стили | Tailwind CSS + кастомные CSS animations |
| Роутинг | React Router v6 |
| HTTP | Axios |
| Шрифты | Bebas Neue + DM Sans + JetBrains Mono |
| Графики | Кастомный SVG (без Chart.js) |
| UI-библиотеки | ТОЛЬКО собственные компоненты |
| Иконки | SVG инлайн |

---

# ПОРЯДОК ВЫПОЛНЕНИЯ

```
1. Шаг 0:  Разведка схемы → SCHEMA_MAP.md
2. Шаг 1:  Миграции (только safe changes)
3. Шаги 2–7: Бэкенд-сервисы
4. Шаг 8:  REST API роуты
5. Шаг 9:  Бизнес-правила
6. Шаги 10–11: Настройка фронтенда
7. Шаги 12–17: Компоненты и страницы
8. Шаг 18: API-слой и хуки
9. Шаг 19: Тесты
10. Шаг 20: Документация
```

**После каждого шага:** запустить существующие тесты проекта и убедиться что ничего не сломано.
