"""Обмен кодов Яндекс ID и VK ID на проверенную сервером идентичность."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import quote

import requests
from django.conf import settings
from django.http import Http404
from django.utils.crypto import constant_time_compare

from .social_avatar import safe_avatar_url


class SocialAuthError(Exception):
    """Провайдер не подтвердил вход или вернул некорректные данные."""


@dataclass(frozen=True)
class Provider:
    """Параметры OAuth из доверенной серверной конфигурации."""

    key: str
    label: str
    client_id: str
    client_secret: str
    redirect_uri: str
    authorize_url: str
    token_url: str
    scope: str
    optional_scope: str = ""


def get_provider(key: str) -> Provider:
    """Вернуть только включённый и настроенный провайдер."""
    if key not in ("yandex", "vk"):
        raise Http404
    config: dict[str, Any] = settings.SOCIAL_AUTH_PROVIDERS.get(key, {})
    if (
        not config.get("enabled")
        or not config.get("client_id")
        or not config.get("redirect_uri")
    ):
        raise Http404
    if key == "yandex" and not config.get("client_secret"):
        raise Http404
    return Provider(
        key=key,
        label="Яндекс" if key == "yandex" else "VK",
        client_id=str(config["client_id"]),
        client_secret=str(config.get("client_secret", "")),
        redirect_uri=str(config["redirect_uri"]),
        authorize_url=(
            "https://oauth.yandex.ru/authorize"
            if key == "yandex"
            else "https://id.vk.ru/authorize"
        ),
        token_url=(
            "https://oauth.yandex.ru/token"
            if key == "yandex"
            else "https://id.vk.ru/oauth2/auth"
        ),
        scope=(
            "login:info login:email"
            if key == "yandex"
            else "vkid.personal_info email phone"
        ),
        optional_scope=(
            "login:default_phone login:avatar login:birthday" if key == "yandex" else ""
        ),
    )


def _request_json(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
    """Запросить API с тайм-аутом, не раскрывая коды и токены в ошибках."""
    try:
        response = requests.request(
            method, url, timeout=10, allow_redirects=False, **kwargs
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise SocialAuthError from exc
    if not isinstance(data, dict) or data.get("error"):
        raise SocialAuthError
    return data


def fetch_identity(
    provider: Provider,
    *,
    code: str,
    verifier: str,
    state: str,
    device_id: str = "",
    subject_only: bool = False,
) -> dict[str, str]:
    """Обменять одноразовый код с PKCE и запросить профиль по полученному токену."""
    payload = {
        "grant_type": "authorization_code",
        "client_id": provider.client_id,
        "redirect_uri": provider.redirect_uri,
        "code": code,
        "code_verifier": verifier,
    }
    if provider.client_secret:
        payload["client_secret"] = provider.client_secret
    if provider.key == "vk":
        if not device_id:
            raise SocialAuthError
        payload.update(device_id=device_id, state=state)
    token = _request_json("POST", provider.token_url, data=payload)
    access_token = token.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise SocialAuthError
    if provider.key == "yandex":
        profile = _request_json(
            "GET",
            "https://login.yandex.ru/info",
            headers={"Authorization": f"OAuth {access_token}"},
            params={"format": "json"},
        )
        subject = profile.get("id")
        email = profile.get("default_email", "")
    else:
        if not constant_time_compare(str(token.get("state", "")), state):
            raise SocialAuthError
        info = _request_json(
            "POST",
            "https://id.vk.ru/oauth2/user_info",
            data={"client_id": provider.client_id, "access_token": access_token},
        )
        vk_profile = info.get("user")
        if not isinstance(vk_profile, dict):
            raise SocialAuthError
        profile = vk_profile
        subject = profile.get("user_id")
        if str(subject) != str(token.get("user_id")):
            raise SocialAuthError
        email = profile.get("email") or token.get("email", "")
    if (
        isinstance(subject, bool)
        or not isinstance(subject, (str, int))
        or not str(subject)
        or len(str(subject)) > 255
    ):
        raise SocialAuthError
    if subject_only:
        return {"subject": str(subject)}
    phone = profile.get("phone")
    avatar_url = profile.get("avatar") or profile.get("photo_200") or ""
    if provider.key == "yandex":
        default_phone = profile.get("default_phone")
        phone = (
            default_phone.get("number", "") if isinstance(default_phone, dict) else ""
        )
        avatar_id = profile.get("default_avatar_id")
        avatar_url = ""
        if avatar_id and not profile.get("is_avatar_empty", True):
            avatar_url = f"https://avatars.yandex.net/get-yapic/{quote(str(avatar_id), safe='/')}/islands-200"
    gender = str(profile.get("sex") or "").lower()
    if provider.key == "vk":
        gender = {"1": "female", "2": "male"}.get(gender, "")
    return {
        "subject": str(subject),
        "email": str(email or "")[:254],
        "first_name": str(profile.get("first_name") or "")[:150],
        "last_name": str(profile.get("last_name") or "")[:150],
        "phone": str(phone or "")[:20],
        "birth_date": normalize_birth_date(profile.get("birthday")),
        "gender": gender if gender in ("male", "female") else "",
        "avatar_url": safe_avatar_url(provider.key, avatar_url),
    }


def normalize_birth_date(value: object) -> str:
    """Преобразовать полную корректную дату Яндекса/VK в формат поля регистрации."""
    if not isinstance(value, str):
        return ""
    for date_format in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            parsed = datetime.strptime(value, date_format).date()
        except ValueError:
            continue
        if parsed <= datetime.now().date():
            return parsed.isoformat()
    return ""
