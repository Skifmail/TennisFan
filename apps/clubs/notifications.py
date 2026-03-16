"""
Сервис уведомлений клубного раздела: email и Telegram.

Все функции учитывают настройки уведомлений клуба (ClubNotificationConfig)
и индивидуальные настройки участника (ClubNotificationSettings).
Отправка оборачивается в try/except — сбой уведомления не ломает основной флоу.
"""

import logging
from typing import Any, cast

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from apps.telegram_bot.notifications import send_to_user_by_user

from .models import (
    Club,
    ClubMember,
    ClubMemberRole,
    ClubMemberStatus,
    ClubNotificationConfig,
    ClubNotificationSettings,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Внутренние хелперы
# ---------------------------------------------------------------------------


def _get_club_config(club: Club) -> ClubNotificationConfig:
    """Возвращает конфигурацию уведомлений клуба (создаёт по-умолчанию, если нет)."""
    config, _ = ClubNotificationConfig.objects.get_or_create(club=club)
    return cast(ClubNotificationConfig, config)


def _get_member_settings(user: Any, club: Club) -> ClubNotificationSettings:
    """Возвращает индивидуальные настройки уведомлений участника."""
    obj, _ = ClubNotificationSettings.objects.get_or_create(user=user, club=club)
    return cast(ClubNotificationSettings, obj)


def _mask_email(email: str) -> str:
    """Маскирует email для логирования: u***r@example.com."""
    if not email or "@" not in email:
        return "***"
    local, domain = email.rsplit("@", 1)
    if len(local) <= 2:
        masked_local = local[0] + "***"
    else:
        masked_local = local[0] + "***" + local[-1]
    return f"{masked_local}@{domain}"


def _should_send_email(
    config: ClubNotificationConfig, member_settings: ClubNotificationSettings
) -> bool:
    """Определяет, нужно ли отправлять email с учётом всех настроек."""
    return bool(
        config.notify_by_email
        and member_settings.is_enabled
        and member_settings.email_enabled
    )


def _should_send_telegram(
    config: ClubNotificationConfig, member_settings: ClubNotificationSettings
) -> bool:
    """Определяет, нужно ли отправлять Telegram с учётом всех настроек."""
    return bool(
        config.notify_by_telegram
        and member_settings.is_enabled
        and member_settings.telegram_enabled
    )


def _send_club_email(
    subject: str,
    template_name: str,
    context: dict[str, Any],
    recipient_email: str,
) -> bool:
    """
    Отправляет HTML-письмо клубного раздела.

    Args:
        subject: тема письма.
        template_name: путь к шаблону (например 'emails/clubs/fee_reminder.html').
        context: контекст для рендера шаблона.
        recipient_email: email получателя.

    Returns:
        True при успешной отправке.
    """
    try:
        html_body = render_to_string(template_name, context)
        text_body = strip_tags(html_body)
        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[recipient_email],
        )
        msg.attach_alternative(html_body, "text/html")
        msg.send(fail_silently=False)
        logger.info(
            "club_email_sent | to=%s | subject=%s",
            _mask_email(recipient_email),
            subject,
        )
        return True
    except Exception:
        logger.exception(
            "club_email_failed | to=%s | subject=%s",
            _mask_email(recipient_email),
            subject,
        )
        return False


def _send_club_telegram(user: Any, text: str) -> bool:
    """
    Отправляет Telegram-сообщение через платформенного бота.

    Args:
        user: Django User-объект.
        text: HTML-текст сообщения.

    Returns:
        True при успешной отправке.
    """
    try:
        ok = send_to_user_by_user(user, text)
        if ok:
            logger.info(
                "club_telegram_sent | user_id=%s",
                getattr(user, "pk", "?"),
            )
        return ok
    except Exception:
        logger.exception(
            "club_telegram_failed | user_id=%s",
            getattr(user, "pk", "?"),
        )
        return False


def _get_club_admins(club: Club) -> list[ClubMember]:
    """Возвращает активных администраторов клуба."""
    return list(
        club.members.filter(
            role=ClubMemberRole.ADMIN, status=ClubMemberStatus.ACTIVE
        ).select_related("user")
    )


