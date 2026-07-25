"""
Сервис отправки email-уведомлений.

Модуль содержит функции для отправки писем пользователям: приветственное письмо
при регистрации, уведомления и др.
"""

from __future__ import annotations

import logging
import secrets
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags

if TYPE_CHECKING:
    from django.http import HttpRequest

    from apps.courts.models import CourtApplication
    from apps.tournaments.models import Tournament
    from apps.training.models import CoachApplication
    from apps.users.models import User

logger = logging.getLogger(__name__)


def _get_site_base_url() -> str:
    """Получить базовый URL сайта для ссылок в письмах.

    Args:
        None: Функция не принимает аргументов.

    Returns:
        str: Базовый URL сайта (например, ``https://tennisfan.ru``).
    """
    base_url = getattr(settings, "TELEGRAM_BOT_SITE_BASE_URL", "").strip()
    if not base_url:
        domain = getattr(settings, "SITE_DOMAIN", "tennisfan.ru")
        base_url = f"https://{domain}"
    return base_url.rstrip("/")


def _resolve_user_email(user: User) -> str:
    """Вернуть валидный email пользователя в нормализованном виде.

    Args:
        user (User): Пользователь, для которого определяем адрес.

    Returns:
        str: Email-адрес или пустая строка, если email невалиден.
    """
    email = (getattr(user, "email", "") or "").strip().lower()
    if not email or "@" not in email:
        return ""
    return email


def _send_html_email(
    *,
    subject: str,
    template_name: str,
    context: dict[str, Any],
    recipient: str,
    category: str = "",
) -> bool:
    """Отправить HTML-письмо с текстовой fallback-версией.

    Args:
        subject (str): Тема письма.
        template_name (str): Путь к HTML-шаблону.
        context (dict[str, Any]): Контекст шаблона.
        recipient (str): Email получателя.
        category (str): Раздел письма для журнала ``OutboundEmail``.

    Returns:
        bool: ``True`` при успешной отправке, иначе ``False``.
    """
    if not recipient or "@" not in recipient:
        return False
    try:
        html_content = render_to_string(template_name, context)
        text_content = strip_tags(html_content)
    except Exception as exc:
        logger.exception("_send_html_email: template rendering failed: %s", exc)
        return False
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@tennisfan.ru")
    try:
        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_content,
            from_email=from_email,
            to=[recipient],
        )
        msg.attach_alternative(html_content, "text/html")
        if category:
            msg.outbound_category = category
        msg.send(fail_silently=False)
        return True
    except Exception as exc:
        logger.exception("_send_html_email: send failed to %s: %s", recipient, exc)
        return False


def send_welcome_email(user: User) -> bool:
    """Отправить приветственное письмо новому пользователю после регистрации.

    Args:
        user (User): Объект пользователя, которому отправляется письмо.

    Returns:
        bool: ``True``, если письмо отправлено успешно, ``False`` в противном случае.
    """
    email = (getattr(user, "email", "") or "").strip()
    if not email or "@" not in email:
        logger.warning("send_welcome_email: user %s has no valid email", user.pk)
        return False

    base_url = _get_site_base_url()

    player = getattr(user, "player", None)
    profile_url = ""
    if player:
        try:
            profile_url = base_url + reverse("profile", kwargs={"pk": player.pk})
        except Exception:
            profile_url = base_url + "/profile/"

    context = {
        "user": user,
        "user_name": user.get_full_name() or user.email.split("@")[0],
        "base_url": base_url,
        "profile_url": profile_url,
        "pricing_url": base_url + reverse("pricing"),
        "tournaments_url": base_url + reverse("tournament_list"),
        "telegram_bot_url": getattr(settings, "TELEGRAM_PUBLIC_COMMUNITY_URL", "")
        or "https://t.me/TennisFanu",
        "support_email": getattr(settings, "DEFAULT_FROM_EMAIL", "tennis@tennisfan.ru"),
    }

    subject = "Добро пожаловать в TennisFan!"

    try:
        html_content = render_to_string("emails/welcome.html", context)
        text_content = strip_tags(html_content)
    except Exception as exc:
        logger.exception("send_welcome_email: template rendering failed: %s", exc)
        return False

    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@tennisfan.ru")

    try:
        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_content,
            from_email=from_email,
            to=[email],
        )
        msg.attach_alternative(html_content, "text/html")
        msg.outbound_category = "registration"
        msg.send(fail_silently=False)
        logger.info("send_welcome_email: sent to %s (user %s)", email, user.pk)
        return True
    except Exception as exc:
        logger.exception("send_welcome_email: failed to send to %s: %s", email, exc)
        return False


