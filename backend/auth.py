"""APP_PASSWORD 鉴权：配置口令后所有 API 需 X-API-Key 头。"""
from __future__ import annotations

import hmac
import ipaddress
import threading
import time

from fastapi import Header, HTTPException, Request

from enterprise_rag.config import APP_PASSWORD
from enterprise_rag.utils.logger import log_audit_event

from .observability.context import get_request_telemetry
from .observability.middleware import route_label
from .observability.metrics import mark_auth_failure


AUTH_FAILURE_DEDUPE_SECONDS = 60.0
_AUTH_FAILURE_LOCK = threading.Lock()
_AUTH_FAILURE_LAST_LOGGED: dict[tuple[str, str], float] = {}


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
) -> None:
    if not APP_PASSWORD:
        return
    if not x_api_key or not hmac.compare_digest(x_api_key, APP_PASSWORD):
        route = _route_label(request)
        mark_auth_failure(route)
        telemetry = get_request_telemetry()
        client_scope = _client_scope(request)
        if not _should_audit_auth_failure(client_scope, route):
            raise HTTPException(status_code=401, detail="访问口令错误")
        log_audit_event(
            "auth_failure",
            route=route,
            client_scope=client_scope,
            method=request.method,
            request_id=telemetry.request_id if telemetry else None,
        )
        raise HTTPException(status_code=401, detail="访问口令错误")
