"""Очистка архивов не удаляет активные журналы при старте."""

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from django.test import SimpleTestCase


class LoggingCleanupTests(SimpleTestCase):
    """Проверка реального запуска обработчиков на старых файлах."""

    def test_startup_preserves_active_logs_and_removes_old_archives(self) -> None:
        """Старые активные логи ротируются без ошибок и потери записи."""
        source = Path(__file__).resolve().parents[2] / "logging_config.py"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copyfile(source, root / "logging_config.py")
            logs = root / "logs"
            logs.mkdir()
            old = time.time() - 9 * 86400
            active = ("all.log", "info.log", "errors.log", "warnings.log")
            archives = (
                "all.2020-01-01_00-00-00_000000.log.zip",
                "info.2020-01-01_00-00-00_000000.log",
            )
            for name in (*active, *archives, "unrelated.log"):
                path = logs / name
                path.write_text("old entry\n")
                os.utime(path, (old, old))
            recent = logs / "all.2026-10-28_00-00-00_000000.log.zip"
            recent.write_text("recent archive")
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import logging_config; "
                    "logging_config.logger.error('cleanup regression marker'); "
                    "logging_config.logger.complete(); logging_config.logger.remove()",
                ],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("Logging error", result.stderr)
            for name in active:
                self.assertIn("cleanup regression marker", (logs / name).read_text())
            for name in archives:
                self.assertFalse((logs / name).exists())
            self.assertTrue(recent.exists())
            self.assertTrue((logs / "unrelated.log").exists())