def send_subscription_pre_debit_notification(
    user: User,
    *,
    amount_rub: str,
    tier_name: str,
    end_date_str: str,
    profile_url: str,
) -> bool:
    """Уведомление за 24ч до автосписания (требование ФЗ 376-ФЗ).

    Args:
        user: Пользователь.
        amount_rub: Сумма списания в рублях.
        tier_name: Название тарифа.
        end_date_str: Дата окончания подписки.
        profile_url: Ссылка на профиль для отключения автосписания.

    Returns:
        True, если письмо отправлено успешно.
    """
    email = (getattr(user, "email", "") or "").strip()
    if not email or "@" not in email:
        logger.warning(
            "send_subscription_pre_debit_notification: user %s has no valid email",
            user.pk,
        )
        return False
    subject = "TennisFan: завтра автосписание подписки"

    body = (
        f"Здравствуйте!\n\n"
        f"Ваша подписка «{tier_name}» истекает {end_date_str}.\n\n"
        f"Завтра с привязанной карты будет автоматически списано {amount_rub} ₽ "
        f"для продления подписки.\n\n"
        f"Чтобы отключить автосписание и отвязать карту, перейдите в профиль:\n"
        f"{profile_url}\n\n"
        f"С уважением,\nКоманда TennisFan"
    )

    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@tennisfan.ru")

    try:
        msg = EmailMultiAlternatives(
            subject=subject,
            body=body,
            from_email=from_email,
            to=[email],
        )
        msg.outbound_category = "subscription"
        msg.send(fail_silently=False)
        logger.info(
            "send_subscription_pre_debit_notification: sent to %s (user %s)",
            email,
            user.pk,
        )
        return True
    except Exception as exc:
        logger.exception(
            "send_subscription_pre_debit_notification: failed to send to %s: %s",
            email,
            exc,
        )
        return False


def send_tournament_cancelled_email(
    user: User,
    tournament: Tournament,
    refunded_ft: int,
    reason: str,
) -> bool:
    """Отправить письмо игроку об отмене турнира и возврате FT.

    Args:
        user (User): Получатель письма.
        tournament (Tournament): Отменённый турнир.
        refunded_ft (int): Количество возвращённых FT.
        reason (str): Причина отмены.

    Returns:
        bool: ``True``, если письмо успешно отправлено.
    """
    email = _resolve_user_email(user)
    if not email:
        return False
    base_url = _get_site_base_url()
    return _send_html_email(
        subject=f"TennisFan: турнир «{tournament.name}» отменён",
        template_name="emails/tournament_cancelled.html",
        context={
            "user": user,
            "tournament": tournament,
            "refunded_ft": refunded_ft,
            "reason": reason,
            "tournament_url": f"{base_url}{reverse('tournament_detail', args=[tournament.slug])}",
            "base_url": base_url,
        },
        recipient=email,
        category="tournament",
    )


def send_tournament_participant_withdrawn_email(
    user: User,
    tournament: Tournament,
    *,
    match_lines: list[str],
    refunded_ft: int,
    has_entry_refund: bool,
) -> bool:
    """Отправить письмо снятому участнику о закрытии матчей «Без игры».

    Args:
        user (User): Снятый участник.
        tournament (Tournament): Турнир.
        match_lines (list[str]): Строки с описанием закрытых матчей.
        refunded_ft (int): Сколько FT возвращено (0 если не возвращали).
        has_entry_refund (bool): Есть ли заявка на возврат денежного взноса.

    Returns:
        bool: ``True``, если письмо отправлено.
    """
    email = _resolve_user_email(user)
    if not email:
        return False
    base_url = _get_site_base_url()
    return _send_html_email(
        subject=f"TennisFan: вы сняты с турнира «{tournament.name}»",
        template_name="emails/tournament_participant_withdrawn.html",
        context={
            "user": user,
            "tournament": tournament,
            "match_lines": match_lines,
            "refunded_ft": refunded_ft,
            "has_entry_refund": has_entry_refund,
            "tournament_url": f"{base_url}{reverse('tournament_detail', args=[tournament.slug])}",
            "base_url": base_url,
        },
        recipient=email,
        category="tournament",
    )


