"""Общие журналы сохраняют записи независимых процессов после ротации."""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from zipfile import ZipFile

import pytest


@pytest.mark.parametrize("rotation_gap", [False, True])
def test_writer_opened_before_another_process_rotates_keeps_its_records(
    tmp_path: Path,
    rotation_gap: bool,
) -> None:
    """Первый cron ротирует файл, второй продолжает писать без потерь."""
    source = Path(__file__).resolve().parents[2] / "logging_config.py"
    shutil.copyfile(source, tmp_path / source.name)
    child_code = (
        "import sys; import logging_config; "
        "sys.stdout.write('ready\\n'); sys.stdout.flush(); sys.stdin.readline(); "
        "logging_config.logger.error('second-process-marker'); "
        "logging_config.logger.complete(); logging_config.logger.remove()"
    )
    script = rf"""
import subprocess
import sys
from datetime import timedelta
from loguru import logger
import logging_config

child = subprocess.Popen(
    [sys.executable, "-c", {child_code!r}],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
)
assert child.stdout.readline() == "ready\n"
logger.patch(lambda record: record.update(time=record["time"] + timedelta(days=1))).error("first-process-marker")
logger.complete()
if {rotation_gap!r}:
    for level in ("all", "info", "warnings", "errors"):
        path = logging_config.LOGS_DIR / (level + ".log")
        path.rename(path.with_name(level + ".manual.log"))
stdout, stderr = child.communicate("write\n", timeout=20)
assert child.returncode == 0, stderr
assert "Logging error" not in stderr, stderr
logger.remove()
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={**os.environ, "DEBUG": "False", "LOG_TO_CONSOLE": "False"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "Logging error" not in result.stderr
    logs = tmp_path / "logs"
    for level in ("all", "info", "warnings", "errors"):
        entries = (logs / f"{level}.log").read_text()
        for archive in logs.glob(f"{level}.*.log"):
            entries += archive.read_text()
        for archive in logs.glob(f"{level}.*.log.zip"):
            with ZipFile(archive) as zipped:
                entries += "".join(
                    zipped.read(name).decode() for name in zipped.namelist()
                )
        assert entries.count("first-process-marker") == 1
        assert entries.count("second-process-marker") == 1


def test_parallel_first_writes_rotate_each_shared_journal_once(tmp_path: Path) -> None:
    """Шесть cron-процессов одновременно пишут после полуночи без потерь и дублей."""
    source = Path(__file__).resolve().parents[2] / "logging_config.py"
    shutil.copyfile(source, tmp_path / source.name)
    logs = tmp_path / "logs"
    logs.mkdir()
    old = time.time() - 86400
    for level in ("all", "info", "warnings", "errors"):
        path = logs / f"{level}.log"
        path.write_text("previous-day-marker\n")
        os.utime(path, (old, old))
    child_code = r"""
import sys
from loguru import logger
import logging_config
sys.stdout.write("ready\n")
sys.stdout.flush()
sys.stdin.readline()
for index in range(30):
    logger.error("parallel-marker-{}-{}", sys.argv[1], index)
logger.complete()
logger.remove()
"""
    script = rf"""
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
children = [subprocess.Popen(
    [sys.executable, "-c", {child_code!r}, str(index)],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
) for index in range(6)]
for child in children:
    assert child.stdout.readline() == "ready\n"
with ThreadPoolExecutor(max_workers=6) as pool:
    outputs = list(pool.map(lambda child: child.communicate("write\n", timeout=20), children))
for child, (stdout, stderr) in zip(children, outputs):
    assert child.returncode == 0, stderr
    assert "Logging error" not in stderr, stderr
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={**os.environ, "DEBUG": "False", "LOG_TO_CONSOLE": "False"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    for level in ("all", "info", "warnings", "errors"):
        archives = list(logs.glob(f"{level}.*.log.zip"))
        assert len(archives) == 1
        with ZipFile(archives[0]) as archive:
            assert len(archive.namelist()) == 1
            assert archive.read(archive.namelist()[0]) == b"previous-day-marker\n"
        entries = (logs / f"{level}.log").read_text()
        assert entries.count("parallel-marker-") == 180
        for child in range(6):
            for index in range(30):
                assert entries.count(f"parallel-marker-{child}-{index}\n") == 1
