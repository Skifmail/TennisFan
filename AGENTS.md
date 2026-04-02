# Tennison — Project Context

## Stack
- Python 3.12, aiogram, loguru, uv

## Architecture
- app/domain/ — business logic
- app/services/ — orchestration
- app/core/ — config, logging

## Rules
- All docstrings in Russian
- No print(), use loguru
- Strict typing everywhere
- For tests, always use one of:
  - `venv/bin/python manage.py test`
  - `source venv/bin/activate && pytest`
