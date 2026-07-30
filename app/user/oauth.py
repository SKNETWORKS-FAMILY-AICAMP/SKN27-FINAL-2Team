"""소셜 로그인(구글/카카오/네이버) OAuth 2.0 흐름.

커스텀 UserAccounts(managed=False) + 커스텀 백엔드 구조라 allauth 대신
수동 구현한다. 외부 HTTP 는 표준 라이브러리 urllib 로 처리한다.
"""

from __future__ import annotations

import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.contrib.auth import login
from django.http import HttpResponseNotFound
from django.shortcuts import redirect
from django.utils import timezone

from .models import UserAccounts


def _provider_endpoints(provider: str) -> dict | None:
    endpoints = {
        "google": {
            "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_url": "https://oauth2.googleapis.com/token",
            "userinfo_url": "https://openidconnect.googleapis.com/v1/userinfo",
            "scope": "openid email profile",
        },
        "kakao": {
            "authorize_url": "https://kauth.kakao.com/oauth/authorize",
            "token_url": "https://kauth.kakao.com/oauth/token",
            "userinfo_url": "https://kapi.kakao.com/v2/user/me",
            "scope": "profile_nickname account_email",
        },
        "naver": {
            "authorize_url": "https://nid.naver.com/oauth2.0/authorize",
            "token_url": "https://nid.naver.com/oauth2.0/token",
            "userinfo_url": "https://openapi.naver.com/v1/nid/me",
            "scope": "",
        },
    }
    return endpoints.get(provider)


def _provider_config(provider: str) -> dict | None:
    endpoints = _provider_endpoints(provider)
    if endpoints is None:
        return None

    config = dict(endpoints)
    config["client_id"] = os.getenv(f"{provider.upper()}_OAUTH_CLIENT_ID", "")
    config["client_secret"] = os.getenv(f"{provider.upper()}_OAUTH_CLIENT_SECRET", "")
    base_url = os.getenv("OAUTH_REDIRECT_BASE_URL", "").rstrip("/")
    config["redirect_uri"] = f"{base_url}/user/oauth/{provider}/callback/"
    return config


def _post_form(url: str, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_json(url: str, access_token: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _extract_profile(provider: str, profile: dict) -> tuple[str, str | None, str | None]:
    """provider 응답에서 (고유ID, 이메일, 닉네임)을 뽑는다."""
    if provider == "google":
        return (
            str(profile.get("sub") or ""),
            (profile.get("email") or "").strip().lower() or None,
            (profile.get("name") or "").strip() or None,
        )
    elif provider == "kakao":
        account = profile.get("kakao_account") or {}
        kakao_profile = account.get("profile") or {}
        return (
            str(profile.get("id") or ""),
            (account.get("email") or "").strip().lower() or None,
            (kakao_profile.get("nickname") or "").strip() or None,
        )
    elif provider == "naver":
        response = profile.get("response") or {}
        return (
            str(response.get("id") or ""),
            (response.get("email") or "").strip().lower() or None,
            (response.get("nickname") or "").strip() or None,
        )

    return "", None, None


def _find_or_create_social_user(
    provider: str,
    provider_id: str,
    email: str | None,
    nickname: str | None,
):
    user = UserAccounts.objects.filter(
        provider=provider,
        provider_id=provider_id,
        deleted_at__isnull=True,
    ).first()
    if user is not None:
        return user

    resolved_email = email
    if not resolved_email or UserAccounts.objects.filter(email=resolved_email).exists():
        # 이메일 미제공(카카오 등)이거나 이미 쓰는 이메일이면 합성 주소를 쓴다.
        resolved_email = f"{provider}_{provider_id}@social.himate"
    resolved_nickname = (nickname or f"{provider}_{provider_id[:8]}")[:30]

    now = timezone.now()
    return UserAccounts.objects.create(
        email=resolved_email,
        password_hash=None,
        nickname=resolved_nickname,
        provider=provider,
        provider_id=provider_id,
        status="active",
        login_fail_count=0,
        is_locked=False,
        locked_at=None,
        created_at=now,
        updated_at=now,
        deleted_at=None,
    )


def oauth_login(request, provider):
    """provider 인가 페이지로 리다이렉트한다. state 로 CSRF 를 막는다."""
    config = _provider_config(provider)
    if config is None:
        return HttpResponseNotFound("지원하지 않는 로그인 방식입니다.")
    if not config["client_id"]:
        return redirect("/user/login/?error=unconfigured")

    state = secrets.token_urlsafe(24)
    request.session["oauth_state"] = state
    params = {
        "response_type": "code",
        "client_id": config["client_id"],
        "redirect_uri": config["redirect_uri"],
        "state": state,
    }
    if config["scope"]:
        params["scope"] = config["scope"]

    return redirect(config["authorize_url"] + "?" + urllib.parse.urlencode(params))


def oauth_callback(request, provider):
    """provider 콜백. 코드 교환 → 프로필 조회 → 계정 찾기/생성 → 로그인."""
    config = _provider_config(provider)
    if config is None:
        return HttpResponseNotFound("지원하지 않는 로그인 방식입니다.")

    if request.GET.get("error"):
        return redirect("/user/login/?error=denied")

    code = request.GET.get("code")
    state = request.GET.get("state")
    expected_state = request.session.pop("oauth_state", None)
    if not code or not state or state != expected_state:
        return redirect("/user/login/?error=state")

    try:
        token_params = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": config["client_id"],
            "redirect_uri": config["redirect_uri"],
        }
        # 카카오는 client_secret 미사용 앱에 빈 값을 보내면 거부하므로 있을 때만 넣는다.
        if config["client_secret"]:
            token_params["client_secret"] = config["client_secret"]
        # 네이버는 토큰 교환 단계에서도 state 를 요구한다.
        if provider == "naver":
            token_params["state"] = state
        token_data = _post_form(config["token_url"], token_params)
        access_token = token_data.get("access_token")
        if not access_token:
            return redirect("/user/login/?error=token")
        profile = _get_json(config["userinfo_url"], access_token)
    except (urllib.error.URLError, TimeoutError, ValueError):
        return redirect("/user/login/?error=provider")

    provider_id, email, nickname = _extract_profile(provider, profile)
    if not provider_id:
        return redirect("/user/login/?error=profile")

    user = _find_or_create_social_user(provider, provider_id, email, nickname)
    # 비활성/삭제 계정은 로그인 차단(소셜 경로엔 authenticate 검사가 없으므로 직접 확인).
    if user.status != "active" or user.deleted_at is not None:
        return redirect("/user/login/?error=inactive")
    login(request, user, backend="user.backends.UserAccountsBackend")

    next_url = request.session.pop("oauth_next", "") or settings.LOGIN_REDIRECT_URL
    return redirect(next_url)
