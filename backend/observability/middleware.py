"""Pure ASGI middleware for request IDs and real streaming metrics."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from enterprise_rag.utils.logger import log_access_event

from .context import (
    RequestTelemetry,
    bind_request_telemetry,
    reset_request_telemetry,
)
from .metrics import record_error, registry


_SAFE_EXACT_ROUTES = {
    "/api/chat",
    "/api/chat/general",
    "/api/chat/draft",
    "/api/upload",
    "/api/kb/documents",
    "/api/kb/clear",
    "/api/kb/stats",
    "/api/health",
    "/api/diagnostics",
    "/metrics",
    "/openapi.json",
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
}
_SAFE_TEMPLATE_FALLBACKS = (
    (re.compile(r"^/api/tasks/[^/]+$"), "/api/tasks/{task_id}"),
    (re.compile(r"^/api/kb/documents/.+$"), "/api/kb/documents/{source}"),
)


def route_label(scope: Mapping[str, Any]) -> str:
    """Return a bounded route template, never a raw user-controlled path."""

    route = scope.get("route")
    template = getattr(route, "path", None)
    if isinstance(template, str) and template.startswith("/") and len(template) <= 120:
        path = scope.get("path") or ""
        if path.startswith("/api/") and not template.startswith("/api/"):
            template = "/api" + template
        return template

    path = scope.get("path") or ""
    if path in _SAFE_EXACT_ROUTES:
        return path
    for pattern, replacement in _SAFE_TEMPLATE_FALLBACKS:
        if pattern.match(path):
            return replacement
    # Keep health/probe route names visible even when a test ASGI app does not
    # populate scope["route"]. All other unresolved paths collapse together.
    if path in {"/api/live", "/api/ready", "/api/health"}:
        return path
    return "/<unmatched>"


def _with_request_id(message: dict, request_id: str) -> dict:
    # The identifier is an application-owned correlation value. Remove any
    # client/downstream value before adding the generated one so callers cannot
    # spoof log correlation or poison an upstream trace.
    headers = [
        (name, value)
        for name, value in list(message.get("headers") or [])
        if name.lower() != b"x-request-id"
    ]
    headers.append((b"x-request-id", request_id.encode("ascii")))
    return {**message, "headers": headers}


def _phase_durations_ms(stages: dict[str, float], total_seconds: float) -> dict[str, float]:
    phases = {
        f"{name}_ms": round(float(seconds) * 1000, 3)
        for name, seconds in stages.items()
    }
    phases["total_ms"] = round(max(0.0, float(total_seconds)) * 1000, 3)
    return phases


def _sse_events(message: dict, buffer: str) -> tuple[list[tuple[str, str | None]], str]:
    body = message.get("body") or b""
    if not body:
        return [], buffer
    if isinstance(body, bytes):
        text = body.decode("utf-8", errors="replace")
    else:
        text = str(body)
    buffer = (buffer + text).replace("\r\n", "\n")
    events: list[tuple[str, str | None]] = []
    while "\n\n" in buffer:
        frame, buffer = buffer.split("\n\n", 1)
        event_name = None
        data = None
        for line in frame.splitlines():
            if line.startswith("event:"):
                event_name = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data = line.removeprefix("data:").strip()
        if event_name:
            events.append((event_name, data))
    return events, buffer


class ObservabilityMiddleware:
    """Request ID + metrics middleware that waits for full SSE body completion."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        request_id = uuid4().hex
        telemetry = RequestTelemetry(
            request_id=request_id,
            method=str(scope.get("method") or "GET").upper(),
        )
        token = bind_request_telemetry(telemetry)
        state = scope.setdefault("state", {})
        state["observability"] = telemetry

        request_started = time.perf_counter()
        status: int | None = None
        is_sse = False
        active_registered = False
        sse_buffer = ""
        request_failed = False

        async def send_wrapper(message):
            nonlocal status, is_sse, active_registered, sse_buffer
            message_type = message.get("type")
            if message_type == "http.response.start":
                status = int(message.get("status", 500))
                telemetry.status = status
                raw_headers = list(message.get("headers") or [])
                content_type = next(
                    (value for name, value in raw_headers if name.lower() == b"content-type"),
                    b"",
                )
                is_sse = content_type.lower().startswith(b"text/event-stream")
                telemetry.route = route_label(scope)
                if is_sse:
                    with registry._lock:
                        registry.metrics.sse_active.inc(1, telemetry.route)
                    active_registered = True
                await send(_with_request_id(message, request_id))
                return

            if message_type == "http.response.body":
                if is_sse:
                    events, sse_buffer = _sse_events(message, sse_buffer)
                    for event, data in events:
                        if event == "token":
                            telemetry.mark_token()
                        elif event == "done":
                            telemetry.mark_terminal("ok")
                        elif event == "error":
                            error_code = None
                            if data:
                                try:
                                    import json

                                    payload = json.loads(data)
                                    error_code = payload.get("code") if isinstance(payload, dict) else None
                                except (TypeError, ValueError):
                                    error_code = None
                            telemetry.mark_terminal("error", error_code)
                await send(message)
                return

            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except asyncio.CancelledError:
            # Client disconnects and shutdown cancellation are not application
            # failures. Keep them out of the unhandled-error alert signal.
            telemetry.mark_terminal("interrupted")
            if status is None:
                status = 499
                telemetry.status = status
            raise
        except Exception:
            request_failed = True
            if status is None:
                status = 500
                telemetry.status = status
            elif is_sse:
                # The response headers may already be committed. We cannot
                # change the wire status, but we can still classify the stream
                # as an application error for metrics and access SLOs.
                telemetry.mark_terminal("error", "unhandled")
            if not is_sse:
                record_error("unhandled")
            raise
        finally:
            duration = max(0.0, time.perf_counter() - request_started)
            telemetry.route = telemetry.route or route_label(scope)
            telemetry.status = int(status or 500)
            if is_sse:
                if telemetry.sse_terminal is None:
                    telemetry.mark_terminal("interrupted")
                snapshot = telemetry.snapshot()
                first_token = None
                if snapshot["sse_first_token_at"] is not None:
                    first_token = max(0.0, snapshot["sse_first_token_at"] - telemetry.started_at)
                registry.observe_sse(
                    telemetry.route,
                    snapshot["sse_terminal"] or "unknown",
                    duration,
                    first_token,
                )
                if snapshot["sse_terminal"] == "error":
                    record_error(snapshot.get("error_code") or "generation_error")
                if active_registered:
                    with registry._lock:
                        registry.metrics.sse_active.dec(1, telemetry.route)
            route_type = "sse" if is_sse else "request"
            outcome = (
                "interrupted"
                if telemetry.sse_terminal == "interrupted"
                else "error"
                if request_failed
                else telemetry.sse_terminal
                # sse_terminal 可能为 None（流结束时没标终态）。不加这个判断的话
                # outcome 会是 None，渲染成指标标签就变成字面量 "None"。
                if is_sse and telemetry.sse_terminal is not None
                else "error"
                if telemetry.status >= 400
                else "ok"
            )
            # /metrics is an observability endpoint, not application traffic;
            # excluding it prevents a scraper/health task from distorting SLOs.
            if telemetry.route != "/metrics":
                registry.observe_request(
                    telemetry.route,
                    telemetry.method,
                    telemetry.status,
                    duration,
                    route_type=route_type,
                    outcome=outcome,
                )
            snapshot = telemetry.snapshot()
            phases = _phase_durations_ms(snapshot.get("stages", {}), duration)
            log_access_event(
                request_id=telemetry.request_id,
                route=telemetry.route,
                method=telemetry.method,
                status_code=telemetry.status,
                duration_ms=round(duration * 1000, 3),
                route_type=route_type,
                outcome=outcome,
                error_code=snapshot.get("error_code"),
                sse_terminal=snapshot.get("sse_terminal") if is_sse else None,
                phases=phases,
            )
            reset_request_telemetry(token)
