"""
Настройка логирования для production.

Логи разделяются по уровням и хранятся в папке logs/:
- errors.log - только ошибки и критические события
- warnings.log - предупреждения и важные события
- info.log - информационные сообщения
- all.log - все логи (для отладки)

Ротация происходит ежедневно в полночь, старые логи хранятся 7 дней.
"""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from loguru import logger

# Базовая директория проекта
BASE_DIR = Path(__file__).resolve().parent

# Директория для логов
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

# Удаляем стандартный обработчик loguru
logger.remove()

# Формат логов с временными метками и контекстом
LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
    "<level>{message}</level>"
)

# Формат для файлов (без цветов)
FILE_LOG_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
    "{level: <8} | "
    "{name}:{function}:{line} | "
    "{message}"
)

# Уровень логирования из переменной окружения (по умолчанию INFO)
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

# Вывод логов в терминал: при DEBUG или LOG_TO_CONSOLE=True
_LOG_TO_CONSOLE = (
    os.environ.get("DEBUG", "False").lower() == "true"
    or os.environ.get("LOG_TO_CONSOLE", "False").lower() == "true"
)
if _LOG_TO_CONSOLE:
    logger.add(
        sys.stderr,
        format=LOG_FORMAT,
        level=LOG_LEVEL,
        colorize=True,
        backtrace=True,
        diagnose=True,
    )

# Обработчик для всех логов (all.log)
logger.add(
    LOGS_DIR / "all.log",
    format=FILE_LOG_FORMAT,
    level="DEBUG",  # Все уровни
    rotation="00:00",  # Ротация в полночь
    retention="7 days",  # Хранить 7 дней
    compression="zip",  # Сжимать старые логи
    enqueue=True,  # Асинхронная запись
    backtrace=True,
    diagnose=True,
)

# Обработчик для ошибок и критических событий (errors.log)
logger.add(
    LOGS_DIR / "errors.log",
    format=FILE_LOG_FORMAT,
    level="ERROR",  # Только ERROR и CRITICAL
    rotation="00:00",
    retention="7 days",
    compression="zip",
    enqueue=True,
    backtrace=True,
    diagnose=True,
)

# Обработчик для предупреждений и важных событий (warnings.log)
logger.add(
    LOGS_DIR / "warnings.log",
    format=FILE_LOG_FORMAT,
    level="WARNING",  # WARNING, ERROR, CRITICAL
    rotation="00:00",
    retention="7 days",
    compression="zip",
    enqueue=True,
    backtrace=True,
    diagnose=True,
)

# Обработчик для информационных сообщений (info.log)
logger.add(
    LOGS_DIR / "info.log",
    format=FILE_LOG_FORMAT,
    level="INFO",  # INFO, WARNING, ERROR, CRITICAL
    rotation="00:00",
    retention="7 days",
    compression="zip",
    enqueue=True,
    backtrace=True,
    diagnose=True,
)


# Функция для очистки старых логов (вызывается при старте приложения)
def cleanup_old_logs():
    """Удаляет логи старше 7 дней."""
    cutoff_date = datetime.now() - timedelta(days=7)

    for log_file in LOGS_DIR.glob("*.log*"):
        try:
            # Проверяем время модификации файла
            mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
            if mtime < cutoff_date:
                log_file.unlink()
                logger.info(f"Удален старый лог-файл: {log_file.name}")
        except Exception as e:
            logger.error(f"Ошибка при удалении лог-файла {log_file.name}: {e}")


# Вызываем очистку при импорте модуля
cleanup_old_logs()

# Экспортируем настроенный logger
__all__ = ["logger"]