# ---------------------------------------------------------------------------
# Публичные функции уведомлений (Task 3)
# ---------------------------------------------------------------------------


def send_club_invite_email(
    club: Club,
    player_name: str,
    player_email: str,
    accept_url: str,
) -> None:
    """
    Отправляет email-приглашение в клуб.

    Args:
        club: клуб, в который приглашают.
        player_name: имя приглашаемого.
        player_email: email приглашаемого.
        accept_url: абсолютный URL для принятия приглашения.
    """
    try:
        _send_club_email(
            subject=f"Приглашение в клуб «{club.name}» — TennisFan",
            template_name="emails/clubs/club_invite.html",
            context={
                "club_name": club.name,
                "player_name": player_name,
                "accept_url": accept_url,
            },
            recipient_email=player_email,
        )
    except Exception:
        logger.exception("send_club_invite_email failed | club=%s", club.pk)


def send_fee_reminder_notifications(
    club: Club,
    member: ClubMember,
    days_left: int,
    amount: Any,
    period_label: str,
    pay_url: str = "",
) -> None:
    """
    Отправляет напоминание о членском взносе (email + Telegram).

    Args:
        club: клуб.
        member: участник клуба.
        days_left: дней до конца периода.
        amount: сумма взноса.
        period_label: метка периода (2026-03).
        pay_url: ссылка на оплату.
    """
    config = _get_club_config(club)
    if not config.fee_reminders_enabled:
        return

    ms = _get_member_settings(member.user, club)
    player_name = member.user.get_full_name() or member.user.email

    if _should_send_email(config, ms):
        try:
            _send_club_email(
                subject=f"Напоминание: взнос в клуб «{club.name}» (осталось {days_left} дн.)",
                template_name="emails/clubs/fee_reminder.html",
                context={
                    "club_name": club.name,
                    "player_name": player_name,
                    "days_left": days_left,
                    "amount": amount,
                    "period_label": period_label,
                    "pay_url": pay_url,
                },
                recipient_email=member.user.email,
            )
        except Exception:
            logger.exception("send_fee_reminder email failed | member=%s", member.pk)

    if _should_send_telegram(config, ms):
        text = (
            f"💰 <b>Напоминание о взносе</b>\n\n"
            f"Клуб: {club.name}\n"
            f"Сумма: {amount} ₽, период: {period_label}\n"
            f"Осталось {days_left} дн. до конца периода."
        )
        try:
            _send_club_telegram(member.user, text)
        except Exception:
            logger.exception("send_fee_reminder tg failed | member=%s", member.pk)


def send_fee_overdue_notifications(
    club: Club,
    member: ClubMember,
    amount: Any,
    period_label: str,
    restrict_access: bool = False,
    pay_url: str = "",
) -> None:
    """
    Отправляет уведомление о просрочке членского взноса.

    Args:
        club: клуб.
        member: участник клуба.
        amount: сумма взноса.
        period_label: метка периода.
        restrict_access: ограничен ли доступ к турнирам.
        pay_url: ссылка на оплату.
    """
    config = _get_club_config(club)
    if not config.fee_overdue_enabled:
        return

    ms = _get_member_settings(member.user, club)
    player_name = member.user.get_full_name() or member.user.email

    if _should_send_email(config, ms):
        try:
            _send_club_email(
                subject=f"Просрочка взноса в клубе «{club.name}»",
                template_name="emails/clubs/fee_overdue.html",
                context={
                    "club_name": club.name,
                    "player_name": player_name,
                    "amount": amount,
                    "period_label": period_label,
                    "restrict_access": restrict_access,
                    "pay_url": pay_url,
                },
                recipient_email=member.user.email,
            )
        except Exception:
            logger.exception("send_fee_overdue email failed | member=%s", member.pk)

    if _should_send_telegram(config, ms):
        warn = "\n⚠️ Доступ к турнирам ограничен." if restrict_access else ""
        text = (
            f"❗ <b>Просрочка взноса</b>\n\n"
            f"Клуб: {club.name}\n"
            f"Период: {period_label}, сумма: {amount} ₽{warn}"
        )
        try:
            _send_club_telegram(member.user, text)
        except Exception:
            logger.exception("send_fee_overdue tg failed | member=%s", member.pk)


