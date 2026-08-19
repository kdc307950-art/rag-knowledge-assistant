"""Deployment-mode-aware access control for business API routes."""
from __future__ import annotations

import hmac
import ipaddress
import threading
import time
from urllib.parse import urlsplit

from fastapi import Header, HTTPException, Request

from enterprise_rag.auth.users import TokenManager, UserStore
from enterprise_rag.config import (
    APP_PASSWORD,
    AUTH_DB_PATH,
    AUTH_COOKIE_NAME,
    AUTH_MODE,
    AUTH_SECRET,
    AUTH_TOKEN_TTL_SECONDS,
    DEPLOYMENT_MODE,
)
from enterprise_rag import config as rag_config
from enterprise_rag.utils.logger import log_audit_event

from .observability.context import get_request_telemetry
from .observability.middleware import route_label
from .observability.metrics import mark_auth_failure


AUTH_FAILURE_DEDUPE_SECONDS = 60.0
_AUTH_FAILURE_LOCK = threading.Lock()
_AUTH_FAILURE_LAST_LOGGED: dict[tuple[str, str], float] = {}
_USER_STORE: UserStore | None = None
_TOKEN_MANAGER: TokenManager | None = None


def get_user_store() -> UserStore:
    global _USER_STORE
    if _USER_STORE is None:
        _USER_STORE = UserStore(AUTH_DB_PATH)
    return _USER_STORE


def get_token_manager() -> TokenManager:
    global _TOKEN_MANAGER
    if _TOKEN_MANAGER is None:
        secret = AUTH_SECRET
        if not secret:
            raise RuntimeError("AUTH_SECRET 未配置，不能启用 users 鉴权")
        _TOKEN_MANAGER = TokenManager(
            secret=secret,
            ttl_seconds=AUTH_TOKEN_TTL_SECONDS,
        )
    return _TOKEN_MANAGER


def _set_auth_state(
    request: Request,
    *,
    user: dict,
    token: str | None = None,
    claims: dict | None = None,
) -> None:
    if DEPLOYMENT_MODE not in {"dev", "single_user", "multi_user"}:
        raise HTTPException(status_code=503, detail="部署鉴权模式无效")
    request.state.current_user = user
    request.state.auth_token = token
    request.state.auth_claims = claims or {}


def _route_label(request: Request) -> str:
    return route_label(request.scope)


def _client_scope(request: Request) -> str:
    host = request.client.host if request.client else ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return "unknown"
    if address.is_loopback:
        return "local"
    if address.is_private:
        return "lan"
    return "unknown"


def _cookie_origin_is_allowed(request: Request) -> bool:
    """Require a same-origin browser request before using a production cookie.

    SameSite=Lax handles most cross-site requests, but it is not a complete
    CSRF boundary for same-site subdomains. Bearer-authenticated automation is
    deliberately exempt because it never relies on the browser cookie.
    """
    if rag_config.RAG_ENVIRONMENT != "production" or request.method.upper() in {
        "GET",
        "HEAD",
        "OPTIONS",
    }:
        return True
    origin = str(request.headers.get("origin") or "").strip().rstrip("/")
    parsed = urlsplit(rag_config.PUBLIC_BASE_URL)
    expected = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    return bool(origin and expected and hmac.compare_digest(origin, expected))


def _should_audit_auth_failure(
    client_scope: str,
    route: str,
    *,
    now: float | None = None,
) -> bool:
    """Keep audit volume bounded while counting every failed attempt in metrics."""

    current = time.monotonic() if now is None else float(now)
    key = (str(client_scope), str(route))
    with _AUTH_FAILURE_LOCK:
        previous = _AUTH_FAILURE_LAST_LOGGED.get(key)
        if previous is not None and current - previous < AUTH_FAILURE_DEDUPE_SECONDS:
            return False
        _AUTH_FAILURE_LAST_LOGGED[key] = current
        if len(_AUTH_FAILURE_LAST_LOGGED) > 256:
            cutoff = current - AUTH_FAILURE_DEDUPE_SECONDS
            for old_key, old_time in list(_AUTH_FAILURE_LAST_LOGGED.items()):
                if old_time < cutoff:
                    _AUTH_FAILURE_LAST_LOGGED.pop(old_key, None)
        return True


async def require_access(
    request: Request,
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> None:
    # Direct unit callers may pass FastAPI's Header sentinel; treat it as absent.
    if not isinstance(authorization, str):
        authorization = None
    if not isinstance(x_api_key, str):
        x_api_key = None
    production_error = rag_config.production_security_error()
    if production_error:
        raise HTTPException(status_code=503, detail=production_error)
    if DEPLOYMENT_MODE == "multi_user" and (
        AUTH_MODE != "users" or not AUTH_SECRET.strip()
    ):
        raise HTTPException(status_code=503, detail="多用户鉴权配置未就绪")
    if DEPLOYMENT_MODE == "single_user" and (
        AUTH_MODE != "legacy" or not APP_PASSWORD.strip()
    ):
        raise HTTPException(status_code=503, detail="单用户鉴权配置未就绪")
    if DEPLOYMENT_MODE == "dev":
        _set_auth_state(
            request,
            user={
                "id": "local",
                "username": "local",
                "roles": ["admin"],
                "department": "general",
            },
        )
        return

    if DEPLOYMENT_MODE == "multi_user":
        bearer = authorization[7:].strip() if authorization and authorization.lower().startswith("bearer ") else ""
        cookies = getattr(request, "cookies", {}) or {}
        cookie_token = str(cookies.get(AUTH_COOKIE_NAME, "") or "").strip()
        for credential_type, token in (("bearer", bearer), ("cookie", cookie_token)):
            if not token:
                continue
            try:
                manager = get_token_manager()
                claims = manager.verify(token, store=get_user_store())
                user = get_user_store().get_user(int(claims["sub"]))
            except (RuntimeError, ValueError, KeyError, TypeError):
                user = None
                claims = None
            if user and user.get("active"):
                if credential_type == "cookie" and not _cookie_origin_is_allowed(request):
                    raise HTTPException(status_code=403, detail="Cookie 请求来源不受信任")
                _set_auth_state(request, user=user, token=token, claims=claims)
                return
    if DEPLOYMENT_MODE == "single_user" and x_api_key and hmac.compare_digest(x_api_key, APP_PASSWORD):
        _set_auth_state(
            request,
            user={
                "id": "service-admin",
                "username": "admin",
                "roles": ["admin"],
                "department": "general",
            },
        )
        return

    route = _route_label(request)
    mark_auth_failure(route)
    telemetry = get_request_telemetry()
    client_scope = _client_scope(request)
    if not _should_audit_auth_failure(client_scope, route):
        raise HTTPException(status_code=401, detail="访问凭据错误")
    log_audit_event(
        "auth_failure",
        route=route,
        client_scope=client_scope,
        method=request.method,
        request_id=telemetry.request_id if telemetry else None,
    )
    raise HTTPException(status_code=401, detail="访问凭据错误")
