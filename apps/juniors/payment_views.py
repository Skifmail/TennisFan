"""Приватная история оплат и перепроверяемые уведомления платёжного шлюза."""

import json

import requests
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from loguru import logger

from .billing import begin_payment, reconcile_payment, request_refund
from .models import JuniorEntry, JuniorPayment
from .permissions import account
from .views import league_available


@league_available
@never_cache
@login_required
@require_POST
def pay(request: HttpRequest, pk: int) -> HttpResponse:
    entry = get_object_or_404(JuniorEntry, pk=pk)
    try:
        payment = begin_payment(
            actor=account(request),
            entry_id=pk,
            return_url=request.build_absolute_uri(reverse("juniors:payments")),
        )
    except (
        ValidationError,
        ValueError,
        RuntimeError,
        requests.RequestException,
    ) as exc:
        logger.warning(
            "Детская оплата: ошибка создания для заявки {} ({})", pk, type(exc).__name__
        )
        messages.error(
            request,
            "Оплата сейчас недоступна. Проверьте резерв места и повторите позже.",
        )
        return redirect("juniors:child", pk=entry.child_id)
    return redirect(payment.confirmation_url)


@never_cache
@login_required
def payments(request: HttpRequest) -> HttpResponse:
    # История доступна плательщику даже после отключения лиги или прекращения полномочий.
    rows = (
        JuniorPayment.objects.filter(payer_id=request.user.pk)
        .select_related("entry__tournament", "refund")
        .order_by("-created_at")
    )
    return render(request, "juniors/payments.html", {"payments": rows})


@never_cache
@login_required
@require_POST
def payment_action(request: HttpRequest, pk) -> HttpResponse:
    payment = get_object_or_404(JuniorPayment, pk=pk, payer_id=request.user.pk)
    try:
        if request.POST.get("action") == "refund":
            request_refund(
                actor=account(request),
                payment_id=pk,
                reason=request.POST.get("reason", ""),
            )
            messages.success(request, "Запрос возврата передан организатору.")
        else:
            payment = reconcile_payment(payment.pk)
            messages.success(request, f"Статус: {payment.get_status_display()}.")
    except (ValidationError, requests.RequestException) as exc:
        messages.error(
            request,
            (
                " ".join(exc.messages)
                if isinstance(exc, ValidationError)
                else "Шлюз временно недоступен. Повторите позже."
            ),
        )
    return redirect("juniors:payments")


@csrf_exempt
@require_POST
def webhook(request: HttpRequest) -> HttpResponse:
    """Принимает только известный ID и подтверждает результат через серверный API ЮKassa."""
    if len(request.body) > 16384:
        return JsonResponse({"error": "invalid_body"}, status=400)
    try:
        body = json.loads(request.body)
        provider_id = body["object"]["id"]
        if not isinstance(provider_id, str) or len(provider_id) > 64:
            raise ValueError
    except (ValueError, KeyError, TypeError):
        return JsonResponse({"error": "invalid_body"}, status=400)
    payment = JuniorPayment.objects.filter(provider_id=provider_id).first()
    if payment:
        try:
            reconcile_payment(payment.pk)
        except (ValidationError, requests.RequestException):
            return JsonResponse({"error": "retry"}, status=503)
    return JsonResponse({"ok": True})
