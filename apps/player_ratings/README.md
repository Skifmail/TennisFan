# Оценка соперников (player_ratings)

После подтверждения результата матча участникам предлагается один раз анонимно оценить соперника по 12 метрикам (1–10). Агрегация с байесовской корректировкой.

## API

- **POST** `/ratings/matches/<match_id>/rate/` — отправить/обновить оценку (JSON, авторизация). Редактирование в течение 24 ч.
- **GET** `/ratings/matches/<match_id>/rate/` — страница формы опроса (HTML) или статус (Accept: application/json).
- **GET** `/ratings/players/<player_id>/skills/` — агрегированные навыки игрока (публично).

### Пример ответа GET /ratings/players/{id}/skills/

```json
{
  "success": true,
  "skills": {
    "metrics": [
      {
        "name": "serve",
        "label": "Подача",
        "average_raw": 7.5,
        "average_weighted": 7.2,
        "votes_count": 12,
        "display_value": 7.2,
        "insufficient_data": false,
        "stars_filled": 4
      }
    ],
    "recommend_to_improve": [
      {
        "name": "punctuality",
        "label": "Пунктуальность",
        "average_weighted": 6.1,
        "votes_count": 10
      }
    ]
  }
}
```

При `votes_count < 10` у метрики: `display_value: null`, `insufficient_data: true`, в интерфейсе — «Недостаточно данных».

### Пример тела POST /ratings/matches/{id}/rate/

```json
{
  "serve": 8,
  "accuracy": 7,
  "tactics": null,
  "speed": 6,
  "endurance": 7,
  "consistency": 6,
  "net_play": 5,
  "pressure_play": 7,
  "fighting_spirit": 8,
  "communication": 9,
  "punctuality": 10,
  "fairness": 9
}
```

Любое поле можно опустить или передать `null`.