def send_fee_paid_notification(
    club: Club,
    member: ClubMember,
    amount: Any,
    period_label: str,
) -> None:
    """
    Отправляет уведомление «взнос оплачен» игроку.

    Args:
        club: клуб.
        member: участник клуба.
        amount: сумма.
        period_label: метка периода.
    """
    config = _get_club_config(club)
    if not config.fee_paid_enabled:
        return

    ms = _get_member_settings(member.user, club)
    player_name = member.user.get_full_name() or member.user.email

    if _should_send_email(config, ms):
        try:
            _send_club_email(
                subject=f"Взнос в клуб «{club.name}» оплачен",
                template_name="emails/clubs/fee_paid.html",
                context={
                    "club_name": club.name,
                    "player_name": player_name,
                    "amount": amount,
                    "period_label": period_label,
                },
                recipient_email=member.user.email,
            )
        except Exception:
            logger.exception("send_fee_paid email failed | member=%s", member.pk)

    if _should_send_telegram(config, ms):
        text = (
            f"✅ <b>Взнос оплачен</b>\n\n"
            f"Клуб: {club.name}\n"
            f"Период: {period_label}, сумма: {amount} ₽"
        )
        try:
            _send_club_telegram(member.user, text)
        except Exception:
            logger.exception("send_fee_paid tg failed | member=%s", member.pk)


def send_subscription_expiring_notifications(
    club: Club,
    days_left: int,
    plan_name: str,
    ends_at: str,
    renew_url: str = "",
) -> None:
    """
    Отправляет админам клуба уведомление об истечении подписки.

    Args:
        club: клуб.
        days_left: дней до окончания.
        plan_name: название тарифа.
        ends_at: дата окончания (строка).
        renew_url: ссылка на продление.
    """
    config = _get_club_config(club)
    if not config.subscription_expiring_enabled:
        return

    admins = _get_club_admins(club)
    for admin_member in admins:
        admin_name = admin_member.user.get_full_name() or admin_member.user.email
        ms = _get_member_settings(admin_member.user, club)

        if _should_send_email(config, ms):
            try:
                _send_club_email(
                    subject=f"Подписка клуба «{club.name}» истекает через {days_left} дн.",
                    template_name="emails/clubs/subscription_expiring.html",
                    context={
                        "club_name": club.name,
                        "admin_name": admin_name,
                        "plan_name": plan_name,
                        "ends_at": ends_at,
                        "days_left": days_left,
                        "renew_url": renew_url,
                    },
                    recipient_email=admin_member.user.email,
                )
            except Exception:
                logger.exception(
                    "send_subscription_expiring email failed | admin=%s",
                    admin_member.pk,
                )

        if _should_send_telegram(config, ms):
            text = (
                f"⏰ <b>Подписка клуба истекает</b>\n\n"
                f"Клуб: {club.name}\n"
                f"Тариф: {plan_name}\n"
                f"Истекает: {ends_at} (через {days_left} дн.)"
            )
            try:
                _send_club_telegram(admin_member.user, text)
            except Exception:
                logger.exception(
                    "send_subscription_expiring tg failed | admin=%s", admin_member.pk
                )


def send_new_member_notification(
    club: Club,
    new_member: ClubMember,
    dashboard_url: str = "",
) -> None:
    """
    Уведомляет администраторов клуба о новом участнике.

    Args:
        club: клуб.
        new_member: новый участник.
        dashboard_url: ссылка на панель клуба.
    """
    config = _get_club_config(club)
    if not config.new_member_enabled:
        return

    member_name = new_member.user.get_full_name() or new_member.user.email
    member_email = new_member.user.email

    admins = _get_club_admins(club)
    for admin_member in admins:
        admin_name = admin_member.user.get_full_name() or admin_member.user.email
        ms = _get_member_settings(admin_member.user, club)

        if _should_send_email(config, ms):
            try:
                _send_club_email(
                    subject=f"Новый участник в клубе «{club.name}»",
                    template_name="emails/clubs/new_member.html",
                    context={
                        "club_name": club.name,
                        "admin_name": admin_name,
                        "member_name": member_name,
                        "member_email": member_email,
                        "dashboard_url": dashboard_url,
                    },
                    recipient_email=admin_member.user.email,
                )
            except Exception:
                logger.exception(
                    "send_new_member email failed | admin=%s", admin_member.pk
                )

        if _should_send_telegram(config, ms):
            text = (
                f"👤 <b>Новый участник клуба</b>\n\n"
                f"Клуб: {club.name}\n"
                f"Участник: {member_name}"
            )
            try:
                _send_club_telegram(admin_member.user, text)
            except Exception:
                logger.exception(
                    "send_new_member tg failed | admin=%s", admin_member.pk
                )


