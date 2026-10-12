"""
Сигналы подписок.
"""

from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.subscriptions.services import ensure_current_subscription


@receiver(post_save, sender=get_user_model())
def assign_free_subscription_on_user_created(
    instance: object,
    created: bool,
    **kwargs: object,
) -> None:
    """Назначить бессрочный тариф Free сразу после создания пользователя.

    Args:
        instance: Сохранённый пользователь.
        created: ``True``, если запись только что создана.
        **kwargs: Дополнительные аргументы сигнала Django.
    """
    if not created:
        return
    ensure_current_subscription(instance)
