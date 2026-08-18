"""Deployment-mode-aware access control for business API routes."""
from __future__ import annotations

import hmac
import ipaddress
import threading
import time

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
        cookie_token = request.cookies.get(AUTH_COOKIE_NAME, "").strip()
        for token in (bearer, cookie_token):
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
                _set_auth_state(request, user=user, token=token, claims=claims)
                return
        if APP_PASSWORD and x_api_key and hmac.compare_digest(x_api_key, APP_PASSWORD):
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
