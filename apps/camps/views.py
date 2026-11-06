"""Публичные страницы кемпов и закрытая работа с заявками."""

from __future__ import annotations

import csv
import uuid
from typing import Any, cast

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Prefetch, Q, QuerySet, Sum
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.users.models import User

from .attendance import confirm_attendance, guest_attendance_application
from .cancellations import decide_cancellation, request_cancellation
from .forms import (
    ApplicationManagementForm,
    CampApplicationForm,
    CancellationDecisionForm,
    CancellationRequestForm,
    GuestRecoveryForm,
    ParticipantFormSet,
    PaymentForm,
    ReservationForm,
)
from .guest_access import consume_guest_link, request_guest_access, valid_guest_link
from .models import (
    Camp,
    CampApplication,
    CampCancellation,
    CampParticipant,
    CampSession,
)
from .reservations import default_reservation_deadline, reserve_application
from .services import (
    can_manage,
    record_payment,
    require_manager,
    submit_application,
    update_application,
)


def camp_list(request: HttpRequest) -> HttpResponse:
    """Показывает собственные программы с актуальными заездами."""
    sessions = (
        CampSession.objects.filter(status__in=["open", "closed"])
        .filter(end_date__gte=timezone.localdate())
        .prefetch_related("packages")
    )
    camps = Camp.objects.filter(is_published=True).prefetch_related(
        Prefetch("sessions", queryset=sessions, to_attr="upcoming_sessions")
    )
    audience = request.GET.get("audience", "")
    if audience in Camp.Audience.values:
        camps = camps.filter(audience=audience)
    return render(
        request,
        "camps/list.html",
        {
            "camps": camps,
            "audiences": Camp.Audience.choices,
            "audience": audience,
            "can_manage_camps": (
                can_manage(cast(User, request.user))
                if request.user.is_authenticated
                else False
            ),
        },
    )


def camp_detail(request: HttpRequest, slug: str) -> HttpResponse:
    """Представляет программу, пакеты и открытые даты без раскрытия участников."""
    camp = get_object_or_404(
        Camp.objects.prefetch_related("coaches"), slug=slug, is_published=True
    )
    sessions = camp.sessions.filter(
        end_date__gte=timezone.localdate(), status__in=["open", "closed"]
    ).prefetch_related("packages", "tournament_links__tournament")
    return render(
        request,
        "camps/detail.html",
        {"camp": camp, "sessions": sessions, "today": timezone.localdate()},
    )


def _submission_key(request: HttpRequest, session_id: int) -> str:
    """Выдаёт ключ повтора, принадлежащий текущей браузерной сессии."""
    keys = request.session.get("camp_submission_keys", {})
    key = keys.get(str(session_id))
    if not key:
        key = str(uuid.uuid4())
        keys[str(session_id)] = key
        request.session["camp_submission_keys"] = dict(list(keys.items())[-20:])
    return str(key)


