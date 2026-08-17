from contextlib import nullcontext

import pytest


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ({"sources": ["员工手册.pdf | 第 3 页"]}, True),
        ({"sources": []}, False),
        ({"sources": ["员工手册.pdf"], "is_reject": True}, False),
        ({"sources": ["员工手册.pdf"], "is_general": True}, False),
    ],
)
def test_source_panel_only_shows_for_grounded_answers(message, expected):
    """来源面板只能对应实际的知识库回答，拒答和通用回答均不展示。"""
    from enterprise_rag.ui.chat_ui import _can_render_sources

    assert _can_render_sources(message) is expected


class FakeStatus:
    def __init__(self):
        self.updates = []

    def update(self, **kwargs):
        self.updates.append(kwargs)


def test_native_stream_renderer_keeps_text_contract_and_completes(monkeypatch):
    from enterprise_rag.ui import chat_ui

    status = FakeStatus()
    captured = {}
    monkeypatch.setattr(chat_ui.st, "status", lambda *args, **kwargs: captured.update({"status": (args, kwargs)}) or status)
    monkeypatch.setattr(
        chat_ui.st,
        "write_stream",
        lambda stream, **kwargs: captured.update({"chunks": list(stream), "cursor": kwargs.get("cursor")}) or "正文",
    )

    content, received_text = chat_ui._render_with_write_stream(
        iter(["正", "文", {"ignored": True}, ""]),
        status_text="正在处理...",
    )

    assert content == "正文"
    assert received_text is True
    assert captured["chunks"] == ["正", "文"]
    assert captured["cursor"] == "▌"
    assert captured["status"][1]["type"] == "compact"
    assert status.updates[-1]["state"] == "complete"


def test_native_stream_renderer_marks_error_when_stream_raises(monkeypatch):
    from enterprise_rag.ui import chat_ui

    status = FakeStatus()
    monkeypatch.setattr(chat_ui.st, "status", lambda *_args, **_kwargs: status)
    monkeypatch.setattr(
        chat_ui.st,
        "write_stream",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError, match="boom"):
        chat_ui._render_with_write_stream(iter(["正文"]), status_text="正在处理...")

    assert status.updates[-1]["state"] == "error"


def test_native_stream_renderer_reports_empty_stream(monkeypatch):
    """空流应明确返回未收到文本，供调用方展示兜底内容。"""
    from enterprise_rag.ui import chat_ui

    status = FakeStatus()
    monkeypatch.setattr(chat_ui.st, "status", lambda *_args, **_kwargs: status)
    monkeypatch.setattr(chat_ui.st, "write_stream", lambda stream, **_kwargs: "".join(stream))

    content, received_text = chat_ui._render_with_write_stream(
        iter(()),
        status_text="正在处理...",
    )

    assert content == ""
    assert received_text is False
    assert status.updates[-1]["state"] == "complete"
    assert "无内容" in status.updates[-1]["label"]


def test_native_stream_renderer_uses_service_metadata_for_failure_state(monkeypatch):
    """服务层已降级为错误正文时，状态条不能误报“回答完成”。"""
    from enterprise_rag.ui import chat_ui

    status = FakeStatus()
    monkeypatch.setattr(chat_ui.st, "status", lambda *_args, **_kwargs: status)
    monkeypatch.setattr(chat_ui.st, "write_stream", lambda stream, **_kwargs: "".join(stream))

    content, received_text = chat_ui._render_with_write_stream(
        iter(["暂时不可用"]),
        status_text="正在处理...",
        meta_provider=lambda: {"is_interrupted": True},
    )

    assert content == "暂时不可用"
    assert received_text is True
    assert status.updates[-1]["state"] == "error"
    assert "可重试" in status.updates[-1]["label"]