def send_debtors_summary(
    club: Club,
    period_label: str,
    debtors: list[dict[str, str]],
    fees_url: str = "",
) -> None:
    """
    Отправляет сводку должников администраторам клуба.

    Args:
        club: клуб.
        period_label: метка периода.
        debtors: список словарей {'name': ..., 'email': ...}.
        fees_url: ссылка на раздел оплат.
    """
    config = _get_club_config(club)
    if not config.debtors_summary_enabled:
        return

    admins = _get_club_admins(club)
    for admin_member in admins:
        admin_name = admin_member.user.get_full_name() or admin_member.user.email
        ms = _get_member_settings(admin_member.user, club)

        if _should_send_email(config, ms):
            try:
                _send_club_email(
                    subject=f"Сводка должников — «{club.name}» ({period_label})",
                    template_name="emails/clubs/debtors_summary.html",
                    context={
                        "club_name": club.name,
                        "admin_name": admin_name,
                        "period_label": period_label,
                        "debtors": debtors,
                        "debtors_count": len(debtors),
                        "fees_url": fees_url,
                    },
                    recipient_email=admin_member.user.email,
                )
            except Exception:
                logger.exception(
                    "send_debtors_summary email failed | admin=%s", admin_member.pk
                )

        if _should_send_telegram(config, ms):
            names = ", ".join(d["name"] for d in debtors[:5])
            extra = f" и ещё {len(debtors) - 5}" if len(debtors) > 5 else ""
            text = (
                f"📊 <b>Сводка должников</b>\n\n"
                f"Клуб: {club.name}\n"
                f"Период: {period_label}\n"
                f"Не оплатили: {len(debtors)} чел.\n"
                f"{names}{extra}"
            )
            try:
                _send_club_telegram(admin_member.user, text)
            except Exception:
                logger.exception(
                    "send_debtors_summary tg failed | admin=%s", admin_member.pk
                )


def send_tournament_reminder(
    club: Club,
    member: ClubMember,
    tournament_name: str,
    start_info: str,
    city: str,
    tournament_url: str = "",
) -> None:
    """
    Отправляет напоминание о турнире участнику клуба.

    Args:
        club: клуб.
        member: участник клуба.
        tournament_name: название турнира.
        start_info: строка о начале (напр. «через 24 часа» или «через 1 час»).
        city: город проведения.
        tournament_url: ссылка на турнир.
    """
    config = _get_club_config(club)
    if not config.tournament_reminders_enabled:
        return

    ms = _get_member_settings(member.user, club)
    player_name = member.user.get_full_name() or member.user.email

    if _should_send_email(config, ms):
        try:
            _send_club_email(
                subject=f"Напоминание: турнир «{tournament_name}» {start_info}",
                template_name="emails/clubs/tournament_reminder.html",
                context={
                    "club_name": club.name,
                    "player_name": player_name,
                    "tournament_name": tournament_name,
                    "start_info": start_info,
                    "city": city,
                    "tournament_url": tournament_url,
                },
                recipient_email=member.user.email,
            )
        except Exception:
            logger.exception(
                "send_tournament_reminder email failed | member=%s", member.pk
            )

    if _should_send_telegram(config, ms):
        text = (
            f"🏆 <b>Напоминание о турнире</b>\n\n"
            f"«{tournament_name}» начинается {start_info}\n"
            f"📍 {city}"
        )
        try:
            _send_club_telegram(member.user, text)
        except Exception:
            logger.exception(
                "send_tournament_reminder tg failed | member=%s", member.pk
            )
