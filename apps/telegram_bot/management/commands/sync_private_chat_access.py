"""
Синхронизация доступа пользователей к приватному Telegram-чату.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandParser
from django.db import transaction
from loguru import logger

from apps.core.models import UserTelegramLink
from apps.telegram_bot import services as bot_services
from apps.telegram_bot.binding import lock_user_bot_link
from apps.telegram_bot.private_chat import user_has_private_chat_access


class Command(BaseCommand):
    """
    Удаляет из приватного чата пользователей, у которых больше нет доступа.

    Доступ считается валидным только при действующей подписке и наличии
    флага `has_private_chat` у текущего тарифа.
    """

    help = "Синхронизировать доступ к приватному Telegram-чату по подпискам."
    _REMOVABLE_STATUSES = {"member", "restricted"}

    def add_arguments(self, parser: CommandParser) -> None:
        """Добавить безопасный предварительный просмотр удаления."""
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Только показать, кого нужно удалить, без фактического удаления.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Проверить доступ дважды и не удалять администраторов при любом тарифе."""
        dry_run: bool = options.get("dry_run", False)

        if not bot_services.is_configured():
            self.stdout.write(
                self.style.WARNING("Telegram user bot token не настроен.")
            )
            return
        if not bot_services.is_private_chat_configured():
            self.stdout.write(
                self.style.WARNING("TELEGRAM_PRIVATE_COMMUNITY_CHAT_ID не настроен.")
            )
            return

        links_qs = (
            UserTelegramLink.objects.filter(user_bot_chat_id__gt=0)
            .select_related("user")
            .order_by("pk")
        )

        checked = 0
        to_remove = 0
        removed = 0
        failed = 0

        for link in links_qs.iterator(chunk_size=200):
            checked += 1
            if user_has_private_chat_access(link.user):
                continue

            assert link.user_bot_chat_id is not None
            user_chat_id = link.user_bot_chat_id
            status = bot_services.get_private_chat_member_status(user_chat_id)
            if status not in self._REMOVABLE_STATUSES:
                if status is None:
                    logger.warning(
                        "Закрытый чат: статус не получен, удаление пропущено; user_id={}",
                        link.user_id,
                    )
                continue

            # Во время getChatMember могли продлить подписку или отключить бота.
            with transaction.atomic():
                fresh_link = lock_user_bot_link(
                    user_chat_id, user_id=link.user_id, require_active=False
                )
                if fresh_link is None or user_has_private_chat_access(
                    fresh_link.user, lock=True
                ):
                    continue

                to_remove += 1
                if dry_run:
                    self.stdout.write(
                        "[DRY-RUN] remove chat member: "
                        f"user_id={link.user_id}, chat_id={user_chat_id}, status={status}"
                    )
                    continue

                ok = bot_services.kick_from_private_chat(user_chat_id)
            if ok:
                removed += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Removed from private chat: user_id={link.user_id}, chat_id={user_chat_id}"
                    )
                )
            else:
                failed += 1
                logger.warning(
                    "Закрытый чат: не удалось удалить user_id={}, chat_id={}",
                    link.user_id,
                    user_chat_id,
                )
                self.stdout.write(
                    self.style.WARNING(
                        f"Failed to remove from private chat: user_id={link.user_id}, chat_id={user_chat_id}"
                    )
                )

        self.stdout.write(
            "sync_private_chat_access finished: "
            f"checked={checked}, to_remove={to_remove}, removed={removed}, failed={failed}, dry_run={dry_run}"
        )