@pytest.mark.parametrize(
    ("action_type", "expected_event", "expected_flag"),
    [
        ("general", "general-notice", "is_general"),
        ("draft", "draft-caption", "is_draft"),
    ],
)
def test_pending_action_renders_context_before_stream_without_polluting_content(
    monkeypatch,
    action_type,
    expected_event,
    expected_flag,
):
    """G2 警示或起草说明应先于正文显示，保存的消息正文仍是纯模型输出。"""
    import streamlit as st

    from enterprise_rag.ui import chat_ui

    events = []
    saved_messages = []

    class FakeService:
        _last_meta = {
            "content": "纯模型输出",
            "sources": ["制度.pdf | 第 1 页"] if action_type == "draft" else [],
        }

        @staticmethod
        def general_stream(_query):
            return iter(["纯模型输出"])

        @staticmethod
        def draft_stream(_query, _retrieval_query):
            return iter(["纯模型输出"])

    st.session_state.clear()
    monkeypatch.setattr(chat_ui, "ChatService", FakeService)
    monkeypatch.setattr(chat_ui.st, "chat_message", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(
        chat_ui.st,
        "markdown",
        lambda body, **_kwargs: events.append(
            "general-notice" if "general-notice" in body else "markdown"
        ),
    )
    monkeypatch.setattr(
        chat_ui.st,
        "caption",
        lambda body, **_kwargs: events.append(
            "draft-caption" if body == chat_ui.DRAFT_CAPTION else "caption"
        ),
    )

    def fake_render(stream, **_kwargs):
        events.append("stream")
        return "".join(stream), True

    monkeypatch.setattr(chat_ui, "_render_with_write_stream", fake_render)
    monkeypatch.setattr(chat_ui, "_append_message", saved_messages.append)
    consumed = []
    monkeypatch.setattr(chat_ui, "_set_action_used", lambda request_id, field: consumed.append((request_id, field)))
    monkeypatch.setattr(chat_ui.st, "rerun", lambda: None)

    chat_ui._run_pending_action(
        {
            "action": action_type,
            "query": "写一封请假邮件",
            "retrieval_query": "请假制度",
            "request_id": "request-1",
            "used_field": "fallback_used" if action_type == "general" else "draft_used",
        }
    )

    assert events.index(expected_event) < events.index("stream")
    assert saved_messages == [
        {
            "role": "assistant",
            "content": "纯模型输出",
            "thought": None,
            "sources": ["制度.pdf | 第 1 页"] if action_type == "draft" else [],
            "is_general": action_type == "general",
            "is_draft": action_type == "draft",
            "is_interrupted": False,
        }
    ]
    assert saved_messages[0][expected_flag] is True
    assert consumed == [
        (
            "request-1",
            "fallback_used" if action_type == "general" else "draft_used",
        )
    ]


def test_failed_general_action_keeps_retry_button_available(monkeypatch):
    """G2 调用失败时不得提前消费原拒答消息，用户应能再次点击。"""
    import streamlit as st

    from enterprise_rag.ui import chat_ui

    saved_messages = []
    consumed = []

    class FakeService:
        _last_meta = {
            "content": "通用办公模型鉴权失败，请检查 DashScope API Key 配置后重试。",
            "sources": [],
            "is_general": True,
            "is_interrupted": True,
            "action_failed": True,
        }

        def general_stream(self, _query):
            yield self._last_meta["content"]

    st.session_state.clear()
    monkeypatch.setattr(chat_ui, "ChatService", FakeService)
    monkeypatch.setattr(chat_ui.st, "chat_message", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(chat_ui, "_render_answer_context", lambda **_kwargs: None)
    monkeypatch.setattr(
        chat_ui,
        "_render_with_write_stream",
        lambda stream, **_kwargs: ("".join(stream), True),
    )
    monkeypatch.setattr(chat_ui, "_append_message", saved_messages.append)
    monkeypatch.setattr(chat_ui, "_set_action_used", lambda *args: consumed.append(args))
    monkeypatch.setattr(chat_ui.st, "rerun", lambda: None)

    chat_ui._run_pending_action(
        {
            "action": "general",
            "query": "写一封请假邮件",
            "retrieval_query": "写一封请假邮件",
            "request_id": "request-2",
            "used_field": "fallback_used",
        }
    )

    assert consumed == []
    assert saved_messages[0]["is_interrupted"] is True


def test_mark_action_only_registers_pending_work(monkeypatch):
    """点击按钮只登记待执行动作，不得提前修改 fallback_used。"""
    import streamlit as st

    from enterprise_rag.ui import chat_ui

    original = {
        "role": "assistant",
        "request_id": "request-3",
        "query": "写一封邮件",
        "retrieval_query": "写一封邮件",
        "fallback_used": False,
    }
    st.session_state.clear()
    st.session_state.messages = [original]
    monkeypatch.setattr(chat_ui.st, "rerun", lambda: None)

    chat_ui._mark_action("request-3", "fallback_used", "general", original)

    assert st.session_state.messages[0]["fallback_used"] is False
    assert st.session_state.generating is True
    assert st.session_state.pending_chat_action["request_id"] == "request-3"
    assert st.session_state.pending_chat_action["used_field"] == "fallback_used"


def test_origin_action_is_hidden_while_pending():
    """同一显式动作执行期间应进入忙碌态，避免用户重复提交。"""
    import streamlit as st

    from enterprise_rag.ui import chat_ui

    st.session_state.clear()
    st.session_state.pending_chat_action = {
        "request_id": "request-4",
        "action": "general",
    }

    assert chat_ui._is_action_pending("request-4", "general") is True
    assert chat_ui._is_action_busy("request-4", "general") is True
    assert chat_ui._is_action_pending("request-4", "draft") is False
    assert chat_ui._is_action_pending("another-request", "general") is False

    st.session_state.pop("pending_chat_action")
    st.session_state.generating = True
    assert chat_ui._is_action_busy("another-request", "general") is True


def test_cleaned_output_cache_is_bounded():
    """清洗缓存必须是有界 LRU：填充超过上限的条目后 currsize 不超 maxsize。"""
    from enterprise_rag.utils import cleaner

    # lru_cache 的 maxsize 在装饰时固定，模块常量仅作文档；直接验证淘汰行为。
    maxsize = cleaner._clean_cached.cache_info().maxsize
    assert maxsize > 0
    for index in range(maxsize + 5):
        cleaner.clean_llm_output(f"answer-{index} unique-{index}")
    info = cleaner._clean_cached.cache_info()
    assert info.currsize <= maxsize
    cleaner._clean_cached.cache_clear()