def send_tournament_opponent_match_closed_email(
    user: User,
    tournament: Tournament,
    *,
    withdrawn_label: str,
    match_line: str,
    match_url: str,
) -> bool:
    """Отправить письмо сопернику о закрытии матча из-за снятия участника.

    Args:
        user (User): Соперник снятого участника.
        tournament (Tournament): Турнир.
        withdrawn_label (str): Имя снятого игрока/команды.
        match_line (str): Описание матча.
        match_url (str): Абсолютная ссылка на матч.

    Returns:
        bool: ``True``, если письмо отправлено.
    """
    email = _resolve_user_email(user)
    if not email:
        return False
    base_url = _get_site_base_url()
    return _send_html_email(
        subject=f"TennisFan: матч в турнире «{tournament.name}» закрыт без игры",
        template_name="emails/tournament_opponent_match_closed.html",
        context={
            "user": user,
            "tournament": tournament,
            "withdrawn_label": withdrawn_label,
            "match_line": match_line,
            "match_url": match_url,
            "tournament_url": f"{base_url}{reverse('tournament_detail', args=[tournament.slug])}",
            "base_url": base_url,
        },
        recipient=email,
        category="tournament",
    )


def send_tournament_postpayment_opened_email(
    user: User,
    tournament: Tournament,
    *,
    amount: str,
    due_at: str,
    payment_url: str,
) -> bool:
    """Отправить письмо об открытии окна постоплаты со ссылкой на оплату.

    Args:
        user (User): Получатель.
        tournament (Tournament): Турнир.
        amount (str): Сумма взноса.
        due_at (str): Срок оплаты (локальное время, строка).
        payment_url (str): Абсолютная ссылка на оплату.

    Returns:
        bool: ``True``, если письмо отправлено.
    """
    email = _resolve_user_email(user)
    if not email:
        return False
    return _send_html_email(
        subject=f"TennisFan: оплатите участие в турнире «{tournament.name}»",
        template_name="emails/tournament_postpayment_opened.html",
        context={
            "user": user,
            "tournament": tournament,
            "amount": amount,
            "due_at": due_at,
            "payment_url": payment_url,
            "base_url": _get_site_base_url(),
        },
        recipient=email,
        category="tournament",
    )


def send_tournament_postpayment_1h_reminder_email(
    user: User,
    tournament: Tournament,
    *,
    due_at: str,
    payment_url: str,
) -> bool:
    """Отправить письмо-напоминание за 1 час до конца постоплаты.

    Args:
        user (User): Получатель.
        tournament (Tournament): Турнир.
        due_at (str): Срок оплаты (локальное время, строка).
        payment_url (str): Абсолютная ссылка на оплату.

    Returns:
        bool: ``True``, если письмо отправлено.
    """
    email = _resolve_user_email(user)
    if not email:
        return False
    return _send_html_email(
        subject=f"TennisFan: остался 1 час на оплату «{tournament.name}»",
        template_name="emails/tournament_postpayment_1h_reminder.html",
        context={
            "user": user,
            "tournament": tournament,
            "due_at": due_at,
            "payment_url": payment_url,
            "base_url": _get_site_base_url(),
        },
        recipient=email,
        category="tournament",
    )


