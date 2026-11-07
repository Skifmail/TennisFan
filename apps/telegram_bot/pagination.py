"""Постраничный вывод интерактивных списков в Telegram.

Единый помощник нужен, чтобы длинные списки матчей и турниров не упирались в
лимит сообщения и не обрезались молча. Смещение хранится в callback_data,
поэтому состояние не зависит от кэша процесса.
"""

from __future__ import annotations

from typing import Final

PAGE_SIZE: Final = 5


def normalize_offset(offset: int, total: int, page_size: int = PAGE_SIZE) -> int:
    """Привести смещение к существующей странице.

    Args:
        offset: Запрошенное смещение из callback_data.
        total: Общее количество элементов.
        page_size: Размер страницы.

    Returns:
        Смещение начала существующей страницы; 0 для пустого списка.
    """
    if page_size <= 0 or total <= 0:
        return 0
    if offset <= 0:
        return 0
    last_page_start = ((total - 1) // page_size) * page_size
    return min(offset, last_page_start)


def navigation_rows(
    prefix: str,
    offset: int,
    total: int,
    page_size: int = PAGE_SIZE,
) -> list[list[dict[str, str]]]:
    """Собрать ряды кнопок «Назад» и «Далее».

    Args:
        prefix: Начало callback_data; к нему добавляется числовое смещение.
        offset: Текущее (уже нормализованное) смещение.
        total: Общее количество элементов.
        page_size: Размер страницы.

    Returns:
        Ряды inline-кнопок; пустой список, если страница одна.
    """
    if page_size <= 0 or total <= page_size:
        return []
    rows: list[list[dict[str, str]]] = []
    if offset > 0:
        rows.append(
            [
                {
                    "text": "⬅️ Назад",
                    "callback_data": f"{prefix}{max(0, offset - page_size)}",
                }
            ]
        )
    if offset + page_size < total:
        rows.append(
            [
                {
                    "text": "➡️ Далее",
                    "callback_data": f"{prefix}{offset + page_size}",
                }
            ]
        )
    return rows
