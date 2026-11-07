"""Одноразовая и атомарная привязка аккаунта к личному чату Telegram."""

from __future__ import annotations

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.models import UserTelegramLink
from apps.users.models import User


def lock_user_bot_link(
    chat_id: int,
    *,
    user_id: int | None = None,
    require_active: bool = True,
) -> UserTelegramLink | None:
    """Заблокировать привязку и свежую активность аккаунта до окончания транзакции.

    Порядок всегда одинаковый: привязка, затем пользователь. NO KEY UPDATE
    разрешает создание уведомлений с FK пользователя, но блокирует отключение,
    перепривязку, удаление и изменение активности на время сетевого действия.
    Вызывающий код должен открыть transaction.atomic().
    """
    if isinstance(chat_id, bool) or chat_id <= 0:
        return None
    links = UserTelegramLink.objects.select_for_update(
        of=("self",), no_key=True
    ).filter(user_bot_chat_id=chat_id)
    if user_id is not None:
        links = links.filter(user_id=user_id)
    link: UserTelegramLink | None = links.first()
    if link is None:
        return None
    user = User.objects.select_for_update(no_key=True).get(pk=link.user_id)
    if require_active and not user.is_active:
        return None
    link.user = user
    return link


def consume_binding_token(token: str, chat_id: int) -> tuple[bool, str]:
    """Использовать непросроченный токен, не перехватывая чужую привязку."""
    invalid = (
        "Токен не найден или устарел. Зайдите в личный кабинет на сайте и нажмите "
        "«Подключить Telegram-бот» заново."
    )
    conflict = (
        "Этот Telegram-аккаунт уже связан с другим аккаунтом платформы. "
        "Сначала отключите прежнюю привязку в настройках профиля на сайте."
    )
    if not token or len(token) > 64 or isinstance(chat_id, bool) or chat_id <= 0:
        return False, invalid
    try:
        with transaction.atomic():
            eligible = UserTelegramLink.objects.filter(binding_token=token)
            link = eligible.select_for_update(of=("self",)).first()
            if link is None:
                return False, invalid
            user = User.objects.select_for_update(no_key=True).get(pk=link.user_id)
            now = timezone.now()
            if (
                not user.is_active
                or not link.token_created_at
                or not (
                    timedelta(0)
                    <= now - link.token_created_at
                    < UserTelegramLink.BINDING_TOKEN_LIFETIME
                )
            ):
                return False, invalid
            if (
                UserTelegramLink.objects.filter(user_bot_chat_id=chat_id)
                .exclude(pk=link.pk)
                .exists()
            ):
                return False, conflict
            # Условное обновление сохраняет одноразовость и без блокировок SQLite.
            consumed = eligible.filter(pk=link.pk).update(
                user_bot_chat_id=chat_id,
                binding_token=None,
                token_created_at=None,
                updated_at=now,
            )
            return (True, "") if consumed else (False, invalid)
    except IntegrityError:
        # Уникальный chat_id защищает от параллельной привязки другого аккаунта.
        return False, conflict
