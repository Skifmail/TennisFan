# Зависимости инструментов разработки и CI

## Файлы

- `requirements.txt` — диапазоны боевых зависимостей.
- `requirements.lock` — их точные версии и SHA256; используется Docker-сборкой.
- `requirements-dev.in` — диапазоны инструментов и включение `requirements.lock`.
- `requirements-dev.txt` — сгенерированный полный lock runtime + dev с SHA256.

Dev-lock должен сохранять каждую версию из `requirements.lock`. Установка тестовых
инструментов не должна незаметно обновлять Django или зависимости боевого приложения.
В `uv.lock` нет описания приложения: его наличие не заменяет используемые pip lock-файлы.

## Установка

```bash
python3.12 -m venv venv
venv/bin/python -m pip install --require-hashes -r requirements.lock
venv/bin/python -m pip install --require-hashes -r requirements-dev.txt
venv/bin/python -m pip check
```

`requirements-dev.in` — вход для компиляции, не файл для прямой установки через pip.
Не смешивать внутри устанавливаемого файла зависимости с хешами и диапазоны без хешей.
Даже один `--hash` включает в pip обязательную проверку хешей для всего набора.
Не обходить ошибку через отключение проверки хешей.

## Обновление

После согласованного изменения диапазонов инструментов:

```bash
uv pip compile requirements-dev.in --python-version 3.12 --generate-hashes --no-annotate --output-file requirements-dev.txt
venv/bin/pytest tests/unit/deployment/test_dependency_locks.py
```

Проверить diff и установку в новом окружении Python 3.12. После любого изменения
`requirements.lock` пересоздать dev-lock и снова проверить неизменность runtime-версий.
Не добавлять вручную хеш единственного Black: обязательны хеши всего дерева зависимостей.
Исходные диапазоны и сгенерированный результат хранить в одном изменении репозитория.

## Граница исправления

Это исправляет установку инструментов, а не устраняет уязвимости runtime-пакетов.
`pip check` проверяет совместимость зависимостей, а не их безопасность.
Аудит `pip-audit -r requirements.lock --no-deps --disable-pip` остаётся отдельным
обязательным шагом CI. После согласованной замены Daphne на Uvicorn Twisted
отсутствует в обоих lock-файлах и чистой установке; локальный аудит проходит без исключений.

Источники:
- [pip: обязательные хеши и точные версии](https://pip.pypa.io/en/stable/topics/secure-installs/).
- [uv: компиляция lock-файлов](https://docs.astral.sh/uv/pip/compile/).