@never_cache
@require_http_methods(["GET", "POST"])
def apply(request: HttpRequest, session_id: int) -> HttpResponse:
    """Принимает гостевую или авторизованную заявку с проверкой всех участников."""
    session = get_object_or_404(
        CampSession.objects.select_related("camp"),
        pk=session_id,
        camp__is_published=True,
    )
    if session.status == "draft":
        raise PermissionDenied
    key = _submission_key(request, session.pk)
    existing = CampApplication.objects.filter(submission_key=key).first()
    if existing:
        return redirect("camps:application", pk=existing.pk)
    if not session.accepts_applications:
        messages.info(
            request, "Приём заявок на этот заезд закрыт. Посмотрите другие даты."
        )
        return redirect(session.camp.get_absolute_url())
    user = cast(User, request.user) if request.user.is_authenticated else None
    initial: dict[str, Any] = {"submission_key": key}
    if user:
        initial.update(
            contact_name=user.get_full_name(), email=user.email, phone=user.phone
        )
    form = CampApplicationForm(
        request.POST if request.method == "POST" else None,
        session=session,
        initial=initial,
    )
    people = ParticipantFormSet(
        request.POST if request.method == "POST" else None,
        session=session,
        prefix="people",
    )
    if request.method == "POST":
        valid_form, valid_people = form.is_valid(), people.is_valid()
        if valid_form and valid_people:
            if str(form.cleaned_data["submission_key"]) != key:
                form.add_error(
                    None, "Форма устарела. Обновите страницу и попробуйте ещё раз."
                )
            else:
                rate_key = f"camp-application:{request.META.get('REMOTE_ADDR', '')}"
                count = cache.get(rate_key, 0)
                if count >= 8:
                    form.add_error(
                        None,
                        "Слишком много заявок. Попробуйте через час или свяжитесь с координатором.",
                    )
                else:
                    try:
                        application = submit_application(
                            session_id=session.pk,
                            submission_key=form.cleaned_data["submission_key"],
                            contact=form.cleaned_data,
                            participants=[
                                row
                                for row in people.cleaned_data
                                if row and not row.get("DELETE")
                            ],
                            user=user,
                        )
                    except ValidationError as exc:
                        form.add_error(None, exc)
                    else:
                        cache.set(rate_key, count + 1, 3600)
                        owned = request.session.get("camp_application_ids", [])
                        request.session["camp_application_ids"] = (
                            owned + [str(application.pk)]
                        )[-50:]
                        return redirect("camps:application", pk=application.pk)
    return render(
        request,
        "camps/apply.html",
        {"session": session, "camp": session.camp, "form": form, "people": people},
    )


def _accessible_applications(request: HttpRequest) -> QuerySet[CampApplication]:
    """Разрешает доступ владельцу аккаунта либо браузеру, подавшему гостевую заявку."""
    query = Q(pk__in=request.session.get("camp_application_ids", []))
    if request.user.is_authenticated:
        query |= Q(user_id=request.user.pk)
    return CampApplication.objects.filter(query)


@never_cache
def application_detail(request: HttpRequest, pk: uuid.UUID) -> HttpResponse:
    """Показывает только собственную заявку, без внутренних заметок команды."""
    app = get_object_or_404(
        _accessible_applications(request)
        .select_related("session__camp", "package")
        .prefetch_related("participants", "payments"),
        pk=pk,
    )
    return render(
        request,
        "camps/application.html",
        {
            "application": app,
            "cancellations": app.cancellation_requests.all(),
            "can_request_cancellation": app.status
            in {"new", "contacted", "agreed", "confirmed", "waitlist"}
            and app.session.end_date >= timezone.localdate()
            and not app.cancellation_requests.filter(status="pending").exists(),
        },
    )


@never_cache
def my_camps(request: HttpRequest) -> HttpResponse:
    """Заявки аккаунта и подтверждённой гостевой сессии."""
    applications = _accessible_applications(request).select_related(
        "session__camp", "package"
    )
    return render(request, "camps/my.html", {"applications": applications})


def _staff_session(request: HttpRequest, pk: int) -> CampSession:
    """Проверяет полномочия координатора до чтения персональных данных."""
    require_manager(cast(User, request.user))
    return cast(
        CampSession,
        get_object_or_404(CampSession.objects.select_related("camp"), pk=pk),
    )


@login_required
@never_cache
def dashboard(request: HttpRequest) -> HttpResponse:
    """Список заездов для команды TennisFan."""
    require_manager(cast(User, request.user))
    sessions = (
        CampSession.objects.select_related("camp")
        .prefetch_related("applications")
        .order_by("-start_date")
    )
    return render(request, "camps/dashboard.html", {"sessions": sessions})


