"""RAG 优先会话调度，内置通用办公兜底分支."""

from __future__ import annotations

import re
from typing import Iterator

from ..agent.memory import build_history
from ..core.state import get_state
from ..llm.client import needs_history_rewrite
from ..llm.schema import AIResponse
from .entity_service import EntityConversationService
from .rag_service import RagService

_GREETING_PATTERNS = [
    r"^(?:\u4f60\u597d|\u60a8\u597d|hi|hello|hey|\u5728\u5417|\u65e9\u4e0a\u597d|\u4e0b\u5348\u597d|\u665a\u4e0a\u597d)[!！。,.\s]*$",
    r"^(?:\u8c22\u8c22|\u611f\u8c22|\u8f9b\u82e6\u4e86|\u597d\u7684|\u77e5\u9053\u4e86|\u660e\u767d\u4e86)[!！。,.\s]*$",
    r"^(?:\u4f60\u662f\u8c01|\u4f60\u80fd\u505a\u4ec0\u4e48|\u4ecb\u7ecd\u4e00\u4e0b\u81ea\u5df1|\u4f60\u6709\u4ec0\u4e48\u529f\u80fd)[?？!！。,.\s]*$",
]
_GREETING_REPLY = (
    "\u60a8\u597d\uff01\u6211\u662f\u4f01\u4e1a\u77e5\u8bc6\u5e93\u52a9\u624b\u3002"
    "\u60a8\u53ef\u4ee5\u76f4\u63a5\u63d0\u95ee\u516c\u53f8\u5236\u5ea6\u3001\u6d41\u7a0b\u6216\u4e1a\u52a1\u76f8\u5173\u95ee\u9898\u3002"
)


class ChatService:
    """编排问候白名单、历史追问判断和 RAG-First 三类流式入口。"""
    def __init__(self):
        self.rag_service = RagService()
        # 保留实体服务以兼容现有数据模型；它不再参与是否绕过检索的路由决策。
        self.entity_service = EntityConversationService()
        self._last_meta: dict = {}

    @staticmethod
    def _history_for(query: str, messages: list) -> str:
        """仅为明确依赖上文的追问返回最近历史，独立问题返回空字符串。"""
        # 历史仅用于解析“它、继续、详细说说”等依赖上下文的追问，
        # 独立问题必须保持原样，避免被上一轮主题污染。
        recent_history = build_history(
            messages[:-1],
            max_entries=3,
            max_length_per_msg=300,
        ) or ""
        return recent_history if needs_history_rewrite(recent_history, query) else ""

    @staticmethod
    def _is_greeting(query: str) -> bool:
        """使用确定性白名单识别可跳过检索的纯问候。"""
        normalized = (query or "").strip()
        return any(re.match(pattern, normalized, re.IGNORECASE) for pattern in _GREETING_PATTERNS)

    def chat(self, query: str) -> AIResponse:
        if self._is_greeting(query):
            return AIResponse(content=_GREETING_REPLY, sources=[], thought=None)
        messages = get_state("messages", [])
        return self.rag_service.answer(query, self._history_for(query, messages), messages)

    def chat_stream(self, query: str) -> Iterator[str]:
        """问候静态回复；其余输入无条件进入知识库检索流程。"""
        if self._is_greeting(query):
            self._last_meta = {"content": _GREETING_REPLY, "sources": [], "thought": None}
            yield _GREETING_REPLY
            return

        # 除白名单问候外，所有问题都进入 RAG；检索失败由 RagService 严格拒答。
        messages = get_state("messages", [])
        history = self._history_for(query, messages)
        try:
            yield from self.rag_service.answer_stream(query, history, messages)
        finally:
            # 即使调用方提前停止流，也同步最新元数据，避免 UI 读取上一轮结果。
            self._last_meta = getattr(self.rag_service, "_last_meta", {})

    def general_stream(self, query: str) -> Iterator[str]:
        """透传用户确认后的通用办公流，并同步本轮元数据。"""
        try:
            yield from self.rag_service.general_stream(query)
        finally:
            self._last_meta = getattr(self.rag_service, "_last_meta", {})

    def draft_stream(self, query: str, retrieval_query: str) -> Iterator[str]:
        """透传基于资料的起草流，并同步本轮元数据。"""
        try:
            yield from self.rag_service.draft_stream(query, retrieval_query)
        finally:
            self._last_meta = getattr(self.rag_service, "_last_meta", {})
