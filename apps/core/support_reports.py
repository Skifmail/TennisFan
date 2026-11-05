"""Создание сообщений с изображениями, классификация ошибок и ответы в общем диалоге."""

from collections.abc import Sequence
from typing import Any, cast
from urllib.parse import urlsplit

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import File
from django.db import transaction
from django.http import FileResponse, Http404, HttpRequest
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_safe

from apps.users.models import User

from .models import BugReport, SupportAttachment, SupportMessage, SupportThread
from .support_service import SupportAuthor, create_admin_reply, create_user_message
from .support_uploads import MAX_ATTACHMENTS, prepare_support_image


def message_details(message: SupportMessage) -> dict[str, Any]:
    """Возвращает сведения об ошибке и ссылки на закрытые вложения без путей хранилища."""
    report = getattr(message, "bug_report", None)
    return {
        "bug_report": (
            {"id": report.pk, "status": report.get_status_display()} if report else None
        ),
        "bug_reply_to": message.bug_reply_to_id,
        "attachments": [
            {"id": item.pk, "url": reverse("support_attachment", args=[item.pk])}
            for item in message.attachments.all()
        ],
    }


def create_support_submission(
    *,
    author: SupportAuthor,
    text: str,
    subject: str = "",
    files: Sequence[File] = (),
    is_bug: bool = False,
    page_url: str = "",
) -> SupportMessage:
    """Сохраняет сообщение и проверенные картинки; некорректный файл не создаёт обращение."""
    if not text.strip() or len(text) > (6000 if is_bug else 2000):
        raise ValidationError(
            "Опишите ошибку подробнее, до 6000 символов."
            if is_bug
            else "Введите сообщение до 2000 символов."
        )
    if len(files) > MAX_ATTACHMENTS:
        raise ValidationError("Можно прикрепить не больше трёх изображений.")
    images = [prepare_support_image(file) for file in files]
    saved: list[tuple[Any, str]] = []
    try:
        with transaction.atomic():
            message = create_user_message(
                author=author,
                text=text.strip(),
                subject="Сообщение об ошибке" if is_bug else subject,
            )
            if is_bug:
                try:
                    path = urlsplit(page_url).path
                except ValueError:
                    path = ""
                BugReport.objects.create(
                    message=message,
                    page_url=(
                        path[:1000]
                        if path.startswith("/") and not path.startswith("//")
                        else ""
                    ),
                )
            for image in images:
                attachment = SupportAttachment(message=message)
                attachment.image.save("screenshot.jpg", image, save=False)
                saved.append((attachment.image.storage, attachment.image.name))
                attachment.save()
            return cast(SupportMessage, message)
    except Exception:
        for storage, name in saved:
            storage.delete(name)
        raise


@transaction.atomic
def reply_to_report(*, actor: User, report_id: int, text: str) -> SupportMessage:
    """Отвечает на конкретную ошибку в том же диалоге и связывает ответ с обращением."""
    if not actor.is_active or not (actor.is_staff or actor.is_superuser):
        raise PermissionDenied
    if not text.strip() or len(text) > 6000:
        raise ValidationError("Введите ответ до 6000 символов.")
    source = BugReport.objects.select_related("message").get(pk=report_id)
    thread = SupportThread.objects.select_for_update().get(pk=source.message.thread_id)
    report = BugReport.objects.select_for_update().get(pk=report_id)
    reply = create_admin_reply(thread=thread, text=text.strip())
    reply.bug_reply_to = report
    reply.save(update_fields=["bug_reply_to"])
    if report.status == BugReport.Status.NEW:
        report.status = BugReport.Status.IN_PROGRESS
        report.save(update_fields=["status", "updated_at"])
    return cast(SupportMessage, reply)


@require_safe
def support_attachment(request: HttpRequest, pk: int) -> FileResponse:
    """Отдаёт изображение только владельцу диалога, его гостевой сессии или поддержке."""
    attachment = get_object_or_404(
        SupportAttachment.objects.select_related("message__thread"), pk=pk
    )
    thread = attachment.message.thread
    if request.user.is_authenticated:
        allowed = request.user.is_active and (
            request.user.is_staff
            or request.user.is_superuser
            or thread.user_id == request.user.pk
        )
    else:
        allowed = (
            thread.user_id is None
            and thread.pk == request.session.get("feedback_thread_id")
            and bool(thread.guest_session_key)
            and thread.guest_session_key == request.session.get("support_guest_token")
        )
    if not allowed:
        raise PermissionDenied
    try:
        response = FileResponse(attachment.image.open("rb"), content_type="image/jpeg")
    except FileNotFoundError as exc:
        raise Http404 from exc
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