@login_required
@never_cache
def manage_session(request: HttpRequest, pk: int) -> HttpResponse:
    """Участники, набор и экономика конкретного заезда."""
    session = _staff_session(request, pk)
    applications = session.applications.select_related("package").prefetch_related(
        "participants", "payments"
    )
    pending_cancellations = CampCancellation.objects.filter(
        application__session=session, status="pending"
    ).count()
    if request.GET.get("cancellation") == "pending":
        applications = applications.filter(cancellation_requests__status="pending")
    status = request.GET.get("status", "")
    if status in CampApplication.Status.values:
        applications = applications.filter(status=status)
    totals = list(
        session.applications.values("currency").annotate(quoted=Sum("quoted_total"))
    )
    payments = list(
        session.applications.values("currency").annotate(
            received=Sum("payments__amount")
        )
    )
    expenses = list(session.expenses.values("currency").annotate(total=Sum("amount")))
    games = session.games.select_related("player1", "player2")
    return render(
        request,
        "camps/manage_session.html",
        {
            "session": session,
            "applications": applications,
            "statuses": CampApplication.Status.choices,
            "selected_status": status,
            "pending_cancellations": pending_cancellations,
            "totals": totals,
            "payments": payments,
            "expenses": expenses,
            "games": games,
        },
    )


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def manage_application(request: HttpRequest, pk: uuid.UUID) -> HttpResponse:
    """Подтверждение участия и ручной учёт расчётов с проверкой вместимости."""
    require_manager(cast(User, request.user))
    app = get_object_or_404(
        CampApplication.objects.select_related(
            "session__camp", "package"
        ).prefetch_related("participants", "payments"),
        pk=pk,
    )
    initial = {
        key: getattr(app, key)
        for key in (
            "status",
            "quoted_total",
            "payment_due",
            "accommodation_notes",
            "staff_notes",
        )
    }
    action = request.POST.get("action") if request.method == "POST" else None
    form = ApplicationManagementForm(
        request.POST if action == "update" else None, initial=initial
    )
    reservation_form = ReservationForm(
        request.POST if action == "reserve" else None,
        initial={
            "expires_at": default_reservation_deadline(
                app.session,
                current=(
                    app.reservation_expires_at if app.has_active_reservation else None
                ),
            )
        },
    )
    if action == "reserve" and reservation_form.is_valid():
        try:
            reserve_application(
                application_id=app.pk,
                actor=cast(User, request.user),
                **reservation_form.cleaned_data,
            )
        except ValidationError as exc:
            reservation_form.add_error(None, exc)
        else:
            messages.success(
                request, "Временная бронь сохранена. Предоплата не требуется."
            )
            return redirect("camps:manage_application", pk=pk)
    pending_cancellation = app.cancellation_requests.filter(status="pending").first()
    cancellation_form = CancellationDecisionForm(
        request.POST if action == "cancellation" else None,
        initial={
            "request_id": pending_cancellation.pk if pending_cancellation else None
        },
    )
    if action == "cancellation" and cancellation_form.is_valid():
        try:
            decide_cancellation(
                application_id=app.pk,
                actor=cast(User, request.user),
                **cancellation_form.cleaned_data,
            )
        except (CampCancellation.DoesNotExist, ValidationError):
            cancellation_form.add_error(
                None,
                "Не удалось обработать запрос. Проверьте решение, ответ участнику и актуальность страницы.",
            )
        else:
            messages.success(
                request, "Решение сохранено. Возврат денег учитывается отдельно."
            )
            return redirect("camps:manage_application", pk=pk)
    payment_key_name = f"camp_payment_key_{pk}"
    if payment_key_name not in request.session:
        request.session[payment_key_name] = str(uuid.uuid4())
    payment_form = PaymentForm(
        request.POST if action == "payment" else None,
        initial={"operation_key": request.session[payment_key_name]},
    )
    if action == "update" and form.is_valid():
        try:
            update_application(
                application_id=app.pk,
                actor=cast(User, request.user),
                values=form.cleaned_data,
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Изменения сохранены.")
            return redirect("camps:manage_application", pk=pk)
    if action == "payment" and payment_form.is_valid():
        if (
            str(payment_form.cleaned_data["operation_key"])
            != request.session[payment_key_name]
        ):
            payment_form.add_error(None, "Форма оплаты устарела. Обновите страницу.")
        else:
            try:
                record_payment(
                    application_id=app.pk,
                    actor=cast(User, request.user),
                    **payment_form.cleaned_data,
                )
            except ValidationError as exc:
                payment_form.add_error(None, exc)
            else:
                request.session[payment_key_name] = str(uuid.uuid4())
                messages.success(
                    request, "Запись добавлена в журнал. Деньги с карты не списывались."
                )
                return redirect("camps:manage_application", pk=pk)
    return render(
        request,
        "camps/manage_application.html",
        {
            "application": app,
            "form": form,
            "payment_form": payment_form,
            "cancellations": app.cancellation_requests.all(),
            "pending_cancellation": pending_cancellation,
            "cancellation_form": cancellation_form,
            "reservation_form": reservation_form,
            "can_reserve": app.status in {"new", "contacted", "agreed", "waitlist"}
            and app.session.status in {"open", "closed"}
            and not app.has_pending_cancellation,
            "platform_timezone": timezone.get_current_timezone_name(),
        },
    )


@login_required
@never_cache
def export_session(request: HttpRequest, pk: int) -> HttpResponse:
    """Выгружает служебный CSV без формул из пользовательского ввода."""
    session = _staff_session(request, pk)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="camp-{session.pk}-participants.csv"'
    )
    response.write("\ufeff")
    writer = csv.writer(response, delimiter=";")
    writer.writerow(
        [
            "Участник",
            "Роль",
            "Возраст",
            "Уровень",
            "Группа",
            "Статус",
            "Контакт",
            "Email",
            "Телефон",
        ]
    )
    for person in CampParticipant.objects.filter(
        application__session=session
    ).select_related("application"):
        values = [
            person.name,
            person.get_role_display(),
            person.age or "",
            person.level,
            person.training_group,
            person.application.get_status_display(),
            person.application.contact_name,
            person.application.email,
            person.application.phone,
        ]
        writer.writerow(
            [
                (
                    f"'{value}"
                    if str(value).lstrip().startswith(("=", "+", "-", "@", "\t", "\r"))
                    else value
                )
                for value in values
            ]
        )
    return response


