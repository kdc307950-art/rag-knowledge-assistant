"""Per-request telemetry shared by async handlers and worker threads.

The request context stores a mutable telemetry object rather than replacing the
context variable from worker threads. ``run_in_threadpool`` copies context
variables into its worker, so mutations to this object remain visible to the
event-loop middleware when a streaming response finishes.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import threading
import time
from typing import Iterator


@dataclass
class RequestTelemetry:
    """Bounded request facts used by metrics and the future access logger."""

    request_id: str
    method: str
    route: str = "/<unmatched>"
    started_at: float = field(default_factory=time.perf_counter)
    status: int | None = None
    sse_terminal: str | None = None
    sse_first_token_at: float | None = None
    error_code: str | None = None
    stages: dict[str, float] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def set_stage(self, name: str, seconds: float) -> None:
        if not name or not isinstance(seconds, (int, float)):
            return
        with self._lock:
            self.stages[str(name)] = max(0.0, float(seconds))

    def mark_token(self, now: float | None = None) -> None:
        with self._lock:
            if self.sse_first_token_at is None:
                self.sse_first_token_at = now if now is not None else time.perf_counter()

    def mark_terminal(self, terminal: str, error_code: str | None = None) -> None:
        normalized = str(terminal or "").strip().lower()
        if normalized in {"done", "ok", "success"}:
            normalized = "ok"
        elif normalized in {"error", "failed", "failure"}:
            normalized = "error"
        elif normalized in {"disconnected", "aborted", "interrupted", "cancelled", "canceled"}:
            normalized = "interrupted"
        else:
            normalized = "unknown"
        with self._lock:
            self.sse_terminal = normalized
            if error_code:
                self.error_code = str(error_code)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "request_id": self.request_id,
                "method": self.method,
                "route": self.route,
                "status": self.status,
                "sse_terminal": self.sse_terminal,
                "sse_first_token_at": self.sse_first_token_at,
                "error_code": self.error_code,
                "stages": dict(self.stages),
            }


_CURRENT: ContextVar[RequestTelemetry | None] = ContextVar(
    "rag_request_telemetry", default=None
)


def get_request_telemetry() -> RequestTelemetry | None:
    return _CURRENT.get()


def bind_request_telemetry(telemetry: RequestTelemetry):
    """Bind a telemetry object and return the context token for reset."""

    return _CURRENT.set(telemetry)


def reset_request_telemetry(token) -> None:
    _CURRENT.reset(token)


def mark_sse_token() -> None:
    telemetry = get_request_telemetry()
    if telemetry is not None:
        telemetry.mark_token()


def mark_sse_terminal(terminal: str, error_code: str | None = None) -> None:
    telemetry = get_request_telemetry()
    if telemetry is not None:
        telemetry.mark_terminal(terminal, error_code)


def record_stage(name: str, seconds: float) -> None:
    telemetry = get_request_telemetry()
    if telemetry is not None:
        telemetry.set_stage(name, seconds)


@contextmanager
def timed_stage(name: str) -> Iterator[None]:
    """Record a stage duration when a request telemetry object is present."""

    started = time.perf_counter()
    try:
        yield
    finally:
        record_stage(name, time.perf_counter() - started)