def send_tournament_postpayment_resend_email(
    user: User,
    tournament: Tournament,
    *,
    amount: str,
    due_at: str,
    payment_url: str,
) -> bool:
    """Повторно отправить письмо со ссылкой на оплату постоплаты.

    Args:
        user (User): Получатель.
        tournament (Tournament): Турнир.
        amount (str): Сумма взноса.
        due_at (str): Срок оплаты (локальное время, строка).
        payment_url (str): Абсолютная ссылка на оплату.

    Returns:
        bool: ``True``, если письмо отправлено.
    """
    email = _resolve_user_email(user)
    if not email:
        return False
    return _send_html_email(
        subject=f"TennisFan: напоминание об оплате «{tournament.name}»",
        template_name="emails/tournament_postpayment_reminder.html",
        context={
            "user": user,
            "tournament": tournament,
            "amount": amount,
            "due_at": due_at,
            "payment_url": payment_url,
            "base_url": _get_site_base_url(),
        },
        recipient=email,
        category="tournament",
    )


def send_tournament_entry_fancoin_confirmed_email(
    user: User,
    tournament: Tournament,
    *,
    fancoin_spent: int,
    fancoin_balance: int,
    had_payment_request: bool,
) -> bool:
    """Отправить письмо о подтверждении участия за счёт FT.

    Args:
        user (User): Получатель письма.
        tournament (Tournament): Турнир.
        fancoin_spent (int): Количество списанных FT.
        fancoin_balance (int): Остаток FT после списания.
        had_payment_request (bool): Был ли ранее запрос оплаты в рублях.

    Returns:
        bool: ``True``, если письмо отправлено успешно.
    """
    email = _resolve_user_email(user)
    if not email:
        return False
    base_url = _get_site_base_url()
    if had_payment_request:
        intro_text = (
            "Ранее мы просили оплатить вступительный взнос в рублях, "
            "но на вашем балансе достаточно FT — участие подтверждено автоматически."
        )
    else:
        intro_text = (
            "Ваше участие в турнире подтверждено: вступительный взнос "
            "покрыт с баланса подписки."
        )
    return _send_html_email(
        subject=f"TennisFan: участие в турнире «{tournament.name}» подтверждено",
        template_name="emails/tournament_entry_fancoin_confirmed.html",
        context={
            "user": user,
            "tournament": tournament,
            "fancoin_spent": fancoin_spent,
            "fancoin_balance": fancoin_balance,
            "intro_text": intro_text,
            "confirmed_at": timezone.now(),
            "tournament_url": f"{base_url}{reverse('tournament_detail', args=[tournament.slug])}",
            "base_url": base_url,
        },
        recipient=email,
        category="tournament",
    )


def send_tournament_entry_receipt_email(
    user: User,
    tournament: Tournament,
    amount: str | None,
    is_postpayment: bool,
) -> bool:
    """Отправить чек-подтверждение об оплате турнирного взноса.

    Args:
        user (User): Получатель письма.
        tournament (Tournament): Турнир, за который внесён взнос.
        amount (str | None): Сумма платежа в строковом виде.
        is_postpayment (bool): Флаг постоплаты после открытия окна доплат.

    Returns:
        bool: ``True``, если письмо отправлено успешно.
    """
    email = _resolve_user_email(user)
    if not email:
        return False
    base_url = _get_site_base_url()
    return _send_html_email(
        subject=f"TennisFan: оплата турнирного взноса «{tournament.name}»",
        template_name="emails/tournament_entry_receipt.html",
        context={
            "user": user,
            "tournament": tournament,
            "amount": amount or "не указана",
            "is_postpayment": is_postpayment,
            "paid_at": timezone.now(),
            "tournament_url": f"{base_url}{reverse('tournament_detail', args=[tournament.slug])}",
        },
        recipient=email,
        category="tournament",
    )


