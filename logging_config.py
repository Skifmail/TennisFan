"""
Настройка логирования для production.

Логи разделяются по уровням и хранятся в папке logs/:
- errors.log - только ошибки и критические события
- warnings.log - предупреждения и важные события
- info.log - информационные сообщения
- all.log - все логи (для отладки)

Ротация происходит ежедневно в полночь, старые логи хранятся 7 дней.
"""

import fcntl
import os
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from zipfile import ZIP_DEFLATED, ZipFile

from loguru import logger

if TYPE_CHECKING:
    from loguru import Message

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


@contextmanager
def _locked_logs() -> Iterator[None]:
    """Сериализовать запись, ротацию и очистку между web и cron-процессами."""
    # Новый дескриптор на каждую операцию: fork не должен наследовать общий flock.
    with (LOGS_DIR / ".rotation.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _cleanup_archives() -> list[str]:
    """Удалить устаревшие архивы внутри межпроцессной блокировки."""
    cutoff = datetime.now().timestamp() - timedelta(days=7).total_seconds()
    removed: list[str] = []
    for path in LOGS_DIR.glob("*.log*"):
        if not re.fullmatch(
            r"(all|errors|warnings|info)\.\d{4}-\d{2}-\d{2}_"
            r"\d{2}-\d{2}-\d{2}_\d{6}\.log(?:\.zip)?",
            path.name,
        ):
            continue
        if path.stat().st_mtime < cutoff:
            path.unlink()
            removed.append(path.name)
    return removed


class DailyFileSink:
    """Общий ежедневный журнал без открытого дескриптора устаревшего файла."""

    def __init__(self, name: str) -> None:
        """Создать активный журнал, не меняя дату существующего файла."""
        self.path = LOGS_DIR / name
        with _locked_logs(), self.path.open("a", encoding="utf-8"):
            pass

    def write(self, message: "Message") -> None:
        """Атомарно проверить дату, сжать архив и записать сообщение."""
        record_time = message.record["time"]
        midnight = datetime.combine(
            record_time.date(), time.min, tzinfo=record_time.tzinfo
        ).timestamp()
        with _locked_logs():
            try:
                stat = self.path.stat()
            except FileNotFoundError:
                # Восстановить имя после внешней ротации или незавершённого rename.
                stat = None
            if stat is not None and stat.st_size and stat.st_mtime < midnight:
                self._rotate(stat.st_mtime)
                _cleanup_archives()
            # Нельзя держать файл открытым между сообщениями: его мог ротировать
            # другой процесс. Открытие под тем же lock исключает окно rename/write.
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(str(message))

    def _rotate(self, modified: float) -> None:
        """Сохранить непустой файл в ZIP без конфликтов имён и потери исходника."""
        stamp = datetime.fromtimestamp(modified)
        while True:
            archive = self.path.with_name(
                f"{self.path.stem}.{stamp:%Y-%m-%d_%H-%M-%S_%f}.log"
            )
            zipped = archive.with_suffix(".log.zip")
            if not archive.exists() and not zipped.exists():
                break
            stamp += timedelta(microseconds=1)
        # ZIP становится видимым только после успешного закрытия; при ошибке
        # сжатия активный исходник ещё не переименован и остаётся пригодным для записи.
        temporary = zipped.with_suffix(".zip.tmp")
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as output:
            output.write(self.path, arcname=archive.name)
        self.path.rename(archive)
        temporary.rename(zipped)
        archive.unlink()


for _name, _level in (
    ("all.log", "DEBUG"),
    ("errors.log", "ERROR"),
    ("warnings.log", "WARNING"),
    ("info.log", "INFO"),
):
    logger.add(
        DailyFileSink(_name),
        format=FILE_LOG_FORMAT,
        level=_level,
        enqueue=True,
        backtrace=True,
        diagnose=True,
    )


# Функция для очистки старых логов (вызывается при старте приложения)
def cleanup_old_logs() -> None:
    """Удалить старые архивы, не затрагивая открытые файлы обработчиков."""
    try:
        with _locked_logs():
            removed = _cleanup_archives()
    except OSError as exc:
        logger.error("Ошибка при очистке архивов журналов: {}", exc)
        return
    # Не логируем из-под flock: sink очереди использует ту же блокировку.
    for name in removed:
        logger.info("Удален старый лог-файл: {}", name)


# Вызываем очистку при импорте модуля
cleanup_old_logs()

# Экспортируем настроенный logger
__all__ = ["logger"]
