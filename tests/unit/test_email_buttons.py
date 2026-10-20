"""Ссылки-кнопки в письмах должны открываться в новой вкладке."""

import re
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[2]

BUTTON_LINK_RE: Final = re.compile(r"<a\s[^>]*class=\"btn\"[^>]*>", re.IGNORECASE)
TARGET_ATTR_RE: Final = re.compile(r"\btarget=\"_blank\"", re.IGNORECASE)


def test_email_button_links_should_open_in_new_tab() -> None:
    """Кнопки писем не должны теряться в iframe почтовых клиентов и админки."""
    broken: list[str] = []
    for path in sorted((ROOT / "templates" / "emails").rglob("*.html")):
        html = path.read_text(encoding="utf-8")
        for match in BUTTON_LINK_RE.finditer(html):
            if not TARGET_ATTR_RE.search(match.group(0)):
                broken.append(f"{path.relative_to(ROOT)}: {match.group(0)}")
    assert not broken, 'Кнопки без target="_blank":\n' + "\n".join(broken)