def send_club_plan_receipt_email(
    user: User,
    *,
    club_name: str,
    plan_name: str,
    amount: str | None,
    auto_renew: bool,
) -> bool:
    """Отправить подтверждение оплаты клубного тарифа.

    Args:
        user (User): Получатель письма.
        club_name (str): Название клуба.
        plan_name (str): Название тарифа клуба.
        amount (str | None): Сумма оплаты.
        auto_renew (bool): Включено ли автопродление.

    Returns:
        bool: ``True``, если письмо отправлено.
    """
    email = _resolve_user_email(user)
    if not email:
        return False
    base_url = _get_site_base_url()
    return _send_html_email(
        subject=f"TennisFan: оплата клубного тарифа «{plan_name}»",
        template_name="emails/club_plan_receipt.html",
        context={
            "user": user,
            "club_name": club_name,
            "plan_name": plan_name,
            "amount": amount or "не указана",
            "auto_renew": auto_renew,
            "paid_at": timezone.now(),
            "profile_url": (
                f"{base_url}{reverse('profile', kwargs={'pk': user.player.pk})}"
                if hasattr(user, "player")
                else base_url
            ),
        },
        recipient=email,
        category="subscription",
    )


def send_coach_application_decision_email(
    application: CoachApplication,
    *,
    approved: bool,
) -> bool:
    """Отправить письмо о результате рассмотрения заявки тренера.

    Args:
        application (CoachApplication): Заявка тренера.
        approved (bool): Итог рассмотрения заявки.

    Returns:
        bool: ``True``, если письмо отправлено.
    """
    recipient = (
        application.applicant_email or ""
    ).strip().lower() or _resolve_user_email(application.applicant_user)
    if not recipient:
        return False
    return _send_html_email(
        subject="TennisFan: решение по заявке тренера",
        template_name="emails/coach_application_decision.html",
        context={
            "application": application,
            "approved": approved,
        },
        recipient=recipient,
        category="other",
    )


def send_court_application_decision_email(
    application: CourtApplication,
    *,
    approved: bool,
) -> bool:
    """Отправить письмо о результате рассмотрения заявки на корт.

    Args:
        application (CourtApplication): Заявка на корт.
        approved (bool): Признак одобрения заявки.

    Returns:
        bool: ``True``, если письмо отправлено.
    """
    recipient = (application.applicant_email or "").strip().lower()
    if not recipient or "@" not in recipient:
        return False
    return _send_html_email(
        subject="TennisFan: решение по заявке на добавление корта",
        template_name="emails/court_application_decision.html",
        context={
            "application": application,
            "approved": approved,
        },
        recipient=recipient,
        category="other",
    )


def send_donation_thanks_email(user: User, amount: str | None) -> bool:
    """Отправить письмо-благодарность за донат авторизованному пользователю.

    Args:
        user (User): Получатель письма.
        amount (str | None): Сумма доната.

    Returns:
        bool: ``True``, если письмо отправлено.
    """
    email = _resolve_user_email(user)
    if not email:
        return False
    return _send_html_email(
        subject="Спасибо за поддержку TennisFan",
        template_name="emails/donation_thanks.html",
        context={
            "user": user,
            "amount": amount or "не указана",
            "paid_at": timezone.now(),
        },
        recipient=email,
        category="subscription",
    )


def send_subscription_autorenew_failed_email(
    user: User,
    tier_name: str,
    reason: str,
) -> bool:
    """Отправить уведомление о неуспешном автосписании подписки.

    Args:
        user (User): Получатель письма.
        tier_name (str): Название тарифа.
        reason (str): Причина ошибки списания.

    Returns:
        bool: ``True``, если письмо отправлено.
    """
    email = _resolve_user_email(user)
    if not email:
        return False
    base_url = _get_site_base_url()
    profile_url = base_url
    if hasattr(user, "player"):
        profile_url = f"{base_url}{reverse('profile', kwargs={'pk': user.player.pk})}"
    return _send_html_email(
        subject="TennisFan: автопродление подписки не выполнено",
        template_name="emails/subscription_autorenew_failed.html",
        context={
            "user": user,
            "tier_name": tier_name,
            "reason": reason,
            "profile_url": profile_url,
        },
        recipient=email,
        category="subscription",
    )


