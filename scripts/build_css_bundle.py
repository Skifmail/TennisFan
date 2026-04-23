"""Собирает единый CSS-бандл из `static/css/style.css` с инлайном `@import`."""

from __future__ import annotations

import re
from pathlib import Path

IMPORT_RE = re.compile(r"@import\s+url\(['\"](?P<path>[^'\"]+)['\"]\);\s*")


def build_css_bundle(source_path: Path, output_path: Path) -> None:
    """Собирает CSS-файл с раскрытием локальных `@import`.

    Args:
        source_path: Путь к исходному CSS-файлу.
        output_path: Путь, куда будет сохранён собранный бандл.

    Returns:
        None: Файл записывается на диск.
    """
    source_dir = source_path.parent
    source_content = source_path.read_text(encoding="utf-8")
    parts: list[str] = []
    tail: list[str] = []
    for line in source_content.splitlines():
        match = IMPORT_RE.match(line)
        if not match:
            tail.append(line)
            continue
        import_path = match.group("path")
        if not import_path.startswith("./"):
            continue
        imported_css_path = source_dir / import_path.removeprefix("./")
        parts.append(f"/* BEGIN {imported_css_path.name} */")
        parts.append(imported_css_path.read_text(encoding="utf-8").rstrip())
        parts.append(f"/* END {imported_css_path.name} */")
    parts.append("\n".join(tail).rstrip())
    output_path.write_text("\n\n".join(parts).strip() + "\n", encoding="utf-8")


def main() -> None:
    """Точка входа сборки CSS-бандла.

    Returns:
        None.
    """
    root = Path(__file__).resolve().parents[1]
    source = root / "static" / "css" / "style.css"
    output = root / "static" / "css" / "style.min.css"
    build_css_bundle(source_path=source, output_path=output)


if __name__ == "__main__":
    main()
