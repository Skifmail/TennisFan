"""Единая доставка событий кемпа участнику и команде."""

from __future__ import annotations

from django.conf import settings
from django.urls import reverse

from apps.notifications.infrastructure.email import send_event_mail as send_mail
from apps.notifications.services import center_enabled, create_notification, preferences
from apps.users.models import User

from .models import CampApplication


def team_recipients(app: CampApplication) -> list[User]:
    """Выбирает координатора либо действующих суперадминистраторов."""
    if app.session.coordinator_id:
        return list(User.objects.filter(pk=app.session.coordinator_id, is_active=True))
    return list(User.objects.filter(is_active=True, is_superuser=True))


def send_camp_event(
    *, app: CampApplication, event_key: str, message: str, to_team: bool = False
) -> bool:
    """Ставит зарегистрированным адресатам события, гостю отправляет письмо с учётом политики."""
    path = reverse(
        "camps:manage_application" if to_team else "camps:application", args=[app.pk]
    )
    recipients = (
        team_recipients(app)
        if to_team
        else ([app.user] if app.user is not None else [])
    )
    for recipient in recipients:
        create_notification(
            event_key=event_key, user=recipient, message=message, url=path
        )
        if not center_enabled() and recipient.email and preferences(recipient.pk)[1]:
            send_mail(
                "TennisFan · кемпы",
                message,
                settings.DEFAULT_FROM_EMAIL,
                [recipient.email],
                event_key=event_key,
                fail_silently=False,
            )
    if not to_team and app.user_id is None:
        recovery_url = str(settings.SITE_URL).rstrip("/") + reverse("camps:recover")
        if event_key == "camp.departure_reminder":
            from django.utils.html import format_html

            from apps.notifications.infrastructure.email import EventEmail

            from .attendance import attendance_token

            url = str(settings.SITE_URL).rstrip("/") + reverse(
                "camps:attendance_guest", args=[attendance_token(app)]
            )
            label = (
                "Поездка подтверждена"
                if app.attendance_confirmed
                else "Подтвердить поездку"
            )
            email = EventEmail(
                subject="TennisFan · скоро кемп",
                body=message
                + f"\n\n{label}: {url}\nСсылка действует по день начала кемпа включительно, но не более 8 дней.\nКоординатор: {app.session.contact_email} {app.session.contact_phone}",
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[app.email],
                event_key=event_key,
                capture_event=False,
            )
            email.sensitive_body = True
            email.attach_alternative(
                format_html(
                    '<div style="font-family:Arial,sans-serif;line-height:1.6"><h2>TennisFan · скоро кемп</h2><p>{}</p><p><a href="{}" style="display:inline-block;padding:14px 22px;background:#164e3e;color:#ffffff;border-radius:10px;text-decoration:none">{}</a></p><p>Регистрация не нужна. На открывшейся странице нажмите кнопку подтверждения. Ссылка действует по день начала кемпа включительно, но не более 8 дней. Никому её не пересылайте.</p><p>Координатор: {} {}</p></div>',
                    message,
                    url,
                    label,
                    app.session.contact_email,
                    app.session.contact_phone,
                ),
                "text/html",
            )
            return bool(email.send(fail_silently=False))
        return bool(
            send_mail(
                "TennisFan · кемпы",
                message
                + f"\n\nОткрыть гостевые заявки: {recovery_url}\nКоординатор: {app.session.contact_email} {app.session.contact_phone}",
                settings.DEFAULT_FROM_EMAIL,
                [app.email],
                event_key=event_key,
                fail_silently=False,
            )
        )
    return bool(recipients)


def invalidate_camp_reminders(
    app: CampApplication,
    *,
    payment_only: bool = False,
    team_only: bool = False,
    departure_only: bool = False,
) -> None:
    """Снимает ещё не доставленные напоминания после оплаты, отмены или ответа команды."""
    from django.utils import timezone

    from apps.notifications.models import Delivery
    from apps.users.models import Notification

    keys = (
        ["camp.unanswered_reminder"]
        if team_only
        else (
            ["camp.payment_reminder"]
            if payment_only
            else (
                ["camp.departure_reminder"]
                if departure_only
                else ["camp.payment_reminder", "camp.departure_reminder"]
            )
        )
    )
    path = reverse(
        "camps:manage_application" if team_only else "camps:application", args=[app.pk]
    )
    notes = Notification.objects.filter(event_key__in=keys, url=path)
    Delivery.objects.filter(notification__in=notes, status="queued").update(
        status="skipped",
        error_code="camp_conditions_changed",
        finished_at=timezone.now(),
    )
    notes.filter(is_read=False).update(is_visible=False)
