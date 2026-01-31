#!/usr/bin/env python
"""Командная утилита Django для административных задач."""
import os
import sys


def main():
    """Запустить административные задачи."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Не удалось импортировать Django. Убедитесь, что он установлен "
            "и доступен в переменной окружения PYTHONPATH. "
            "Вы не забыли активировать виртуальное окружение?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()