def send_password_changed_email(user: User, request: HttpRequest | None = None) -> bool:
    """Отправить security-письмо о смене пароля.

    Args:
        user (User): Пользователь, у которого изменён пароль.
        request (HttpRequest | None): HTTP-запрос для извлечения IP-адреса.

    Returns:
        bool: ``True``, если письмо отправлено.
    """
    email = _resolve_user_email(user)
    if not email:
        return False
    ip_address = ""
    if request is not None:
        ip_address = (
            str(request.META.get("HTTP_X_FORWARDED_FOR", "")).split(",")[0].strip()
            or str(request.META.get("REMOTE_ADDR", "")).strip()
        )
    return _send_html_email(
        subject="TennisFan: пароль аккаунта изменён",
        template_name="emails/security_password_changed.html",
        context={"user": user, "event_at": timezone.now(), "ip_address": ip_address},
        recipient=email,
        category="security",
    )


def send_phone_changed_email(user: User, old_phone: str, new_phone: str) -> bool:
    """Отправить security-письмо о смене телефона в профиле.

    Args:
        user (User): Пользователь, изменивший телефон.
        old_phone (str): Предыдущее значение телефона.
        new_phone (str): Новое значение телефона.

    Returns:
        bool: ``True``, если письмо отправлено.
    """
    email = _resolve_user_email(user)
    if not email:
        return False
    return _send_html_email(
        subject="TennisFan: номер телефона в профиле изменён",
        template_name="emails/security_phone_changed.html",
        context={
            "user": user,
            "old_phone": old_phone or "не указан",
            "new_phone": new_phone or "не указан",
            "event_at": timezone.now(),
        },
        recipient=email,
        category="security",
    )


def send_email_verification(user: User) -> bool:
    """Создать токен и отправить письмо подтверждения email.

    Args:
        user (User): Пользователь, которому нужно подтвердить email.

    Returns:
        bool: ``True``, если письмо отправлено.
    """
    from apps.users.models import EmailVerificationToken

    email = _resolve_user_email(user)
    if not email:
        return False
    if getattr(user, "email_verified", False):
        return True
    token = secrets.token_urlsafe(32)
    expires_at = timezone.now() + timedelta(days=7)
    EmailVerificationToken.objects.filter(user=user, used_at__isnull=True).update(
        used_at=timezone.now()
    )
    EmailVerificationToken.objects.create(
        user=user,
        token=token,
        expires_at=expires_at,
    )
    base_url = _get_site_base_url()
    verify_url = f"{base_url}{reverse('verify_email_confirm', kwargs={'token': token})}"
    return _send_html_email(
        subject="TennisFan: подтвердите ваш email",
        template_name="emails/email_verification.html",
        context={"user": user, "verify_url": verify_url, "expires_at": expires_at},
        recipient=email,
        category="registration",
    )


def send_new_tournament_email(user: User, tournament: Tournament) -> bool:
    """Отправить branded-письмо о старте нового турнира.

    Args:
        user (User): Получатель письма.
        tournament (Tournament): Созданный турнир.

    Returns:
        bool: ``True``, если письмо отправлено.
    """
    email = _resolve_user_email(user)
    if not email:
        return False
    base_url = _get_site_base_url()
    tournament_url = f"{base_url}{reverse('tournament_detail', args=[tournament.slug])}"
    user_name = user.get_display_name() or user.email.split("@")[0]
    start_str = (
        tournament.start_date.strftime("%d.%m.%Y")
        if getattr(tournament, "start_date", None)
        else ""
    )
    city = getattr(tournament, "city", "") or ""
    logo_url = f"{base_url}/static/images/logo.png"
    support_email = getattr(settings, "DEFAULT_FROM_EMAIL", "tennis@tennisfan.ru")
    from apps.users.skill_levels import skill_with_ntrp

    category_codes = list(
        tournament.allowed_categories.values_list("category", flat=True)
    )
    allowed_categories = [skill_with_ntrp(code) for code in category_codes]
    return _send_html_email(
        subject=f"TennisFan: новый турнир «{tournament.name}»",
        template_name="emails/new_tournament.html",
        context={
            "user": user,
            "user_name": user_name,
            "tournament": tournament,
            "tournament_name": tournament.name,
            "tournament_url": tournament_url,
            "start_date": start_str,
            "city": city,
            "allowed_categories": allowed_categories,
            "base_url": base_url,
            "logo_url": logo_url,
            "support_email": support_email,
        },
        recipient=email,
        category="new_tournament",
    )
