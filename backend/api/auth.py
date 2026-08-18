"""Optional local-user authentication endpoints."""

from __future__ import annotations

from collections import defaultdict, deque
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from enterprise_rag.config import APP_PASSWORD, AUTH_MODE, AUTH_SECRET
from enterprise_rag.utils.logger import log_audit_event

from ..auth import get_token_manager, get_user_store, require_access

router = APIRouter()
_LOGIN_FAILURE_LIMIT = 5
_LOGIN_FAILURE_WINDOW_SECONDS = 60.0
_LOGIN_FAILURE_LOCK = threading.Lock()
_LOGIN_FAILURES: dict[str, deque[float]] = defaultdict(deque)


class LoginRequest(BaseModel):
    username: str
    password: str


def _ensure_users_mode() -> None:
    if AUTH_MODE != "users":
        raise HTTPException(status_code=404, detail="用户登录未启用")
    if not (AUTH_SECRET or APP_PASSWORD):
        raise HTTPException(status_code=503, detail="用户鉴权未配置 AUTH_SECRET")


def _login_key(request: Request, username: str) -> str:
    host = request.client.host if request.client else "unknown"
    return f"{host}:{username.strip().lower()[:128]}"


def _login_rate_limited(request: Request, username: str, *, now: float | None = None) -> bool:
    current = time.monotonic() if now is None else float(now)
    key = _login_key(request, username)
    with _LOGIN_FAILURE_LOCK:
        failures = _LOGIN_FAILURES[key]
        while failures and current - failures[0] >= _LOGIN_FAILURE_WINDOW_SECONDS:
            failures.popleft()
        return len(failures) >= _LOGIN_FAILURE_LIMIT


def _record_login_failure(request: Request, username: str, *, now: float | None = None) -> None:
    current = time.monotonic() if now is None else float(now)
    key = _login_key(request, username)
    with _LOGIN_FAILURE_LOCK:
        failures = _LOGIN_FAILURES[key]
        while failures and current - failures[0] >= _LOGIN_FAILURE_WINDOW_SECONDS:
            failures.popleft()
        failures.append(current)
        if len(_LOGIN_FAILURES) > 512:
            for old_key, old_failures in list(_LOGIN_FAILURES.items()):
                if not old_failures or current - old_failures[-1] >= _LOGIN_FAILURE_WINDOW_SECONDS:
                    _LOGIN_FAILURES.pop(old_key, None)


@router.post("/auth/login")
async def login(request: Request, payload: LoginRequest):
    _ensure_users_mode()
    if _login_rate_limited(request, payload.username):
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请稍后重试")
    user = get_user_store().authenticate(payload.username, payload.password)
    if not user:
        _record_login_failure(request, payload.username)
        log_audit_event("auth_login_failure", username=payload.username[:64])
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = get_token_manager().issue(user)
    log_audit_event("auth_login_success", user_id=user["id"], username=user["username"])
    return {"token": token, "expires_in": get_token_manager().ttl_seconds, "user": user}


@router.get("/auth/me")
async def me(request: Request, _=Depends(require_access)):
    _ensure_users_mode()
    return {"user": getattr(request.state, "current_user", None)}


@router.post("/auth/logout")
async def logout(request: Request, _=Depends(require_access)):
    _ensure_users_mode()
    claims = getattr(request.state, "auth_claims", {}) or {}
    if claims.get("jti"):
        get_user_store().revoke_token(str(claims["jti"]), float(claims.get("exp", 0)))
    current_user = getattr(request.state, "current_user", {}) or {}
    log_audit_event("auth_logout", user_id=current_user.get("id"))
    return {"ok": True}
