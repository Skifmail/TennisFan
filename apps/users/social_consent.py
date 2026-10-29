"""Отдельное согласие перед получением данных от сервиса авторизации."""

from __future__ import annotations

import hashlib
import ipaddress
import secrets
from typing import Any, Final

from django import forms
from django.core import signing
from django.http import HttpRequest
from django.utils import timezone

from apps.core.models import LegalAcceptanceLog
from apps.legal.utils import LEGAL_DOCUMENT_PATHS, LEGAL_VERSION_LENGTH

from .models import SocialAuthFlow, User

DOCUMENTS: Final = ("social-personal-data", "terms", "privacy")
SALT: Final = "social-consent-v1"
SESSION_NONCE: Final = "social_consent_nonce"


class SocialConsentForm(forms.Form):
    """Независимые подтверждения без заранее установленных отметок."""

    token = forms.CharField(widget=forms.HiddenInput, max_length=4096)
    personal_data = forms.BooleanField(
        widget=forms.CheckboxInput(attrs={"class": "form-checkbox"}),
        error_messages={
            "required": "Подтвердите отдельное согласие на обработку данных."
        },
    )
    terms = forms.BooleanField(
        widget=forms.CheckboxInput(attrs={"class": "form-checkbox"}),
        error_messages={
            "required": "Подтвердите принятие пользовательского соглашения."
        },
    )

    def clean(self) -> dict[str, Any]:
        """Принимает только явные отметки из формы, а не произвольные строки."""
        cleaned: dict[str, Any] = super().clean() or {}
        for name in ("personal_data", "terms"):
            if self.data.get(name) != "on" and name not in self.errors:
                self.add_error(name, "Необходимо отдельное подтверждение.")
        return cleaned


def load_documents() -> dict[str, dict[str, str]]:
    """Читает полные тексты и вычисляет версии без подстановки заглушек."""
    documents: dict[str, dict[str, str]] = {}
    for slug in DOCUMENTS:
        text = LEGAL_DOCUMENT_PATHS[slug].read_text(encoding="utf-8")
        if not text.strip():
            raise ValueError("Обязательный документ пуст")
        digest = hashlib.sha256(text.encode()).hexdigest()
        documents[slug] = {
            "document_text": text,
            "document_sha256": digest,
            "version": digest[:LEGAL_VERSION_LENGTH],
        }
    return documents


def consent_context(
    request: HttpRequest,
    *,
    provider: str,
    external_provider: str,
    connect: bool,
    next_url: str,
    documents: dict[str, dict[str, str]],
) -> dict[str, Any]:
    """Связывает предъявленные редакции с браузером, аккаунтом и целью входа."""
    if SESSION_NONCE not in request.session:
        request.session[SESSION_NONCE] = secrets.token_urlsafe(32)
    return {
        "nonce": request.session[SESSION_NONCE],
        "provider": provider,
        "external_provider": external_provider,
        "connect_user_id": request.user.pk if connect else None,
        "next_url": next_url,
        "versions": {slug: item["version"] for slug, item in documents.items()},
    }


def build_form(context: dict[str, Any]) -> SocialConsentForm:
    """Выпускает подписанный контекст формы со сроком действия 30 минут."""
    return SocialConsentForm(initial={"token": signing.dumps(context, salt=SALT)})


def validate_form(form: SocialConsentForm, context: dict[str, Any]) -> bool:
    """Проверяет отдельные отметки, подпись, браузер и актуальность редакций."""
    if not form.is_valid():
        if "token" in form.errors:
            form.add_error(None, "Форма устарела. Подтвердите документы заново.")
        return False
    try:
        if (
            signing.loads(form.cleaned_data["token"], salt=SALT, max_age=1800)
            != context
        ):
            raise signing.BadSignature
    except signing.BadSignature:
        form.add_error(
            None, "Форма устарела. Ознакомьтесь с документами и подтвердите их заново."
        )
        return False
    return True


def acceptance_snapshot(
    request: HttpRequest, documents: dict[str, dict[str, str]], context: dict[str, Any]
) -> dict[str, Any]:
    """Фиксирует доказательства в момент отправки формы до обращения к провайдеру."""
    try:
        ip_address: str | None = str(
            ipaddress.ip_address(request.META.get("REMOTE_ADDR", ""))
        )
    except ValueError:
        ip_address = None
    return {
        "documents": documents,
        "confirmed_at": timezone.now().isoformat(),
        "ip_address": ip_address,
        "user_agent": request.META.get("HTTP_USER_AGENT", "")[:2000],
        "provider": context["provider"],
        "external_provider": context["external_provider"],
        "action": "connect" if context["connect_user_id"] else "login_or_signup",
        "personal_data_checked": True,
        "terms_checked": True,
    }


def record_social_acceptance(flow: SocialAuthFlow, user: User) -> None:
    """Сохраняет исходные редакции после определения владельца внешнего аккаунта."""
    snapshot = flow.legal_acceptance
    for slug in DOCUMENTS:
        document = snapshot["documents"][slug]
        LegalAcceptanceLog.objects.create(
            user=user,
            document_slug=slug,
            document_version=document["version"],
            source="social_auth",
            ip_address=snapshot["ip_address"],
            user_agent=snapshot["user_agent"],
            metadata={
                "subject": {"id": user.pk},
                **{key: value for key, value in snapshot.items() if key != "documents"},
                **document,
                "event": "notice_presented" if slug == "privacy" else "acceptance",
            },
        )
