"""Проверки резервного копирования без доступа к боевой базе."""

import os
import subprocess
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[3]


def _run_backup(
    tmp_path: Path, *, fail_dump: bool = False
) -> subprocess.CompletedProcess[str]:
    binary = tmp_path / "docker"
    binary.write_text(
        "#!/bin/bash\n"
        'if [[ "$*" == *pg_restore* ]]; then cat >/dev/null; exit 0; fi\n'
        + ("exit 1\n" if fail_dump else 'printf "PGDMPtest"\n')
    )
    binary.chmod(0o700)
    return subprocess.run(
        ["bash", str(ROOT / "scripts/backup-postgres.sh")],
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "BACKUP_ROOT": str(tmp_path / "backups"),
        },
        capture_output=True,
        text=True,
        check=False,
    )


def test_backup_when_dump_succeeds_should_write_private_verified_archive(
    tmp_path: Path,
) -> None:
    """Публикуется только проверенный архив с правами владельца."""
    assert _run_backup(tmp_path).returncode == 0
    archives = list((tmp_path / "backups").glob("*.dump"))
    assert len(archives) == 1
    assert archives[0].read_bytes() == b"PGDMPtest"
    assert archives[0].stat().st_mode & 0o777 == 0o600


def test_backup_when_dump_fails_should_not_publish_archive(tmp_path: Path) -> None:
    """Частичный дамп не становится пригодной резервной копией."""
    assert _run_backup(tmp_path, fail_dump=True).returncode != 0
    assert not list((tmp_path / "backups").glob("*.dump"))
    assert not list((tmp_path / "backups").glob("*.partial"))


def test_backup_when_repeated_should_preserve_existing_archives(tmp_path: Path) -> None:
    """Повторный запуск не перезаписывает и не удаляет старые копии."""
    assert _run_backup(tmp_path).returncode == 0
    assert _run_backup(tmp_path).returncode == 0
    assert len(list((tmp_path / "backups").glob("*.dump"))) == 2
