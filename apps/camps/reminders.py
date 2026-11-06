"""Идемпотентные напоминания о кемпе, доплате и необработанной заявке."""

from __future__ import annotations

import smtplib
from datetime import date, timedelta

from django.db import transaction
from django.utils import timezone
from loguru import logger

from apps.notifications.services import center_enabled

from .communication import send_camp_event
from .models import CampApplication, CampReminder

MAX_ATTEMPTS = 3
LEASE = timedelta(minutes=15)


def _active(app: CampApplication) -> bool:
    """Проверяет актуальность участия перед каждой попыткой доставки."""
    return bool(
        app.status in {"new", "contacted", "agreed", "confirmed", "waitlist"}
        and app.session.status in {"open", "closed"}
        and app.session.end_date >= timezone.localdate()
        and not app.reservation_expired
        and not app.cancellation_requests.filter(status="pending").exists()
    )


def _candidate_kinds(app: CampApplication, today: date) -> list[tuple[str, str]]:
    """Выбирает однократные поводы с ключом исходной даты, а не даты запуска cron."""
    kinds: list[tuple[str, str]] = []
    if app.status == "new" and app.created_at <= timezone.now() - timedelta(hours=24):
        kinds.append(("unanswered", app.created_at.isoformat()))
    if app.status == "confirmed" or app.has_active_reservation:
        days = (app.session.start_date - today).days
        if days in {7, 3, 1}:
            kinds.append((f"departure_{days}", app.session.start_date.isoformat()))
    if (
        app.status in {"agreed", "confirmed"}
        and app.payment_due
        and app.balance is not None
        and app.balance > 0
    ):
        days = (app.payment_due - today).days
        if days in {3, 1, 0}:
            kinds.append((f"payment_{days}", app.payment_due.isoformat()))
        elif days < 0:
            kinds.append(("payment_overdue", app.payment_due.isoformat()))
    return kinds


def _message(app: CampApplication, kind: str) -> tuple[str, str, bool]:
    """Строит сообщение без внутренних заметок и персональных данных детей."""
    if kind == "unanswered":
        return (
            "camp.unanswered_reminder",
            "Заявка на кемп без ответа больше суток. Свяжитесь с участником и обновите статус.",
            True,
        )
    if kind.startswith("departure_"):
        return (
            "camp.departure_reminder",
            f"Кемп начинается {app.session.start_date:%d.%m.%Y}. "
            + (
                "Вы уже подтвердили поездку. Ждём вас! Если планы изменились, свяжитесь с координатором."
                if app.attendance_confirmed
                else "Пожалуйста, подтвердите, что приедете, кнопкой в заявке. Подтверждение не требует оплаты."
            ),
            False,
        )
    return (
        "camp.payment_reminder",
        f"Кемп: доплата {app.balance} {app.currency} до {app.payment_due:%d.%m.%Y}. Уже оплатили? Напишите координатору.",
        False,
    )


def _still_relevant(row: CampReminder, app: CampApplication) -> bool:
    """Отменяет устаревшие поводы после оплаты, переноса даты или ответа команды."""
    if not _active(app):
        return False
    today = timezone.localdate()
    if row.kind == "unanswered":
        return bool(app.status == "new")
    if row.kind.startswith("departure_"):
        days = (app.session.start_date - today).days
        if (row.kind == "departure_7" and days <= 3) or (
            row.kind == "departure_3" and days <= 1
        ):
            return False
        return bool(
            (app.status == "confirmed" or app.has_active_reservation)
            and row.basis == app.session.start_date.isoformat()
            and app.session.start_date >= today
        )
    if app.payment_due:
        days = (app.payment_due - today).days
        if (
            (row.kind == "payment_3" and days <= 1)
            or (row.kind == "payment_1" and days <= 0)
            or (row.kind == "payment_0" and days < 0)
        ):
            return False
    return bool(
        app.status in {"agreed", "confirmed"}
        and app.payment_due
        and row.basis == app.payment_due.isoformat()
        and app.balance is not None
        and app.balance > 0
    )


def _deliver(pk: int) -> bool:
    """Захватывает событие однократно; неизвестный результат SMTP не повторяет вслепую."""
    with transaction.atomic():
        row = CampReminder.objects.select_for_update().get(pk=pk)
        if (
            row.status in {"sent", "skipped", "uncertain"}
            or row.attempts >= MAX_ATTEMPTS
        ):
            return False
        now = timezone.now()
        if row.status == "processing":
            if row.updated_at < now - LEASE:
                row.status = "uncertain"
                row.save(update_fields=["status"])
            return False
        if row.status == "failed" and row.updated_at > now - timedelta(hours=1):
            return False
        app = CampApplication.objects.select_related(
            "session__camp", "session__coordinator", "user"
        ).get(pk=row.application_id)
        if not _still_relevant(row, app):
            row.status = "skipped"
            row.save(update_fields=["status"])
            return False
        row.status, row.updated_at = "processing", now
        row.attempts += 1
        row.save(update_fields=["status", "updated_at", "attempts"])
        event, message, to_team = _message(app, row.kind)
        # Для аккаунтов события ЛК и очередь доставки фиксируются вместе с отметкой.
        if center_enabled() and (app.user_id is not None or to_team):
            sent = send_camp_event(
                app=app, event_key=event, message=message, to_team=to_team
            )
            row.status = "sent" if sent else "skipped"
            row.save(update_fields=["status"])
            return sent
    # Для гостя перед SMTP уже сохранён захват: параллельный cron письмо не повторит.
    try:
        with transaction.atomic():
            sent = send_camp_event(
                app=app, event_key=event, message=message, to_team=to_team
            )
    except (
        smtplib.SMTPResponseException,
        smtplib.SMTPRecipientsRefused,
        ConnectionRefusedError,
    ):
        CampReminder.objects.filter(pk=pk, status="processing").update(
            status="failed", updated_at=timezone.now()
        )
        logger.warning(
            "Почтовый сервер отклонил напоминание кемпа {}; будет повтор", pk
        )
        return False
    except Exception:
        CampReminder.objects.filter(pk=pk, status="processing").update(
            status="uncertain", updated_at=timezone.now()
        )
        logger.warning(
            "Результат отправки напоминания кемпа {} неизвестен; автоматический повтор остановлен",
            pk,
        )
        return False
    CampReminder.objects.filter(pk=pk, status="processing").update(
        status="sent" if sent else "skipped", updated_at=timezone.now()
    )
    return sent


def send_camp_reminders(*, dry_run: bool = False) -> int:
    """Готовит напоминания и повторяет явные ошибки не более трёх раз."""
    today = timezone.localdate()
    apps = CampApplication.objects.filter(
        status__in=["new", "contacted", "agreed", "confirmed", "waitlist"],
        session__status__in=["open", "closed"],
        session__end_date__gte=today,
    ).select_related("session__camp", "session__coordinator", "user")
    ids: set[int] = set()
    candidate_count = 0
    for app in apps.iterator():
        if not _active(app):
            continue
        for kind, basis in _candidate_kinds(app, today):
            candidate_count += 1
            if not dry_run:
                row, _ = CampReminder.objects.get_or_create(
                    application=app, kind=kind, basis=basis
                )
                ids.add(row.pk)
    if dry_run:
        return candidate_count
    ids.update(
        CampReminder.objects.filter(
            status__in=["failed", "pending", "processing"]
        ).values_list("pk", flat=True)
    )
    count = 0
    for pk in sorted(ids):
        try:
            count += int(_deliver(pk))
        except Exception:
            logger.exception("Не удалось подготовить напоминание кемпа {}", pk)
    return count
