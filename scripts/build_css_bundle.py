"""Собирает единый CSS-бандл из `static/css/style.css` с инлайном `@import`."""

from __future__ import annotations

import re
from pathlib import Path

IMPORT_RE = re.compile(r"@import\s+url\(['\"](?P<path>[^'\"]+)['\"]\);\s*")
URL_RE = re.compile(r"url\((?P<quote>['\"]?)(?P<path>[^)\"']+)(?P=quote)\)")


def _rewrite_css_urls(content: str, imported_css_path: Path, output_path: Path) -> str:
    """Переписывает относительные `url(...)` под директорию итогового бандла.

    Args:
        content: Содержимое импортируемого CSS.
        imported_css_path: Файл, из которого пришёл CSS-контент.
        output_path: Путь к итоговому `style.min.css`.

    Returns:
        str: CSS-контент с обновлёнными относительными путями.
    """

    def _replace(match: re.Match[str]) -> str:
        raw_path = match.group("path").strip()
        quote = match.group("quote") or ""
        if raw_path.startswith(("http://", "https://", "data:", "/", "#")):
            return match.group(0)
        source_dir = imported_css_path.parent.resolve()
        output_dir = output_path.parent.resolve()
        target_path = (source_dir / raw_path).resolve()
        rewritten_path = Path(
            re.sub(r"\\", "/", str(target_path.relative_to(output_dir.parent)))
        )
        # Для файлов в static/fonts/... нужна ссылка ../fonts/... из static/css/style.min.css.
        if rewritten_path.parts and rewritten_path.parts[0] == "static":
            rewritten_path = Path(*rewritten_path.parts[1:])
        relative_from_output = Path("..") / rewritten_path
        return f"url({quote}{relative_from_output.as_posix()}{quote})"

    return URL_RE.sub(_replace, content)


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
        imported_content = imported_css_path.read_text(encoding="utf-8").rstrip()
        imported_content = _rewrite_css_urls(
            content=imported_content,
            imported_css_path=imported_css_path,
            output_path=output_path,
        )
        parts.append(f"/* BEGIN {imported_css_path.name} */")
        parts.append(imported_content)
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
