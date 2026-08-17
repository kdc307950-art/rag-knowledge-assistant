# 进程内临时状态：保留给未迁移的服务辅助逻辑，不承载 HTTP 会话。
import copy
import threading
from typing import Protocol

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


class DictSessionStore:
    """线程安全的进程内状态存储。

    HTTP 会话历史由 ``backend.session`` 按 ``session_id`` 管理。这里仅保留实体
    服务等遗留辅助逻辑需要的短生命周期状态，避免把 API 请求绑定到可变的全局
    "当前 session"。
    """

    def __init__(self):
        self._state = copy.deepcopy(_STATE_DEFAULTS)
        self._lock = threading.RLock()

    def init_state(self) -> None:
        with self._lock:
            for key, value in _STATE_DEFAULTS.items():
                if key not in self._state:
                    self._state[key] = copy.deepcopy(value)

    def get_state(self, key: str, default=None):
        with self._lock:
            return self._state.get(key, default)

    def set_state(self, key: str, value) -> None:
        with self._lock:
            self._state[key] = value

    def clear_chat(self) -> None:
        with self._lock:
            self._state["messages"] = []
            self._state["cleaned_cache"] = {}
            self._state["answer_cache"] = {}
            self._state["entity_coverage"] = {}
            self._state["active_entity_id"] = None
            self._state["chat_started"] = False


# 默认状态不依赖 UI 框架；API 会话由 backend.session 单独管理。
_store: SessionStore = DictSessionStore()


def set_session_store(store: SessionStore) -> None:
    """替换辅助状态存储，供测试或兼容层注入。"""
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


def reset_state() -> None:
    """重建默认状态；仅用于测试和显式本地重置。"""
    set_session_store(DictSessionStore())
