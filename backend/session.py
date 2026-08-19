"""API 层会话历史：进程内 dict + 锁（Phase 3 前不上 Redis）。"""
from __future__ import annotations

import threading
import uuid
import time

from enterprise_rag.core.constants import MAX_MESSAGES

_SESSION_HISTORIES: dict[str, list[dict]] = {}
_SESSION_PRINCIPALS: dict[str, str] = {}
_SESSION_TOUCHED: dict[str, float] = {}
_SESSION_LOCK = threading.Lock()
SESSION_TTL_SECONDS = 24 * 3600
SESSION_MAX_COUNT = 1000


class SessionOwnershipError(PermissionError):
    """The supplied session belongs to another principal or is unknown."""


def get_or_create_session_id(session_id: str | None, principal_id: str = "local") -> str:
    principal = str(principal_id or "local")
    now = time.time()
    with _SESSION_LOCK:
        expired = [sid for sid, touched in _SESSION_TOUCHED.items() if now - touched > SESSION_TTL_SECONDS]
        for sid in expired:
            _SESSION_TOUCHED.pop(sid, None)
            _SESSION_PRINCIPALS.pop(sid, None)
            _SESSION_HISTORIES.pop(sid, None)
        if session_id:
            sid = str(session_id)
            owner = _SESSION_PRINCIPALS.get(sid)
            if owner is None or owner != principal:
                raise SessionOwnershipError("session 不存在或不属于当前账号")
            _SESSION_TOUCHED[sid] = now
            return sid
        sid = uuid.uuid4().hex
        _SESSION_PRINCIPALS[sid] = principal
        _SESSION_TOUCHED[sid] = now
        while len(_SESSION_PRINCIPALS) > SESSION_MAX_COUNT:
            oldest = min(_SESSION_TOUCHED, key=_SESSION_TOUCHED.get)
            _SESSION_TOUCHED.pop(oldest, None)
            _SESSION_PRINCIPALS.pop(oldest, None)
            _SESSION_HISTORIES.pop(oldest, None)
        return sid


def get_messages(session_id: str) -> list[dict]:
    with _SESSION_LOCK:
        _SESSION_TOUCHED[session_id] = time.time()
        return list(_SESSION_HISTORIES.get(session_id, []))


def append_message(session_id: str, role: str, content: str) -> None:
    with _SESSION_LOCK:
        messages = _SESSION_HISTORIES.setdefault(session_id, [])
        messages.append({"role": role, "content": content})
        if len(messages) > MAX_MESSAGES:
            del messages[: len(messages) - MAX_MESSAGES]
