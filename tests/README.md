| Каталог | Назначение |
|---------|------------|
| `unit/` | Доменная логика без HTTP: формы, алгоритмы сеток, slug, FANcoin, конфиг Telegram |
| `integration/` | Несколько модулей и БД без HTTP: TVD, постоплата, клубные планы, спарринги, почта |
| `e2e/` | Полный HTTP-цикл через Django test client: страницы, редиректы, шаблоны |
| `support/` | Общие фабрики (`make_user`, `make_player`, `make_club`, …) и хелперы матчей |

## Запуск

```bash
# Все тесты
venv/bin/python manage.py test tests

# По уровню
venv/bin/python manage.py test tests.unit
venv/bin/python manage.py test tests.integration
venv/bin/python manage.py test tests.e2e

# Один модуль
venv/bin/python manage.py test tests.e2e.test_auth_and_profile

# Критичные зоны (платежи, подписки)
venv/bin/python manage.py test tests.integration.test_payments_finalize tests.unit.test_subscription_utils tests.integration.test_subscription_models

venv/bin/python manage.py test tests.unit.test_rating_utils tests.unit.test_player_ratings_validation tests.integration.test_player_ratings tests.unit.test_users_forms tests.integration.test_users_notifications

# Через pytest (нужен DJANGO_SETTINGS_MODULE из pytest.ini)
venv/bin/pytest tests/
```

## Конвенции

- Базовые классы: `django.test.TestCase` (с БД) и `SimpleTestCase` (без БД).
- Имена: `test_<поведение>_<ожидаемый_результат>`.
- Внутри метода — структура **Arrange → Act → Assert** (пустая строка между блоками).
- Docstring на модуле, классе и нетривиальном тесте — на русском, без пустых `Args: None`.
- Повторяющиеся данные — через `tests.support.factories`, завершение матчей — через `tests.support.matches`.

## Линтинг

```bash
venv/bin/black tests/
venv/bin/ruff check tests/ --fix
venv/bin/mypy tests/
```