@never_cache
def session_games(request: HttpRequest, pk: int) -> HttpResponse:
    """Открывает журнал игр сотрудникам и подтверждённым участникам заезда."""
    session = get_object_or_404(CampSession.objects.select_related("camp"), pk=pk)
    owns = (
        _accessible_applications(request)
        .filter(session=session, status__in=["confirmed", "completed"])
        .exists()
    )
    is_manager = request.user.is_authenticated and can_manage(cast(User, request.user))
    if not is_manager and not owns:
        raise PermissionDenied
    return render(
        request,
        "camps/games.html",
        {
            "session": session,
            "games": session.games.select_related("player1", "player2"),
            "tournament_links": session.tournament_links.select_related("tournament"),
        },
    )


@never_cache
@require_http_methods(["GET", "POST"])
def recover_guest(request: HttpRequest) -> HttpResponse:
    """Возвращает одинаковое сообщение для существующего и неизвестного email."""
    form = GuestRecoveryForm(request.POST if request.method == "POST" else None)
    sent = False
    if request.method == "POST" and form.is_valid():
        request_guest_access(
            email=form.cleaned_data["email"],
            address=request.META.get("REMOTE_ADDR", ""),
        )
        sent = True
    return render(request, "camps/recover.html", {"form": form, "sent": sent})


