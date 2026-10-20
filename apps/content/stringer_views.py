"""Подача и просмотр собственных заявок на публикацию стрингера."""

from typing import cast

from django.contrib import messages
from django.db import IntegrityError, transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from apps.users.models import User

from .models import StringerApplication, StringerPage
from .stringer_forms import StringerApplicationForm, StringerPhotoFormSet
from .stringer_services import notify_stringer_application


@require_http_methods(["GET", "POST"])
def stringer_application(request: HttpRequest) -> HttpResponse:
    """Принимает заявку и показывает историю только её владельцу."""
    if not StringerPage.get_singleton().is_enabled:
        raise Http404("Страница отключена")
    applicant = cast(User, request.user) if request.user.is_authenticated else None
    applications = (
        StringerApplication.objects.filter(applicant=applicant).select_related(
            "company"
        )
        if applicant is not None
        else StringerApplication.objects.none()
    )
    pending = applications.filter(status=StringerApplication.Status.PENDING).exists()
    form = StringerApplicationForm(
        request.POST if request.method == "POST" else None,
        initial={"email": applicant.email if applicant is not None else ""},
    )
    photos = StringerPhotoFormSet(
        request.POST if request.method == "POST" else None,
        request.FILES if request.method == "POST" else None,
        prefix="photos",
    )
    if request.method == "POST":
        if pending:
            messages.info(request, "Ваша заявка уже находится на рассмотрении.")
            return redirect("stringer_application")
        form_valid = form.is_valid()
        photos_valid = photos.is_valid()
        if form_valid and photos_valid:
            try:
                with transaction.atomic():
                    application = form.save(commit=False)
                    application.applicant = applicant
                    application.save()
                    photos.instance = application
                    photos.save()
                    notify_stringer_application(application)
            except IntegrityError:
                if (
                    applicant is None
                    or not StringerApplication.objects.filter(
                        applicant=applicant, status=StringerApplication.Status.PENDING
                    ).exists()
                ):
                    raise
                messages.info(request, "Ваша заявка уже находится на рассмотрении.")
            else:
                messages.success(
                    request,
                    "Заявка отправлена. Компания появится на сайте после одобрения администратором.",
                )
            return redirect("stringer_application")
    return render(
        request,
        "content/stringer_application.html",
        {
            "form": form,
            "photos": photos,
            "applications": applications,
            "pending": pending,
        },
    )
