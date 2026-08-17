# Session 状态管理：SessionStore 协议 + Streamlit/进程内 dict 双实现
import copy
import threading
from typing import Protocol

import streamlit as st

from .constants import MAX_MESSAGES

# 所有会话级状态集中初始化，避免组件首次访问时出现缺失键异常。
_STATE_DEFAULTS = {
    "messages": [],
    "authenticated": False,
    "kb_version": 0,
    "answer_cache": {},
    "chat_started": False,
    "system_status": {
        "doc_count": 0,
        "chunk_count": 0,
        "vector_status": "就绪",
        "model_status": "检查中"
    },
    "generating": False,
    "debug_mode": False,
    "cleaned_cache": {},
    "upload_tasks": {},
    "entity_coverage": {},
    "active_entity_id": None,
}


class SessionStore(Protocol):
    """会话存储最小接口：初始化、安全读写与清空对话。"""

    def init_state(self) -> None: ...

    def get_state(self, key: str, default=None): ...

    def set_state(self, key: str, value) -> None: ...

    def clear_chat(self) -> None: ...


class StreamlitSessionStore:
    """包装 st.session_state 的实现；行为与历史版本完全一致。"""

    def init_state(self) -> None:
        """初始化所有 session 状态变量。"""
        for key, value in _STATE_DEFAULTS.items():
            if key not in st.session_state:
                st.session_state[key] = value

    def get_state(self, key: str, default=None):
        """安全获取状态。"""
        return st.session_state.get(key, default)

    def set_state(self, key: str, value) -> None:
        """设置状态。"""
        st.session_state[key] = value

    def clear_chat(self) -> None:
        """清空聊天记录和缓存。"""
        # 实体知识块的展示进度属于会话上下文，清空对话时必须同步重置。
        st.session_state.messages.clear()
        st.session_state.cleaned_cache.clear()
        st.session_state.answer_cache.clear()
        st.session_state.entity_coverage.clear()
        st.session_state.active_entity_id = None
        st.session_state.chat_started = False


class DictSessionStore:
    """进程内 dict[session_id] 会话存储，线程安全，供 API 层使用。"""

    def __init__(self):
        self._sessions: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._current_session = "default"

    def set_session_id(self, session_id: str) -> None:
        """切换当前会话；后续所有读写都作用于该会话。"""
        with self._lock:
            self._current_session = session_id

    def _ensure_session_locked(self) -> dict:
        """在锁内返回当前会话字典，不存在则按默认值创建。"""
        session = self._sessions.get(self._current_session)
        if session is None:
            session = copy.deepcopy(_STATE_DEFAULTS)
            self._sessions[self._current_session] = session
        return session

    def init_state(self) -> None:
        with self._lock:
            session = self._sessions.get(self._current_session)
            if session is None:
                self._sessions[self._current_session] = copy.deepcopy(_STATE_DEFAULTS)
                return
            for key, value in _STATE_DEFAULTS.items():
                if key not in session:
                    session[key] = value

    def get_state(self, key: str, default=None):
        with self._lock:
            session = self._ensure_session_locked()
            return session.get(key, default)

    def set_state(self, key: str, value) -> None:
        with self._lock:
            session = self._ensure_session_locked()
            session[key] = value

    def clear_chat(self) -> None:
        with self._lock:
            session = self._ensure_session_locked()
            session["messages"] = []
            session["cleaned_cache"] = {}
            session["answer_cache"] = {}
            session["entity_coverage"] = {}
            session["active_entity_id"] = None
            session["chat_started"] = False


# 默认指向 Streamlit 实现，保证现有 UI 与 services 调用方零改动。
_store: SessionStore = StreamlitSessionStore()


def set_session_store(store: SessionStore) -> None:
    """切换全局会话存储实现（如 API 层注入 DictSessionStore）。"""
    global _store
    _store = store


def init_state():
    """初始化所有 session 状态变量。"""
    _store.init_state()


def get_state(key, default=None):
    """安全获取状态。"""
    return _store.get_state(key, default)


def set_state(key, value):
    """设置状态。"""
    _store.set_state(key, value)


def clear_chat():
    """清空聊天记录和缓存。"""
    _store.clear_chat()