@never_cache
@require_http_methods(["GET", "POST"])
def restore_guest(request: HttpRequest, token: str) -> HttpResponse:
    """Подтверждает одноразовую ссылку только POST-запросом с CSRF."""
    valid = valid_guest_link(token)
    if request.method == "POST":
        try:
            ids = consume_guest_link(token)
        except ValidationError:
            valid = False
        else:
            request.session.cycle_key()
            existing = request.session.get("camp_application_ids", [])
            request.session["camp_application_ids"] = list(
                dict.fromkeys(existing + ids)
            )[-150:]
            response: HttpResponse = redirect("camps:my")
            response["Referrer-Policy"] = "no-referrer"
            return response
    response = render(request, "camps/restore.html", {"valid": valid})
    response["Referrer-Policy"] = "no-referrer"
    response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return response


@never_cache
@require_http_methods(["GET", "POST"])
def cancel_application(request: HttpRequest, pk: uuid.UUID) -> HttpResponse:
    """Принимает запрос на отмену только от владельца заявки или подтверждённого гостя."""
    app = get_object_or_404(
        _accessible_applications(request).select_related("session__camp"), pk=pk
    )
    pending = app.cancellation_requests.filter(status="pending").first()
    if pending:
        messages.info(request, "Ваш запрос уже ожидает решения координатора.")
        return redirect("camps:application", pk=pk)
    if (
        app.status not in {"new", "contacted", "agreed", "confirmed", "waitlist"}
        or app.session.end_date < timezone.localdate()
    ):
        messages.info(
            request, "Запрос на отмену недоступен. Свяжитесь с координатором."
        )
        return redirect("camps:application", pk=pk)
    key_name = f"camp_cancel_key_{pk}"
    if key_name not in request.session:
        request.session[key_name] = str(uuid.uuid4())
    form = CancellationRequestForm(
        request.POST if request.method == "POST" else None,
        initial={"submission_key": request.session[key_name]},
    )
    if request.method == "POST" and form.is_valid():
        if str(form.cleaned_data["submission_key"]) != request.session[key_name]:
            form.add_error(None, "Форма устарела. Обновите страницу.")
        else:
            try:
                request_cancellation(
                    application_id=app.pk,
                    actor=(
                        cast(User, request.user)
                        if request.user.is_authenticated
                        else None
                    ),
                    guest_application_ids=request.session.get(
                        "camp_application_ids", []
                    ),
                    submission_key=form.cleaned_data["submission_key"],
                    reason=form.cleaned_data["reason"],
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                request.session.pop(key_name, None)
                messages.success(
                    request, "Запрос отправлен координатору. Решение появится в заявке."
                )
                return redirect("camps:application", pk=pk)
    return render(request, "camps/cancel.html", {"application": app, "form": form})


@never_cache
@require_http_methods(["POST"])
def confirm_trip(request: HttpRequest, pk: uuid.UUID) -> HttpResponse:
    """Принимает ответ владельца авторизованной или восстановленной гостевой заявки."""
    app = get_object_or_404(_accessible_applications(request), pk=pk)
    try:
        confirm_attendance(
            application_id=app.pk,
            actor=cast(User, request.user) if request.user.is_authenticated else None,
            guest_ids=request.session.get("camp_application_ids", []),
        )
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, "Спасибо! Вы подтвердили, что приедете на кемп.")
    return redirect("camps:application", pk=pk)


@never_cache
@require_http_methods(["GET", "POST"])
def confirm_guest_trip(request: HttpRequest, token: str) -> HttpResponse:
    """Даёт ссылке из письма только право подтвердить приезд; GET ничего не меняет."""
    app = guest_attendance_application(token)
    done = bool(app and app.attendance_confirmed)
    if app is not None and request.method == "POST":
        try:
            app = confirm_attendance(application_id=app.pk, token=token)
        except (ValidationError, PermissionDenied):
            app = None
        else:
            done = True
    response = render(
        request,
        "camps/confirm_trip.html",
        {"valid": app is not None, "done": done, "application": app},
        status=200 if app is not None else 410,
    )
    response["Referrer-Policy"] = "no-referrer"
    response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response["Content-Security-Policy"] = (
        "default-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
    )
    return response
