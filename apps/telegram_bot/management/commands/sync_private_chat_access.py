"""
Синхронизация доступа пользователей к приватному Telegram-чату.
"""

from __future__ import annotations

import logging

from django.core.management.base import BaseCommand

from apps.core.models import UserTelegramLink
from apps.telegram_bot import services as bot_services
from apps.telegram_bot.private_chat import user_has_private_chat_access

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Удаляет из приватного чата пользователей, у которых больше нет доступа.

    Доступ считается валидным только при действующей подписке и наличии
    флага `has_private_chat` у текущего тарифа.
    """

    help = "Синхронизировать доступ к приватному Telegram-чату по подпискам."
    _ACTIVE_STATUSES = {"member", "administrator", "creator", "restricted"}
    _INACTIVE_STATUSES = {"left", "kicked"}

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Только показать, кого нужно удалить, без фактического удаления.",
        )

    def handle(self, *args, **options) -> None:
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
            UserTelegramLink.objects.filter(user_bot_chat_id__isnull=False)
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

            user_chat_id = int(link.user_bot_chat_id)
            status = bot_services.get_private_chat_member_status(user_chat_id)
            # Если пользователь уже не участник чата, удалять не требуется.
            if status in self._INACTIVE_STATUSES:
                continue
            # Неизвестный статус (None) может означать ошибку getChatMember
            # (например, недостаточно прав бота). В этом случае всё равно
            # пробуем удалить, чтобы не оставлять "висящий" доступ.
            if status is None:
                logger.warning(
                    "sync_private_chat_access: unknown member status for user_id=%s chat_id=%s; "
                    "attempting forced removal",
                    link.user_id,
                    user_chat_id,
                )
            elif status not in self._ACTIVE_STATUSES:
                logger.warning(
                    "sync_private_chat_access: unexpected member status=%s for user_id=%s chat_id=%s; "
                    "skipping",
                    status,
                    link.user_id,
                    user_chat_id,
                )
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
                    "sync_private_chat_access: failed to remove user_id=%s chat_id=%s",
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
