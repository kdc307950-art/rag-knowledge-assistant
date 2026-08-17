"""API 层会话历史：进程内 dict + 锁（Phase 3 前不上 Redis）。"""
from __future__ import annotations

import threading
import uuid

from enterprise_rag.core.constants import MAX_MESSAGES

_SESSION_HISTORIES: dict[str, list[dict]] = {}
_SESSION_LOCK = threading.Lock()


def get_or_create_session_id(session_id: str | None) -> str:
    return session_id or uuid.uuid4().hex


def get_messages(session_id: str) -> list[dict]:
    with _SESSION_LOCK:
        return list(_SESSION_HISTORIES.get(session_id, []))


def append_message(session_id: str, role: str, content: str) -> None:
    with _SESSION_LOCK:
        messages = _SESSION_HISTORIES.setdefault(session_id, [])
        messages.append({"role": role, "content": content})
        if len(messages) > MAX_MESSAGES:
            del messages[: len(messages) - MAX_MESSAGES]
